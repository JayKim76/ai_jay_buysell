"""경보 엔진 (1단계 핵심).

규칙은 기획안 4번 모듈 2·3·4 기준:
- 배송지시 후 24시간 경과했는데 아직 주문 접수 단계 (발주/발주확인 미처리)
- 출고예정일 24시간 이내 임박 / 경과 (미출고) — 노출정지 직결이라 경과는 critical
- 주문 후 5일 경과했는데 국제 출고 전 단계 (미출고 지연)
- 미답변 문의 24시간 경과
- 문의 회신 기한 24시간 이내 임박 / 기한 경과 (쿠팡 회신 기한 72시간 기준)
- 상품 판매 불가 상태 / 부적합 의심 (store_watch 어댑터가 생성)

경보는 dedupe_key로 중복을 막고, 조건이 해소되면 자동 resolved 처리한다.
"""
from .db import SHIPPED_STAGES, db
from .timeutil import hours_between, now, now_iso, parse_iso

# (kind, severity, title)
RULE_META = {
    "INSTRUCT_OVERDUE": ("warning", "배송지시 24시간 경과 — 발주 미처리"),
    "SHIP_DUE_SOON": ("warning", "출고예정일 24시간 이내 임박"),
    "SHIP_OVERDUE": ("critical", "출고예정일 경과 — 미출고 (노출정지 위험)"),
    "STALE_ORDER": ("warning", "주문 5일 경과 — 국제 출고 전 지연"),
    "INQUIRY_UNANSWERED": ("warning", "미답변 문의 24시간 경과"),
    "INQUIRY_DUE_SOON": ("warning", "문의 회신 기한 24시간 이내"),
    "INQUIRY_OVERDUE": ("critical", "문의 회신 기한 경과"),
    "PRODUCT_UNSUITABLE": ("critical", "판매부적합 의심 상품"),
    "PRODUCT_NOT_ON_SALE": ("warning", "판매 불가 상태 상품"),
}


def _upsert_alert(conn, kind, target_type, target_id, detail="", due_at=None):
    severity, title = RULE_META[kind]
    dedupe = f"{kind}:{target_type}:{target_id}"
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO alerts (dedupe_key, kind, severity, target_type, target_id,
                            title, detail, due_at, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
        ON CONFLICT(dedupe_key) DO UPDATE SET
            detail=excluded.detail, due_at=excluded.due_at, updated_at=excluded.updated_at,
            status=CASE WHEN alerts.status='resolved' THEN 'open' ELSE alerts.status END,
            resolved_at=CASE WHEN alerts.status='resolved' THEN NULL ELSE alerts.resolved_at END
        """,
        (dedupe, kind, severity, target_type, str(target_id), title, detail,
         due_at, ts, ts),
    )


def _resolve_stale(conn, kind, active_target_ids):
    """이번 점검에서 더 이상 해당하지 않는 open 경보를 자동 해소한다."""
    rows = conn.execute(
        "SELECT id, target_id FROM alerts WHERE kind=? AND status='open'", (kind,)
    ).fetchall()
    ts = now_iso()
    for r in rows:
        if r["target_id"] not in active_target_ids:
            conn.execute(
                "UPDATE alerts SET status='resolved', resolved_at=?, updated_at=? WHERE id=?",
                (ts, ts, r["id"]),
            )


def evaluate(db_path=None) -> dict:
    """전체 경보 규칙을 평가한다. 생성/유지된 경보 종류별 건수를 반환."""
    counts: dict[str, int] = {}
    with db(db_path) as conn:
        # ── 주문 규칙 ──
        active = {k: set() for k in
                  ("INSTRUCT_OVERDUE", "SHIP_DUE_SOON", "SHIP_OVERDUE", "STALE_ORDER")}
        orders = conn.execute(
            "SELECT * FROM orders WHERE stage NOT IN ('DELIVERED','CANCELLED')"
        ).fetchall()
        for o in orders:
            oid = str(o["id"])
            label = f"주문 {o['channel_order_id']} · {o['product_name'] or ''}"
            age_h = hours_between(o["ordered_at"])
            if o["stage"] == "RECEIVED" and age_h is not None and age_h >= 24:
                active["INSTRUCT_OVERDUE"].add(oid)
                _upsert_alert(conn, "INSTRUCT_OVERDUE", "order", oid,
                              f"{label} — 접수 후 {age_h:.0f}시간 경과, 타오바오 발주가 필요합니다.")
            if o["stage"] not in SHIPPED_STAGES and o["expected_ship_date"]:
                exp = parse_iso(o["expected_ship_date"])
                if exp:
                    remain_h = (exp - now()).total_seconds() / 3600.0
                    if remain_h < 0:
                        active["SHIP_OVERDUE"].add(oid)
                        _upsert_alert(conn, "SHIP_OVERDUE", "order", oid,
                                      f"{label} — 출고예정일({o['expected_ship_date']}) 경과, 아직 출고 전입니다.",
                                      due_at=o["expected_ship_date"])
                    elif remain_h <= 24:
                        active["SHIP_DUE_SOON"].add(oid)
                        _upsert_alert(conn, "SHIP_DUE_SOON", "order", oid,
                                      f"{label} — 출고예정일까지 {remain_h:.0f}시간 남았습니다.",
                                      due_at=o["expected_ship_date"])
            if o["stage"] in ("RECEIVED", "ORDERED_CN", "CN_SHIPPING", "BAEDAEJI_IN") \
                    and age_h is not None and age_h >= 120:
                active["STALE_ORDER"].add(oid)
                _upsert_alert(conn, "STALE_ORDER", "order", oid,
                              f"{label} — 주문 후 {age_h/24:.1f}일 경과, 현재 단계 지연 중입니다.")
        for kind, ids in active.items():
            _resolve_stale(conn, kind, ids)
            counts[kind] = len(ids)

        # ── 문의 규칙 ──
        active_q = {k: set() for k in
                    ("INQUIRY_UNANSWERED", "INQUIRY_DUE_SOON", "INQUIRY_OVERDUE")}
        inquiries = conn.execute(
            "SELECT * FROM inquiries WHERE status='unanswered'"
        ).fetchall()
        for q in inquiries:
            qid = str(q["id"])
            label = f"문의 #{q['id']} · {q['product_name'] or q['summary'] or ''}"
            age_h = hours_between(q["received_at"])
            if age_h is not None and age_h >= 24:
                active_q["INQUIRY_UNANSWERED"].add(qid)
                _upsert_alert(conn, "INQUIRY_UNANSWERED", "inquiry", qid,
                              f"{label} — 미답변 {age_h:.0f}시간 경과")
            if q["due_at"]:
                due = parse_iso(q["due_at"])
                if due:
                    remain_h = (due - now()).total_seconds() / 3600.0
                    if remain_h < 0:
                        active_q["INQUIRY_OVERDUE"].add(qid)
                        _upsert_alert(conn, "INQUIRY_OVERDUE", "inquiry", qid,
                                      f"{label} — 회신 기한({q['due_at']})이 지났습니다.",
                                      due_at=q["due_at"])
                    elif remain_h <= 24:
                        active_q["INQUIRY_DUE_SOON"].add(qid)
                        _upsert_alert(conn, "INQUIRY_DUE_SOON", "inquiry", qid,
                                      f"{label} — 회신 기한까지 {remain_h:.0f}시간 남았습니다.",
                                      due_at=q["due_at"])
        for kind, ids in active_q.items():
            _resolve_stale(conn, kind, ids)
            counts[kind] = len(ids)

    return counts


def open_alerts(db_path=None, limit=200):
    with db(db_path) as conn:
        return [dict(r) for r in conn.execute(
            """
            SELECT * FROM alerts WHERE status='open'
            ORDER BY CASE severity WHEN 'critical' THEN 0 ELSE 1 END, created_at ASC
            LIMIT ?
            """, (limit,)).fetchall()]


def set_alert_status(alert_id: int, status: str, db_path=None):
    ts = now_iso()
    with db(db_path) as conn:
        conn.execute(
            "UPDATE alerts SET status=?, updated_at=?, resolved_at=? WHERE id=?",
            (status, ts, ts if status == "resolved" else None, alert_id),
        )


def morning_summary(db_path=None) -> str:
    """매일 아침 경보 요약 (토스트/메신저/콘솔 공용 텍스트)."""
    evaluate(db_path)
    alerts = open_alerts(db_path)
    with db(db_path) as conn:
        stage_rows = conn.execute(
            "SELECT stage, COUNT(*) AS n FROM orders "
            "WHERE stage NOT IN ('DELIVERED','CANCELLED') GROUP BY stage"
        ).fetchall()
        unanswered = conn.execute(
            "SELECT COUNT(*) AS n FROM inquiries WHERE status='unanswered'"
        ).fetchone()["n"]
    lines = [f"[바이셀 구매대행 아침 요약] {now().strftime('%Y-%m-%d %H:%M')} KST"]
    crit = [a for a in alerts if a["severity"] == "critical"]
    warn = [a for a in alerts if a["severity"] != "critical"]
    lines.append(f"긴급 경보 {len(crit)}건 / 주의 경보 {len(warn)}건 / 미답변 문의 {unanswered}건")
    for a in crit + warn:
        lines.append(f"- [{'긴급' if a['severity']=='critical' else '주의'}] {a['title']}: {a['detail'] or ''}")
    if stage_rows:
        from .db import STAGE_LABELS
        stages = ", ".join(f"{STAGE_LABELS.get(r['stage'], r['stage'])} {r['n']}건" for r in stage_rows)
        lines.append(f"진행 중 주문: {stages}")
    if not alerts:
        lines.append("오늘 처리할 경보가 없습니다.")
    return "\n".join(lines)

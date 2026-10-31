"""동기화·입력 계층.

- sync_orders: 쿠팡 발주서 API → orders 테이블 (PC에서 IP 화이트리스트 통과 시 동작)
- sync_products: 쿠팡 상품 API → products + 부적합/판매불가 경보 (기존 store_watch 통합)
- import_orders_csv: Wing에서 내려받은 주문 CSV 수동 임포트 (API가 막힐 때의 대체 경로)
- update_stage / update_shipment: 대시보드에서 단계·운송장을 직접 갱신
- seed_demo: 동작 확인용 샘플 데이터 (실데이터와 섞이지 않게 채널을 demo로 분리하지 않고
  주문번호 접두사 DEMO- 를 쓴다. 삭제는 --clear-demo)
"""
import csv
import io
import json
from datetime import timedelta

from . import alerts as alert_engine
from .coupang import CoupangClient, load_coupang_credentials, normalize_ordersheet
from .db import db
from .timeutil import mask_name, now, now_iso, parse_iso
from .unsuitable import check_unsuitable


def _log_run(conn, kind, status, detail):
    conn.execute(
        "INSERT INTO sync_runs (kind, status, detail, ran_at) VALUES (?, ?, ?, ?)",
        (kind, status, detail, now_iso()),
    )


def _upsert_order(conn, rec: dict) -> int:
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO orders (channel, channel_order_id, shipment_box_id, seller_product_id,
            product_name, buyer_masked, quantity, order_price, status_raw, stage,
            ordered_at, expected_ship_date, created_at, updated_at)
        VALUES ('coupang', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel, channel_order_id, shipment_box_id) DO UPDATE SET
            product_name=excluded.product_name, status_raw=excluded.status_raw,
            expected_ship_date=COALESCE(excluded.expected_ship_date, orders.expected_ship_date),
            updated_at=excluded.updated_at,
            stage=CASE
                WHEN orders.stage IN ('ORDERED_CN','CN_SHIPPING','BAEDAEJI_IN')
                     AND excluded.stage='RECEIVED' THEN orders.stage
                ELSE excluded.stage END
        """,
        (rec["channel_order_id"], rec.get("shipment_box_id") or "", rec.get("seller_product_id"),
         rec.get("product_name"), rec.get("buyer_masked"), rec.get("quantity") or 1,
         rec.get("order_price"), rec.get("status_raw"), rec.get("stage") or "RECEIVED",
         rec.get("ordered_at"), rec.get("expected_ship_date"), ts, ts),
    )
    row = conn.execute(
        "SELECT id FROM orders WHERE channel='coupang' AND channel_order_id=? AND shipment_box_id=?",
        (rec["channel_order_id"], rec.get("shipment_box_id") or ""),
    ).fetchone()
    conn.execute(
        "INSERT OR IGNORE INTO shipments (order_id, updated_at) VALUES (?, ?)",
        (row["id"], ts),
    )
    return row["id"]


def sync_orders(days: int = 7, db_path=None) -> dict:
    vendor, access, secret = load_coupang_credentials()
    client = CoupangClient(vendor, access, secret)
    sheets = client.list_ordersheets(days=days)
    with db(db_path) as conn:
        for s in sheets:
            rec = normalize_ordersheet(s)
            if rec["channel_order_id"]:
                _upsert_order(conn, rec)
        _log_run(conn, "orders", "ok", f"{len(sheets)}건 조회")
    counts = alert_engine.evaluate(db_path)
    return {"orders_seen": len(sheets), "alert_counts": counts}


def sync_products(statuses=("APPROVED", "PARTIAL_APPROVED", "DENIED"), db_path=None) -> dict:
    vendor, access, secret = load_coupang_credentials()
    client = CoupangClient(vendor, access, secret)
    ts = now_iso()
    n_products, n_alerts = 0, 0
    with db(db_path) as conn:
        for st in statuses:
            for p in client.list_products(status=st):
                pid = str(p.get("sellerProductId") or "")
                if not pid:
                    continue
                try:
                    detail = client.get_product_detail(pid)
                except Exception:
                    detail = {}
                merged = {**p, **detail}
                on_sale, _reason = client.is_on_sale(merged)
                reasons = check_unsuitable(merged)
                name = merged.get("sellerProductName") or merged.get("displayProductName") or ""
                items = merged.get("items") or []
                price = items[0].get("salePrice") if items else None
                conn.execute(
                    """
                    INSERT INTO products (channel, seller_product_id, product_id, name,
                        sale_price, status_name, on_sale, unsuitable_reasons,
                        last_checked_at, created_at, updated_at)
                    VALUES ('coupang', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(channel, seller_product_id) DO UPDATE SET
                        name=excluded.name, sale_price=excluded.sale_price,
                        status_name=excluded.status_name, on_sale=excluded.on_sale,
                        unsuitable_reasons=excluded.unsuitable_reasons,
                        last_checked_at=excluded.last_checked_at, updated_at=excluded.updated_at
                    """,
                    (pid, str(merged.get("productId") or ""), name, price,
                     merged.get("statusName"), 1 if on_sale else 0,
                     json.dumps(reasons, ensure_ascii=False), ts, ts, ts),
                )
                n_products += 1
                if reasons:
                    n_alerts += 1
                    alert_engine._upsert_alert(
                        conn, "PRODUCT_UNSUITABLE", "product", pid,
                        f"상품 {pid} · {name} — " + "; ".join(reasons))
                if not on_sale:
                    alert_engine._upsert_alert(
                        conn, "PRODUCT_NOT_ON_SALE", "product", pid,
                        f"상품 {pid} · {name} — {_reason}")
    return {"products_seen": n_products, "unsuitable": n_alerts}


# ── CSV 임포트 (Wing 주문내역 다운로드 파일) ──
CSV_COLUMNS = {
    "주문번호": "channel_order_id", "orderId": "channel_order_id", "order_id": "channel_order_id",
    "묶음배송번호": "shipment_box_id", "shipmentBoxId": "shipment_box_id",
    "상품명": "product_name", "productName": "product_name",
    "구매자": "buyer_masked", "주문자": "buyer_masked",
    "수량": "quantity", "quantity": "quantity",
    "결제금액": "order_price", "주문금액": "order_price",
    "주문일": "ordered_at", "주문일시": "ordered_at", "orderedAt": "ordered_at",
    "출고예정일": "expected_ship_date", "expectedShippingDate": "expected_ship_date",
}


def import_orders_csv(text: str, db_path=None) -> int:
    reader = csv.DictReader(io.StringIO(text))
    count = 0
    with db(db_path) as conn:
        for raw in reader:
            rec = {"stage": "RECEIVED", "status_raw": "CSV"}
            for k, v in raw.items():
                key = CSV_COLUMNS.get((k or "").strip())
                if key and v not in (None, ""):
                    rec[key] = str(v).strip()
            if not rec.get("channel_order_id"):
                continue
            rec.setdefault("shipment_box_id", "")
            if rec.get("buyer_masked"):
                rec["buyer_masked"] = mask_name(rec["buyer_masked"])
            for num_key in ("quantity", "order_price"):
                if rec.get(num_key):
                    try:
                        rec[num_key] = int(float(str(rec[num_key]).replace(",", "")))
                    except ValueError:
                        rec[num_key] = None
            _upsert_order(conn, rec)
            count += 1
        _log_run(conn, "orders_csv", "ok", f"{count}건 임포트")
    alert_engine.evaluate(db_path)
    return count


def update_stage(order_id: int, stage: str, db_path=None):
    ts = now_iso()
    with db(db_path) as conn:
        extra = ""
        if stage in ("INTL_SHIPPED", "KR_DELIVERING"):
            extra = ", shipped_at=COALESCE(shipped_at, ?)"
        if stage == "DELIVERED":
            extra = ", delivered_at=COALESCE(delivered_at, ?)"
        if extra:
            conn.execute(f"UPDATE orders SET stage=?, updated_at=?{extra} WHERE id=?",
                         (stage, ts, ts, order_id))
        else:
            conn.execute("UPDATE orders SET stage=?, updated_at=? WHERE id=?",
                         (stage, ts, order_id))
    alert_engine.evaluate(db_path)


def update_shipment(order_id: int, fields: dict, db_path=None):
    allowed = ("taobao_order_no", "cn_tracking_no", "intl_tracking_no",
               "kr_carrier", "kr_tracking_no", "baedaeji_status")
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    ts = now_iso()
    with db(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO shipments (order_id, updated_at) VALUES (?, ?)",
            (order_id, ts))
        clause = ", ".join(f"{k}=?" for k in sets)
        conn.execute(f"UPDATE shipments SET {clause}, updated_at=? WHERE order_id=?",
                     (*sets.values(), ts, order_id))
    # 운송장이 채워지면 단계 자동 전진 (수동 단계보다 뒤로 가지 않음)
    with db(db_path) as conn:
        row = conn.execute("SELECT stage FROM orders WHERE id=?", (order_id,)).fetchone()
    if row:
        order_stage_rank = ["RECEIVED", "ORDERED_CN", "CN_SHIPPING", "BAEDAEJI_IN",
                            "INTL_SHIPPED", "KR_DELIVERING", "DELIVERED"]
        cur = order_stage_rank.index(row["stage"]) if row["stage"] in order_stage_rank else 0
        target = cur
        if sets.get("kr_tracking_no"):
            target = max(target, 5)
        elif sets.get("intl_tracking_no"):
            target = max(target, 4)
        elif sets.get("cn_tracking_no"):
            target = max(target, 2)
        elif sets.get("taobao_order_no"):
            target = max(target, 1)
        if target > cur:
            update_stage(order_id, order_stage_rank[target], db_path)


def add_inquiry(summary: str, product_name="", received_at=None, due_hours=72,
                channel_inquiry_id=None, db_path=None) -> int:
    ts = now_iso()
    received = parse_iso(received_at) or now()
    due = received + timedelta(hours=due_hours)
    with db(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO inquiries (channel, channel_inquiry_id, product_name, summary,
                status, received_at, due_at, created_at, updated_at)
            VALUES ('coupang', ?, ?, ?, 'unanswered', ?, ?, ?, ?)
            """,
            (channel_inquiry_id, product_name, summary,
             received.isoformat(timespec="seconds"), due.isoformat(timespec="seconds"), ts, ts),
        )
        qid = cur.lastrowid
    alert_engine.evaluate(db_path)
    return qid


def answer_inquiry(inquiry_id: int, template_used="", db_path=None):
    ts = now_iso()
    with db(db_path) as conn:
        conn.execute(
            "UPDATE inquiries SET status='answered', answered_at=?, template_used=?, updated_at=? WHERE id=?",
            (ts, template_used, ts, inquiry_id))
    alert_engine.evaluate(db_path)


# ── 데모 데이터 ──
def seed_demo(db_path=None):
    """경보가 실제로 뜨는 구성으로 샘플 주문 4건 + 문의 2건을 넣는다."""
    n = now()
    samples = [
        # 30시간 전 접수, 아직 발주 안 함 → INSTRUCT_OVERDUE
        ("DEMO-1001", "주방 정리 선반", (n - timedelta(hours=30)).isoformat(timespec="seconds"),
         (n + timedelta(days=2)).date().isoformat(), "RECEIVED"),
        # 출고예정일 어제, 출고 전 → SHIP_OVERDUE (critical)
        ("DEMO-1002", "차량용 거치대", (n - timedelta(days=4)).isoformat(timespec="seconds"),
         (n - timedelta(days=1)).date().isoformat(), "CN_SHIPPING"),
        # 정상 진행 중
        ("DEMO-1003", "접이식 수납 박스", (n - timedelta(days=2)).isoformat(timespec="seconds"),
         (n + timedelta(days=3)).date().isoformat(), "BAEDAEJI_IN"),
        # 배송 완료
        ("DEMO-1004", "LED 무드등", (n - timedelta(days=6)).isoformat(timespec="seconds"),
         (n - timedelta(days=2)).date().isoformat(), "DELIVERED"),
    ]
    with db(db_path) as conn:
        for oid, name, ordered, exp, stage in samples:
            _upsert_order(conn, {
                "channel_order_id": oid, "shipment_box_id": oid + "-1",
                "product_name": name, "buyer_masked": "김*근", "quantity": 1,
                "order_price": 15900, "status_raw": "DEMO", "stage": stage,
                "ordered_at": ordered, "expected_ship_date": exp,
            })
    add_inquiry("배송이 언제 오나요?", product_name="주방 정리 선반",
                received_at=(n - timedelta(hours=30)).isoformat(timespec="seconds"),
                channel_inquiry_id="DEMO-Q1", db_path=db_path)
    add_inquiry("통관 지연 문의", product_name="차량용 거치대",
                received_at=(n - timedelta(hours=50)).isoformat(timespec="seconds"),
                channel_inquiry_id="DEMO-Q2", db_path=db_path)
    return alert_engine.evaluate(db_path)


def clear_demo(db_path=None):
    with db(db_path) as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM orders WHERE channel_order_id LIKE 'DEMO-%'").fetchall()]
        conn.execute("DELETE FROM orders WHERE channel_order_id LIKE 'DEMO-%'")
        conn.execute("DELETE FROM inquiries WHERE channel_inquiry_id LIKE 'DEMO-%'")
        for oid in ids:
            conn.execute("DELETE FROM alerts WHERE target_type='order' AND target_id=?", (str(oid),))
    alert_engine.evaluate(db_path)

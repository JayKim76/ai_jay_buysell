"""3단계: 정산·손익.

- 주문별 실제 비용 입력 (구매가 CNY→환산, 중국배송, 국제배송 실측, 관부가세 실제 청구,
  쿠팡 수수료 자동 계산(판매가×수수료율, 직접 입력 시 그 값 우선), 광고비, 기타)
- 실마진 = 판매가 - 총비용
- 월별 손익: 주문일 기준 월 집계 (정산 입력된 주문만 손익에 포함, 미입력 건수 함께 표시)
- 상품별 손익 + 마진 하위 상품 표시
- 광고비 배분: 월 광고비 총액을 그달 주문 매출 비중으로 주문별 배분
"""
import math

from .db import db
from .timeutil import now_iso

DEFAULT_FEE_RATE = 0.11
DEFAULT_EXCHANGE_RATE = 200.0


def _round100(x: float) -> int:
    return int(round(x / 100.0)) * 100


def compute(s: dict, revenue: int) -> dict:
    """정산 레코드 + 매출로 실마진 계산."""
    purchase_krw = s.get("purchase_krw") or 0
    if s.get("purchase_cny") and not purchase_krw:
        purchase_krw = _round100(float(s["purchase_cny"]) * float(s.get("exchange_rate") or DEFAULT_EXCHANGE_RATE))
    fee = s.get("fee_krw")
    if fee is None:
        fee = _round100(revenue * float(s.get("fee_rate") if s.get("fee_rate") is not None else DEFAULT_FEE_RATE))
    parts = {
        "purchase_krw": purchase_krw,
        "cn_shipping_krw": s.get("cn_shipping_krw") or 0,
        "intl_shipping_krw": s.get("intl_shipping_krw") or 0,
        "customs_krw": s.get("customs_krw") or 0,
        "fee_krw": fee,
        "ad_krw": s.get("ad_krw") or 0,
        "other_krw": s.get("other_krw") or 0,
    }
    total_cost = sum(parts.values())
    profit = revenue - total_cost
    return {**parts, "revenue": revenue, "total_cost": total_cost,
            "profit": profit, "margin_rate": round(profit / revenue, 4) if revenue else 0.0}


def set_settlement(order_id: int, fields: dict, db_path=None):
    """비용 입력 (넘긴 항목만 갱신). purchase_cny를 주면 purchase_krw를 환산해 함께 저장."""
    allowed = ("purchase_cny", "exchange_rate", "purchase_krw", "cn_shipping_krw",
               "intl_shipping_krw", "customs_krw", "fee_rate", "fee_krw", "ad_krw", "other_krw")
    sets = {k: (None if v == "" else v) for k, v in fields.items() if k in allowed}
    ts = now_iso()
    with db(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO order_settlements (order_id, updated_at) VALUES (?, ?)",
            (order_id, ts))
        if "purchase_cny" in sets and sets["purchase_cny"] is not None and "purchase_krw" not in sets:
            rate = sets.get("exchange_rate")
            if rate is None:
                row = conn.execute(
                    "SELECT exchange_rate FROM order_settlements WHERE order_id=?", (order_id,)).fetchone()
                rate = (row["exchange_rate"] if row else None) or DEFAULT_EXCHANGE_RATE
            sets["purchase_krw"] = _round100(float(sets["purchase_cny"]) * float(rate))
        if sets:
            clause = ", ".join(f"{k}=?" for k in sets)
            conn.execute(f"UPDATE order_settlements SET {clause}, updated_at=? WHERE order_id=?",
                         (*sets.values(), ts, order_id))


def get_settlement(order_id: int, db_path=None) -> dict | None:
    with db(db_path) as conn:
        o = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not o:
            return None
        s = conn.execute("SELECT * FROM order_settlements WHERE order_id=?", (order_id,)).fetchone()
    revenue = o["order_price"] or 0
    base = dict(s) if s else {}
    return {"order": dict(o), "settlement": base, **compute(base, revenue),
            "has_settlement": s is not None}


def order_profits(db_path=None, month: str | None = None):
    """주문별 손익 목록. month='YYYY-MM'면 그달 주문만."""
    with db(db_path) as conn:
        q = "SELECT * FROM orders WHERE stage != 'CANCELLED'"
        params = []
        if month:
            # 월 귀속 기준: 배송완료일이 있으면 그 달, 없으면 주문일 (화면에 명시)
            q += " AND substr(COALESCE(delivered_at, ordered_at), 1, 7) = ?"
            params.append(month)
        orders = [dict(r) for r in conn.execute(q, params).fetchall()]
        settlements = {r["order_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM order_settlements").fetchall()}
    out = []
    for o in orders:
        s = settlements.get(o["id"])
        revenue = o["order_price"] or 0
        calc = compute(s or {}, revenue)
        out.append({"order": o, "settled": s is not None, **calc})
    return out


def monthly_summary(month: str, db_path=None) -> dict:
    rows = order_profits(db_path, month)
    settled = [r for r in rows if r["settled"]]
    def _sum(key, data):
        return sum(r[key] for r in data)
    summary = {
        "month": month,
        "orders_total": len(rows),
        "orders_settled": len(settled),
        "revenue_all": _sum("revenue", rows),
        "revenue": _sum("revenue", settled),
        "purchase_krw": _sum("purchase_krw", settled),
        "cn_shipping_krw": _sum("cn_shipping_krw", settled),
        "intl_shipping_krw": _sum("intl_shipping_krw", settled),
        "customs_krw": _sum("customs_krw", settled),
        "fee_krw": _sum("fee_krw", settled),
        "ad_krw": _sum("ad_krw", settled),
        "other_krw": _sum("other_krw", settled),
        "total_cost": _sum("total_cost", settled),
        "profit": _sum("profit", settled),
    }
    summary["margin_rate"] = round(summary["profit"] / summary["revenue"], 4) if summary["revenue"] else 0.0
    return summary


def available_months(db_path=None):
    with db(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(COALESCE(delivered_at, ordered_at), 1, 7) AS m FROM orders "
            "WHERE COALESCE(delivered_at, ordered_at) IS NOT NULL ORDER BY m DESC").fetchall()
    return [r["m"] for r in rows if r["m"]]


def product_summary(db_path=None, month: str | None = None):
    """상품별 손익 (정산 입력된 주문 기준) + 마진율 낮은 순 정렬."""
    rows = [r for r in order_profits(db_path, month) if r["settled"]]
    agg: dict[str, dict] = {}
    for r in rows:
        name = r["order"]["product_name"] or "(상품명 없음)"
        a = agg.setdefault(name, {"product_name": name, "orders": 0, "revenue": 0, "total_cost": 0, "profit": 0})
        a["orders"] += 1
        a["revenue"] += r["revenue"]
        a["total_cost"] += r["total_cost"]
        a["profit"] += r["profit"]
    out = list(agg.values())
    for a in out:
        a["margin_rate"] = round(a["profit"] / a["revenue"], 4) if a["revenue"] else 0.0
    out.sort(key=lambda a: a["margin_rate"])
    return out


def distribute_ad_cost(month: str, total_ad_krw: int, db_path=None) -> int:
    """월 광고비를 그달 주문 매출 비례로 배분한다. 배분된 주문 수 반환.
    기존 ad_krw는 덮어쓴다 (재배분 가능). 매출 0이면 균등 배분."""
    rows = order_profits(db_path, month)
    targets = [r for r in rows if r["order"]["stage"] != "CANCELLED"]
    if not targets or total_ad_krw <= 0:
        return 0
    total_rev = sum(r["revenue"] for r in targets)
    assigned = 0
    with db(db_path) as conn:
        for i, r in enumerate(targets):
            if total_rev:
                share = int(round(total_ad_krw * r["revenue"] / total_rev))
            else:
                share = total_ad_krw // len(targets)
            if i == len(targets) - 1:
                share = total_ad_krw - assigned  # 반올림 잔차는 마지막 주문에
            assigned += share
            oid = r["order"]["id"]
            conn.execute(
                "INSERT OR IGNORE INTO order_settlements (order_id, updated_at) VALUES (?, ?)",
                (oid, now_iso()))
            conn.execute(
                "UPDATE order_settlements SET ad_krw=?, updated_at=? WHERE order_id=?",
                (share, now_iso(), oid))
    return len(targets)

"""3단계 테스트: 정산 실마진·월 손익·상품별 집계·광고비 배분·CS 템플릿."""
import os
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from buysell import cs_templates, settlement, sync
from buysell.db import db, init_db
from buysell.timeutil import now


def fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    init_db(path)
    return path


def _order(path, oid, name, price, days_ago=1):
    with db(path) as conn:
        sync._upsert_order(conn, {
            "channel_order_id": oid, "shipment_box_id": oid + "-1",
            "product_name": name, "buyer_masked": "김**", "quantity": 1,
            "order_price": price, "status_raw": "TEST", "stage": "DELIVERED",
            "ordered_at": (now() - timedelta(days=days_ago)).isoformat(timespec="seconds"),
            "expected_ship_date": None})
        return conn.execute(
            "SELECT id FROM orders WHERE channel_order_id=?", (oid,)).fetchone()["id"]


def test_order_profit():
    path = fresh_db()
    oid = _order(path, "S-1", "텐트", 46300)
    settlement.set_settlement(oid, {"purchase_cny": 100, "exchange_rate": 200,
                                    "cn_shipping_krw": 0, "intl_shipping_krw": 9000,
                                    "customs_krw": 2900}, path)
    r = settlement.get_settlement(oid, path)
    # 구매 환산 20,000 + 국제 9,000 + 관부가세 2,900 + 수수료 46,300*11%=5,100(100원 반올림) = 37,000
    assert r["purchase_krw"] == 20000, r
    assert r["fee_krw"] == 5100, r
    assert r["total_cost"] == 37000, r
    assert r["profit"] == 9300, r
    print("test_order_profit OK")


def test_monthly_and_product_summary():
    path = fresh_db()
    month = now().strftime("%Y-%m")
    o1 = _order(path, "S-2", "텐트", 46300)
    o2 = _order(path, "S-3", "수납 박스", 20000)
    _order(path, "S-4", "미입력 상품", 99900)  # 정산 미입력
    settlement.set_settlement(o1, {"purchase_cny": 100, "intl_shipping_krw": 9000,
                                   "customs_krw": 2900}, path)
    settlement.set_settlement(o2, {"purchase_cny": 80, "intl_shipping_krw": 9000,
                                   "customs_krw": 2500}, path)  # 적자 상품
    s = settlement.monthly_summary(month, path)
    assert s["orders_total"] == 3 and s["orders_settled"] == 2, s
    assert s["revenue"] == 66300, s
    prods = settlement.product_summary(path, month)
    assert prods[0]["product_name"] == "수납 박스" and prods[0]["margin_rate"] < 0, prods
    assert prods[-1]["product_name"] == "텐트", prods
    print("test_monthly_and_product_summary OK")


def test_ad_distribution():
    path = fresh_db()
    month = now().strftime("%Y-%m")
    o1 = _order(path, "S-5", "A", 30000)
    o2 = _order(path, "S-6", "B", 10000)
    n = settlement.distribute_ad_cost(month, 8000, path)
    assert n == 2
    r1 = settlement.get_settlement(o1, path)
    r2 = settlement.get_settlement(o2, path)
    assert r1["ad_krw"] + r2["ad_krw"] == 8000, (r1, r2)  # 합계 일치 (잔차 처리)
    assert r1["ad_krw"] == 6000, r1                        # 매출 비례 3:1
    print("test_ad_distribution OK")


def test_templates():
    names = cs_templates.list_templates()
    assert len(names) == 6, names
    text = cs_templates.render("customs_id_request", product_name="텐트")
    assert "개인통관고유부호" in text and "유니패스" in text and "텐트" in text
    text2 = cs_templates.render("shipping_delay_local", product_name="수납 박스")
    assert "현지 판매자" in text2 and "취소·환불" in text2
    for key in names:
        assert "{상품명}" not in cs_templates.render(key, product_name="상품")
    print("test_templates OK")


def test_fee_manual_override():
    path = fresh_db()
    oid = _order(path, "S-7", "텐트", 46300)
    settlement.set_settlement(oid, {"purchase_cny": 100, "fee_krw": 3000}, path)
    r = settlement.get_settlement(oid, path)
    assert r["fee_krw"] == 3000, r
    print("test_fee_manual_override OK")


if __name__ == "__main__":
    test_order_profit()
    test_monthly_and_product_summary()
    test_ad_distribution()
    test_templates()
    test_fee_manual_override()
    print("ALL PHASE3 TESTS PASSED")

"""2단계 테스트: 링크→상품명→마진 역산→사전 검사→초안 생성."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from buysell import sourcing
from buysell.db import init_db


def fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    init_db(path)
    return path


def get(path, cid):
    return [c for c in sourcing.list_candidates(path) if c["id"] == cid][0]


def test_margin_reverse_calc():
    # 구매 100CNY, 환율 200 → 20,000원. 무게 1kg → 국제 9,000. 세금 10% → 2,900.
    # 원가 31,900. 수수료 11%·목표 20% → 판매가 = 31,900 / 0.69 = 46,232 → 46,300원(100원 올림)
    m = sourcing.calc_margin({"price_cny": 100, "cn_shipping_cny": 0, "exchange_rate": 200,
                              "weight_kg": 1, "intl_rate_per_kg": 9000, "tax_rate": 0.10,
                              "fee_rate": 0.11, "target_margin_rate": 0.20})
    assert m["goods_krw"] == 20000, m
    assert m["intl_krw"] == 9000, m
    assert m["tax_krw"] == 2900, m
    assert m["suggested_sale_price_krw"] == 46300, m
    assert abs(m["margin_rate"] - 0.20) < 0.01, m
    print("test_margin_reverse_calc OK")


def test_flow_ready():
    path = fresh_db()
    cid = sourcing.add_candidate(
        "https://item.taobao.com/item.htm?id=111", "冬季帐篷 4人 DAC 防水 露营帐篷",
        "4人, 防水3000mm", price_cny=100, weight_kg=1.0, db_path=path)
    c = get(path, cid)
    names = json.loads(c["generated_names"])
    assert "텐트" in names["displayProductName_search"], names
    assert len(names["searchTags"]) >= 3
    assert c["status"] == "ready", c
    assert c["sale_price_krw"] == 46300, c
    print("test_flow_ready OK")


def test_blocked_by_precheck():
    path = fresh_db()
    cid = sourcing.add_candidate(
        "https://item.taobao.com/item.htm?id=222", "의료기기 혈압계",
        "", price_cny=50, weight_kg=0.5, db_path=path)
    c = get(path, cid)
    assert c["status"] == "blocked", c
    try:
        sourcing.export_draft(cid, path, out_dir=tempfile.mkdtemp())
        raise AssertionError("blocked인데 초안이 생성됨")
    except ValueError:
        pass
    print("test_blocked_by_precheck OK")


def test_manual_sale_and_low_margin():
    path = fresh_db()
    cid = sourcing.add_candidate(
        "https://item.taobao.com/item.htm?id=333", "折叠收纳箱 大型",
        "", price_cny=100, weight_kg=1.0, db_path=path)
    # 판매가를 원가 수준으로 고정 → 마진 부족 상태
    sourcing.update_candidate(cid, {"sale_price_krw": 35000}, path)
    c = get(path, cid)
    assert c["status"] == "low_margin", c
    assert c["sale_price_krw"] == 35000, c
    print("test_manual_sale_and_low_margin OK")


def test_draft_export():
    path = fresh_db()
    out = tempfile.mkdtemp()
    cid = sourcing.add_candidate(
        "https://item.taobao.com/item.htm?id=444", "冬季帐篷 4人 DAC 防水 露营帐篷",
        "", price_cny=100, weight_kg=1.0, db_path=path)
    draft = sourcing.export_draft(cid, path, out_dir=out)
    data = json.load(open(draft, encoding="utf-8"))
    assert data["overseasPurchased"] == "Y"
    assert data["salePrice"] == 46300
    assert data["brand"] == "바이셀"
    assert "텐트" in data["displayProductName"]
    assert get(path, cid)["status"] == "exported"
    print("test_draft_export OK")


def test_intl_override():
    m = sourcing.calc_margin({"price_cny": 10, "exchange_rate": 200, "weight_kg": 5,
                              "intl_shipping_krw": 12000})
    assert m["intl_krw"] == 12000 and "직접 입력" in m["intl_source"], m
    print("test_intl_override OK")


if __name__ == "__main__":
    test_margin_reverse_calc()
    test_flow_ready()
    test_blocked_by_precheck()
    test_manual_sale_and_low_margin()
    test_draft_export()
    test_intl_override()
    print("ALL PHASE2 TESTS PASSED")

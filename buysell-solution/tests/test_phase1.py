"""1단계 테스트: DB·경보 엔진·CSV 임포트·운송장 단계 전진·부적합 검사."""
import os
import sys
import tempfile
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from buysell import alerts as alert_engine
from buysell import sync
from buysell.db import db, init_db
from buysell.timeutil import now
from buysell.unsuitable import check_unsuitable


def fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    init_db(path)
    return path


def kinds(db_path):
    return {a["kind"] for a in alert_engine.open_alerts(db_path)}


def test_demo_alerts():
    path = fresh_db()
    sync.seed_demo(path)
    ks = kinds(path)
    assert "INSTRUCT_OVERDUE" in ks, ks          # 30시간 미발주
    assert "SHIP_OVERDUE" in ks, ks              # 출고예정 경과
    assert "INQUIRY_UNANSWERED" in ks, ks        # 미답변 24h+
    assert "INQUIRY_DUE_SOON" in ks or "INQUIRY_OVERDUE" in ks, ks
    summary = alert_engine.morning_summary(path)
    assert "아침 요약" in summary and "긴급" in summary
    print("test_demo_alerts OK")


def test_stage_progress_clears_alerts():
    path = fresh_db()
    sync.seed_demo(path)
    with db(path) as conn:
        oid = conn.execute(
            "SELECT id FROM orders WHERE channel_order_id='DEMO-1001'").fetchone()["id"]
    # 타오바오 발주 처리 → INSTRUCT_OVERDUE 해소돼야 함
    sync.update_stage(oid, "ORDERED_CN", path)
    with db(path) as conn:
        row = conn.execute(
            "SELECT status FROM alerts WHERE kind='INSTRUCT_OVERDUE' AND target_id=?",
            (str(oid),)).fetchone()
    assert row["status"] == "resolved", row["status"]
    print("test_stage_progress_clears_alerts OK")


def test_shipment_advances_stage():
    path = fresh_db()
    sync.seed_demo(path)
    with db(path) as conn:
        oid = conn.execute(
            "SELECT id FROM orders WHERE channel_order_id='DEMO-1003'").fetchone()["id"]
    sync.update_shipment(oid, {"kr_tracking_no": "1234567890", "kr_carrier": "CJ대한통운"}, path)
    with db(path) as conn:
        stage = conn.execute("SELECT stage FROM orders WHERE id=?", (oid,)).fetchone()["stage"]
    assert stage == "KR_DELIVERING", stage
    print("test_shipment_advances_stage OK")


def test_csv_import_and_masking():
    path = fresh_db()
    csv_text = ("주문번호,상품명,구매자,수량,결제금액,주문일시,출고예정일\n"
                f"CSV-1,테스트 상품,김재근,2,\"31,800\",{(now()-timedelta(hours=30)).strftime('%Y-%m-%d %H:%M:%S')},"
                f"{(now()+timedelta(days=2)).strftime('%Y-%m-%d')}\n")
    n = sync.import_orders_csv(csv_text, path)
    assert n == 1
    with db(path) as conn:
        row = conn.execute("SELECT * FROM orders WHERE channel_order_id='CSV-1'").fetchone()
    assert row["buyer_masked"] == "김**", row["buyer_masked"]   # 마스킹
    assert row["quantity"] == 2 and row["order_price"] == 31800
    assert "INSTRUCT_OVERDUE" in kinds(path)                    # 30시간 경과 경보
    print("test_csv_import_and_masking OK")


def test_unsuitable_rules_carried_over():
    reasons = check_unsuitable({
        "sellerProductName": "의료기기 혈압계 구매대행",
        "items": [{"overseasPurchased": "Y"}],
    })
    assert any("CAUTION_의료기기" in r and "구매대행 불가" in r for r in reasons), reasons
    assert check_unsuitable({"sellerProductName": "일반 수납 박스", "items": []}) == []
    print("test_unsuitable_rules_carried_over OK")


def test_answer_inquiry_clears_alert():
    path = fresh_db()
    sync.seed_demo(path)
    with db(path) as conn:
        qid = conn.execute("SELECT id FROM inquiries LIMIT 1").fetchone()["id"]
    sync.answer_inquiry(qid, "배송지연 안내", path)
    with db(path) as conn:
        row = conn.execute(
            "SELECT status FROM alerts WHERE target_type='inquiry' AND target_id=? AND status='open'",
            (str(qid),)).fetchall()
    assert row == [], row
    print("test_answer_inquiry_clears_alert OK")


if __name__ == "__main__":
    test_demo_alerts()
    test_stage_progress_clears_alerts()
    test_shipment_advances_stage()
    test_csv_import_and_masking()
    test_unsuitable_rules_carried_over()
    test_answer_inquiry_clears_alert()
    print("ALL TESTS PASSED")

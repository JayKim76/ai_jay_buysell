"""바이셀 구매대행 통합 관리 솔루션 — 실행 진입점 (1단계)

사용법:
  python app.py init                 DB 초기화
  python app.py serve                대시보드 (http://127.0.0.1:8765)
  python app.py sync-orders          쿠팡 발주서 동기화 (최근 7일, .env 필요)
  python app.py sync-orders --days 3
  python app.py sync-products        쿠팡 상품 동기화 + 부적합 감시
  python app.py import-csv 파일.csv   Wing 주문 CSV 임포트
  python app.py evaluate             경보 재평가
  python app.py morning              아침 경보 요약 출력 (+ reports/ 저장)
  python app.py demo                 데모 데이터 투입 (동작 확인용)
  python app.py clear-demo           데모 데이터 삭제
  python app.py add-candidate --url 링크 --title 원제목 --price-cny 89 --weight-kg 1.2
  python app.py candidates           소싱 후보·마진 목록
  python app.py draft 후보ID          등록 초안 JSON 생성 (drafts/)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from buysell import alerts as alert_engine
from buysell import sync
from buysell.db import init_db
from buysell.timeutil import now


def main():
    parser = argparse.ArgumentParser(description="구매대행 통합 관리 솔루션 (1단계)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--port", type=int, default=8765)
    p_orders = sub.add_parser("sync-orders")
    p_orders.add_argument("--days", type=int, default=7)
    sub.add_parser("sync-products")
    p_csv = sub.add_parser("import-csv")
    p_csv.add_argument("file")
    sub.add_parser("evaluate")
    sub.add_parser("morning")
    sub.add_parser("demo")
    sub.add_parser("clear-demo")
    p_add = sub.add_parser("add-candidate")
    p_add.add_argument("--url", required=True)
    p_add.add_argument("--title", default="")
    p_add.add_argument("--desc", default="")
    p_add.add_argument("--price-cny", type=float)
    p_add.add_argument("--cn-shipping-cny", type=float, default=0)
    p_add.add_argument("--weight-kg", type=float)
    sub.add_parser("candidates")
    p_draft = sub.add_parser("draft")
    p_draft.add_argument("id", type=int)
    p_settle = sub.add_parser("settle")
    p_settle.add_argument("order_db_id", type=int)
    p_settle.add_argument("--purchase-cny", type=float)
    p_settle.add_argument("--cn-shipping-krw", type=int)
    p_settle.add_argument("--intl-shipping-krw", type=int)
    p_settle.add_argument("--customs-krw", type=int)
    p_settle.add_argument("--other-krw", type=int)
    p_pnl = sub.add_parser("pnl")
    p_pnl.add_argument("--month", required=True, help="YYYY-MM")
    p_ad = sub.add_parser("ad-distribute")
    p_ad.add_argument("--month", required=True)
    p_ad.add_argument("--total", type=int, required=True)
    args = parser.parse_args()

    init_db()

    if args.cmd == "init":
        print("DB 초기화 완료")
    elif args.cmd == "serve":
        from buysell.dashboard import serve
        serve(port=args.port)
    elif args.cmd == "sync-orders":
        result = sync.sync_orders(days=args.days)
        print(f"주문 동기화 완료: {result['orders_seen']}건 조회, 경보 {result['alert_counts']}")
    elif args.cmd == "sync-products":
        result = sync.sync_products()
        print(f"상품 동기화 완료: {result['products_seen']}건, 부적합 의심 {result['unsuitable']}건")
    elif args.cmd == "import-csv":
        with open(args.file, encoding="utf-8-sig") as f:
            count = sync.import_orders_csv(f.read())
        print(f"CSV 임포트 완료: {count}건")
    elif args.cmd == "evaluate":
        print("경보 평가 결과:", alert_engine.evaluate())
    elif args.cmd == "morning":
        text = alert_engine.morning_summary()
        print(text)
        reports = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
        os.makedirs(reports, exist_ok=True)
        path = os.path.join(reports, f"morning-{now().strftime('%Y-%m-%d')}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"\n저장: {path}")
    elif args.cmd == "demo":
        counts = sync.seed_demo()
        print("데모 데이터 투입 완료. 경보:", counts)
        print(alert_engine.morning_summary())
    elif args.cmd == "clear-demo":
        sync.clear_demo()
        print("데모 데이터 삭제 완료")
    elif args.cmd == "add-candidate":
        from buysell.sourcing import add_candidate, list_candidates
        cid = add_candidate(args.url, args.title, args.desc, price_cny=args.price_cny,
                            cn_shipping_cny=args.cn_shipping_cny, weight_kg=args.weight_kg)
        c = [x for x in list_candidates() if x["id"] == cid][0]
        print(f"후보 #{cid} 등록: 상태={c['status']}, 판매가={c['sale_price_krw']}원, "
              f"마진율={(c['margin_rate'] or 0) * 100:.1f}%")
    elif args.cmd == "candidates":
        from buysell.sourcing import list_candidates
        for c in list_candidates():
            print(f"#{c['id']} [{c['status']}] {c['taobao_url']} 판매가={c['sale_price_krw']} "
                  f"마진율={(c['margin_rate'] or 0) * 100:.1f}% 사유={c['unsuitable_reasons']}")
    elif args.cmd == "draft":
        from buysell.sourcing import export_draft
        print("등록 초안 생성:", export_draft(args.id))
    elif args.cmd == "settle":
        from buysell.settlement import get_settlement, set_settlement
        fields = {k: v for k, v in {
            "purchase_cny": args.purchase_cny, "cn_shipping_krw": args.cn_shipping_krw,
            "intl_shipping_krw": args.intl_shipping_krw, "customs_krw": args.customs_krw,
            "other_krw": args.other_krw}.items() if v is not None}
        set_settlement(args.order_db_id, fields)
        r = get_settlement(args.order_db_id)
        print(f"정산 저장: 매출 {r['revenue']:,}원, 총비용 {r['total_cost']:,}원, "
              f"실마진 {r['profit']:,}원 ({r['margin_rate'] * 100:.1f}%)")
    elif args.cmd == "pnl":
        from buysell.settlement import monthly_summary, product_summary
        s = monthly_summary(args.month)
        print(f"[{s['month']}] 정산 {s['orders_settled']}/{s['orders_total']}건 · "
              f"매출 {s['revenue']:,}원 · 총비용 {s['total_cost']:,}원 · "
              f"실이익 {s['profit']:,}원 · 마진율 {s['margin_rate'] * 100:.1f}%")
        for p in product_summary(month=args.month):
            print(f"  - {p['product_name']}: {p['orders']}건, 이익 {p['profit']:,}원, 마진율 {p['margin_rate'] * 100:.1f}%")
    elif args.cmd == "ad-distribute":
        from buysell.settlement import distribute_ad_cost
        n = distribute_ad_cost(args.month, args.total)
        print(f"광고비 {args.total:,}원을 {n}개 주문에 매출 비례 배분했어요.")


if __name__ == "__main__":
    main()

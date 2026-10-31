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


if __name__ == "__main__":
    main()

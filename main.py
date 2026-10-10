"""
쿠팡 판매 상품 랜덤 선정 → AI 소개글 생성 → 스레드 자동 업로드

사용법:
    python main.py                        # 상품 1개 랜덤 선정 후 스레드에 발행
    python main.py --dry-run              # 발행 없이 미리보기만
    python main.py --product-id 12345678  # 특정 상품으로 테스트
"""
import argparse
import random

from config import load_settings
from coupang_client import CoupangClient, CoupangApiError
from ai_writer import generate_post_text, AiWriterError
from threads_client import ThreadsClient, ThreadsApiError


def pick_random_product(client: CoupangClient) -> dict:
    products = client.list_products()
    if not products:
        raise SystemExit("쿠팡에 판매 중인 상품이 없습니다.")
    print(f"[Coupang] 판매 중 상품 {len(products)}개 조회됨")
    chosen = random.choice(products)
    seller_product_id = chosen.get("sellerProductId")
    print(
        f"[Coupang] 랜덤 선정: {chosen.get('sellerProductName')} "
        f"(ID: {seller_product_id})"
    )
    detail = client.get_product_detail(seller_product_id)
    if not detail:
        raise SystemExit(f"상품 상세 조회 실패 (ID: {seller_product_id})")
    return detail


def main() -> None:
    parser = argparse.ArgumentParser(description="쿠팡 상품 스레드 자동 포스팅")
    parser.add_argument(
        "--dry-run", action="store_true", help="스레드 발행 없이 미리보기만 실행"
    )
    parser.add_argument(
        "--product-id", type=str, default=None, help="특정 상품 ID로 테스트"
    )
    args = parser.parse_args()

    settings = load_settings()
    settings.dry_run = args.dry_run

    coupang = CoupangClient(
        settings.coupang_vendor_id,
        settings.coupang_access_key,
        settings.coupang_secret_key,
    )

    if args.product_id:
        print(f"[Coupang] 지정 상품 조회: {args.product_id}")
        product = coupang.get_product_detail(args.product_id)
        if not product:
            raise SystemExit(f"상품 상세 조회 실패 (ID: {args.product_id})")
    else:
        product = pick_random_product(coupang)

    print("[AI] 소개글 생성 중…")
    text, image_url = generate_post_text(
        product,
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        style=settings.post_style,
    )
    print(f"[AI] 생성된 소개글 ({len(text)}자):\n{text}\n")
    if image_url:
        print(f"[AI] 대표 이미지: {image_url}")

    if settings.dry_run:
        print("[DRY-RUN] 스레드 발행을 건너뜁니다.")
        return

    threads = ThreadsClient(settings.threads_user_id, settings.threads_access_token)
    print("[Threads] 발행 중… (미디어 처리 대기 약 30초)")
    post_id = threads.publish_post(text, image_url or None)
    print(f"[Threads] 발행 완료! post_id={post_id}")


if __name__ == "__main__":
    try:
        main()
    except (CoupangApiError, AiWriterError, ThreadsApiError) as e:
        raise SystemExit(f"오류 발생: {e}")

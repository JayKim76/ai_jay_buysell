"""
쿠팡 판매 상품 랜덤 선정 → AI 소개글 생성 → 스레드 자동 업로드

판매중(승인완료 + 판매기간 내 + 상품페이지 정상)인 상품만 선정합니다.

사용법:
    python main.py                        # 판매중 상품 1개 랜덤 선정 후 스레드에 발행
    python main.py --dry-run              # 발행 없이 미리보기만
    python main.py --product-id 12345678  # 특정 상품으로 테스트
    python main.py --no-page-check        # 상품페이지 열림 확인 생략
"""
import argparse
import random
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config import load_settings
from coupang_client import CoupangClient, CoupangApiError
from ai_writer import generate_post_text, AiWriterError
from threads_client import ThreadsClient, ThreadsApiError


def check_sale_status(
    client: CoupangClient, detail: dict, check_page: bool = True
) -> "tuple[bool, str]":
    """상품이 실제 판매중인지 3단계로 확인합니다. (여부, 사유)를 반환합니다."""
    ok, reason = CoupangClient.is_on_sale(detail)
    if not ok:
        return False, reason

    if not check_page:
        return True, "판매중 (페이지 확인 생략)"

    url = CoupangClient.build_product_url(detail)
    if not url:
        # 구매자용 productId가 없으면 페이지 확인 불가 — 경고만 하고 통과
        return True, "판매중 (구매자용 productId 없음 — 페이지 확인 생략)"

    alive = CoupangClient.check_product_page_alive(url)
    if alive is False:
        return False, f"상품페이지가 열리지 않음 ({url})"
    if alive is None:
        return True, "판매중 (상품페이지 확인 불가 — 경고)"
    return True, "판매중"


def pick_random_product(
    client: CoupangClient, check_page: bool = True, max_attempts: int = 10
) -> dict:
    """판매중인 상품만 대상으로 랜덤 선정합니다.

    승인완료 목록에서 후보를 섞은 뒤, 판매 상태 확인을 통과하는 첫 상품을 반환합니다.
    """
    products = client.list_products(status="APPROVED")
    if not products:
        raise SystemExit("쿠팡에 승인완료 상태인 상품이 없습니다.")
    print(f"[Coupang] 승인완료 상품 {len(products)}개 조회됨")

    random.shuffle(products)
    limit = min(max_attempts, len(products))
    for i, chosen in enumerate(products[:limit], 1):
        seller_product_id = chosen.get("sellerProductId")
        name = chosen.get("sellerProductName")
        print(f"[Coupang] 후보 확인 ({i}/{limit}): {name} (ID: {seller_product_id})")

        detail = client.get_product_detail(seller_product_id)
        if not detail:
            print("[Coupang]   → 상세 조회 실패, 다음 후보로 넘어갑니다.")
            continue

        ok, reason = check_sale_status(client, detail, check_page)
        if not ok:
            print(f"[Coupang]   → 제외: {reason}")
            continue

        print(f"[Coupang] 최종 선정: {name} (ID: {seller_product_id}) [{reason}]")
        return detail

    raise SystemExit(
        "판매중인 상품을 찾지 못했습니다. "
        "(승인완료 + 판매기간 + 상품페이지 확인을 모두 통과한 상품 없음)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="쿠팡 상품 스레드 자동 포스팅")
    parser.add_argument(
        "--dry-run", action="store_true", help="스레드 발행 없이 미리보기만 실행"
    )
    parser.add_argument(
        "--product-id", type=str, default=None, help="특정 상품 ID로 테스트"
    )
    parser.add_argument(
        "--no-page-check",
        action="store_true",
        help="상품페이지 열림 확인을 생략합니다",
    )
    args = parser.parse_args()

    settings = load_settings()
    settings.dry_run = args.dry_run
    check_page = not args.no_page_check

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
        ok, reason = check_sale_status(coupang, product, check_page)
        if not ok:
            msg = f"지정 상품이 판매중이 아닙니다: {reason}"
            if settings.dry_run:
                print(f"[경고] {msg} — dry-run이므로 미리보기만 계속합니다.")
            else:
                raise SystemExit(msg + " — 발행을 중단합니다.")
        else:
            print(f"[Coupang] 지정 상품 판매 상태 확인: {reason}")
    else:
        product = pick_random_product(coupang, check_page=check_page)

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

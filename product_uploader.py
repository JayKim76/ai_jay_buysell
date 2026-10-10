"""
쿠팡 상품 원클릭 등록 CLI

사용법:
    python product_uploader.py product.json --dry-run            # 페이로드 미리보기 (API 호출 없음)
    python product_uploader.py product.json --recommend-category # 카테고리 추천만 조회
    python product_uploader.py product.json --category-meta      # 카테고리 필수 고시정보/옵션 메타 조회
    python product_uploader.py product.json                      # 등록 (확인 프롬프트 표시)
    python product_uploader.py product.json --yes --request-approval  # 등록 + 승인요청까지

상품 정보는 JSON 파일로 작성합니다. product_example.json 참고.

주의:
- 쿠팡 API는 테스트 환경이 없습니다. 등록 호출 즉시 실제 환경에 반영됩니다.
- 반드시 IP 화이트리스트에 등록된 환경(사장님 PC)에서 실행하세요.
- 등록 후 카테고리는 변경할 수 없으니 --recommend-category 로 먼저 확인하세요.
"""
import argparse
import json
import sys
from datetime import datetime

from config import load_settings, load_seller_profile, SellerProfile
from coupang_client import CoupangClient, CoupangApiError

# 상품 JSON 필수 필드
REQUIRED_FIELDS = [
    "sellerProductName",   # 관리용 상품명 (발주서에 표시, 변경 불가)
    "brand",               # 브랜드명
    "generalProductName",  # 일반 상품명
    "originalPrice",       # 정가
    "salePrice",           # 판매가
    "images",              # 이미지 URL 목록 (1장 이상, 정사각형 JPG/PNG)
]

# 이미지 규격 (쿠팡 공식 가이드)
IMAGE_RULES = "정사각형 JPG/PNG, 500x500~5000x5000px, 3MB 이하"


def load_product(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"상품 파일을 찾을 수 없습니다: {path}")
    except json.JSONDecodeError as e:
        raise SystemExit(f"JSON 파싱 오류 ({path}): {e}")


def validate_product(product: dict) -> list:
    """상품 JSON 검증. 오류 메시지 목록 반환 (비어 있으면 정상)."""
    errors = []
    for field in REQUIRED_FIELDS:
        if not product.get(field):
            errors.append(f"필수 필드 누락: {field}")

    for price_field in ("originalPrice", "salePrice"):
        value = product.get(price_field)
        if value is not None and (not isinstance(value, (int, float)) or value <= 0):
            errors.append(f"{price_field}는 0보다 큰 숫자여야 합니다.")

    images = product.get("images") or []
    if len(images) > 10:
        errors.append("이미지는 최대 10장까지 등록 가능합니다. (대표 1장 + 추가 9장)")
    for url in images:
        if not isinstance(url, str) or not url.startswith("http"):
            errors.append(f"이미지 URL이 올바르지 않습니다: {url}")

    if not product.get("displayCategoryCode"):
        errors.append(
            "displayCategoryCode가 없습니다. "
            "--recommend-category 로 추천받거나 JSON에 직접 입력하세요."
        )

    # 고시정보/구매옵션은 카테고리마다 필수 항목이 다르므로 경고만
    if not product.get("notices"):
        errors.append("경고: notices(상품정보제공고시)가 비어 있습니다. 카테고리 필수 고시정보를 확인하세요.")
    return errors


def build_images(images: list) -> list:
    """첫 장은 대표(REPRESENTATION), 나머지는 추가(DETAIL)."""
    result = []
    for i, url in enumerate(images):
        result.append(
            {
                "imageOrder": i,
                "imageType": "REPRESENTATION" if i == 0 else "DETAIL",
                "vendorPath": url,
            }
        )
    return result


def build_item(option: dict, product: dict, images: list) -> dict:
    """단일 아이템(옵션) 페이로드 생성."""
    original_price = option.get("originalPrice", product["originalPrice"])
    sale_price = option.get("salePrice", product["salePrice"])
    barcode = product.get("barcode", "")
    return {
        "itemName": option.get("itemName", product["generalProductName"]),
        "originalPrice": original_price,
        "salePrice": sale_price,
        "maximumBuyCount": product.get("maxBuyCount", 100),
        "maximumBuyForPerson": 0,
        "maximumBuyForPersonPeriod": 1,
        "unitCount": 1,
        "adultOnly": "N",
        "taxType": "TAX",
        "parallelImported": "N",
        # 해외직구대행 상품이면 "Y" (통관 관련 추가 정보가 필요할 수 있음)
        "overseasPurchased": product.get("overseasPurchased", "N"),
        "pccNeeded": False,
        "externalVendorSkuCode": option.get("sku", ""),
        "barcode": barcode,
        "emptyBarcode": not bool(barcode),
        "emptyBarcodeReason": "" if barcode else "상품에 바코드 없음",
        "modelNo": product.get("modelNo", ""),
        "extraProperties": {},
        "certifications": product.get(
            "certifications", [{"certificationType": "NOT_REQUIRED", "certificationCode": ""}]
        ),
        "searchTags": product.get("searchTags", []),
        "images": images,
        "notices": product.get("notices", []),
        "attributes": product.get("attributes", []),
        "contents": [
            {
                "contentsType": "TOP",
                "contentDetails": [
                    {
                        "content": product.get("detailHtml", ""),
                        "detailType": "TEXT",
                    }
                ],
            }
        ],
        "offerCondition": "NEW",
        "offerDescription": "",
    }


def build_payload(product: dict, profile: SellerProfile, vendor_id: str) -> dict:
    """상품 JSON + 판매자 프로필 → 쿠팡 등록 API 페이로드."""
    brand = product["brand"]
    general_name = product["generalProductName"]
    images = build_images(product["images"])

    options = product.get("options") or [{"itemName": general_name}]
    items = [build_item(opt, product, images) for opt in options]

    sale_started = product.get("saleStartedAt") or datetime.now().strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    return {
        "displayCategoryCode": product["displayCategoryCode"],
        "sellerProductName": product["sellerProductName"],
        "vendorId": vendor_id,
        "saleStartedAt": sale_started,
        "saleEndedAt": product.get("saleEndedAt", "2099-01-01T23:59:59"),
        "displayProductName": product.get(
            "displayProductName", f"{brand} {general_name}"
        ),
        "brand": brand,
        "generalProductName": general_name,
        "productGroup": product.get("productGroup", general_name),
        "deliveryMethod": "SEQUENCIAL",
        "deliveryCompanyCode": profile.delivery_company_code,
        "deliveryChargeType": profile.delivery_charge_type,
        "deliveryCharge": profile.delivery_charge,
        "freeShipOverAmount": profile.free_ship_over_amount,
        "deliveryChargeOnReturn": profile.delivery_charge_on_return,
        "remoteAreaDeliverable": "N",
        "unionDeliveryType": "UNION_DELIVERY",
        "returnCenterCode": profile.return_center_code,
        "returnChargeName": profile.return_charge_name,
        "companyContactNumber": profile.company_contact_number,
        "returnZipCode": profile.return_zip_code,
        "returnAddress": profile.return_address,
        "returnAddressDetail": profile.return_address_detail,
        "returnCharge": profile.return_charge,
        "afterServiceInformation": profile.after_service_information,
        "afterServiceContactNumber": profile.after_service_contact_number,
        "outboundShippingPlaceCode": profile.outbound_shipping_place_code,
        "vendorUserId": profile.vendor_user_id,
        "requested": True,
        "items": items,
    }


def print_category_recommendation(client: CoupangClient, product: dict) -> None:
    print("[Coupang] 카테고리 추천 조회 중…")
    result = client.recommend_category(
        product_name=product.get("displayProductName")
        or product.get("generalProductName", ""),
        product_description=product.get("description", ""),
        brand=product.get("brand", ""),
    )
    print(f"  추천 카테고리: {result.get('predictedCategoryName')}")
    print(f"  카테고리 코드: {result.get('predictedCategoryId')}")
    print("  → 이 코드를 product.json의 displayCategoryCode에 입력하세요.")
    print("  (쿠팡 수수료는 카테고리마다 다르니 Wing에서 한 번 더 확인 권장)")


def print_category_meta(client: CoupangClient, product: dict) -> None:
    code = product.get("displayCategoryCode")
    if not code:
        raise SystemExit("displayCategoryCode가 없어 메타를 조회할 수 없습니다.")
    print(f"[Coupang] 카테고리 메타 조회 중… (코드: {code})")
    meta = client.get_category_meta(code)
    print(json.dumps(meta, ensure_ascii=False, indent=2)[:3000])
    print("\n위 메타에서 필수 notices(고시정보)와 attributes(구매옵션)를 확인해")
    print("product.json에 반영하세요.")


def confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def main() -> None:
    parser = argparse.ArgumentParser(description="쿠팡 상품 원클릭 등록")
    parser.add_argument("product_json", help="상품 정보 JSON 파일 경로")
    parser.add_argument("--dry-run", action="store_true", help="API 호출 없이 페이로드 미리보기")
    parser.add_argument("--recommend-category", action="store_true", help="카테고리 추천만 조회")
    parser.add_argument("--category-meta", action="store_true", help="카테고리 필수 고시정보/옵션 메타 조회")
    parser.add_argument("--request-approval", action="store_true", help="등록 후 승인요청까지 진행")
    parser.add_argument("--yes", action="store_true", help="확인 프롬프트 건너뛰기 (주의!)")
    args = parser.parse_args()

    settings = load_settings()
    product = load_product(args.product_json)
    client = CoupangClient(
        settings.coupang_vendor_id,
        settings.coupang_access_key,
        settings.coupang_secret_key,
    )

    if args.recommend_category:
        print_category_recommendation(client, product)
        return

    if args.category_meta:
        print_category_meta(client, product)
        return

    errors = validate_product(product)
    fatal = [e for e in errors if not e.startswith("경고:")]
    for e in errors:
        print(f"  {'!' if e.startswith('경고:') else 'X'} {e}")
    if fatal:
        raise SystemExit("상품 정보 오류를 수정한 뒤 다시 실행하세요.")

    profile = load_seller_profile()
    payload = build_payload(product, profile, settings.coupang_vendor_id)

    print("\n[페이로드 미리보기]")
    print(f"  노출상품명: {payload['displayProductName']}")
    print(f"  카테고리 코드: {payload['displayCategoryCode']}")
    print(f"  아이템 수: {len(payload['items'])}개")
    for item in payload["items"]:
        print(f"    - {item['itemName']}: {item['salePrice']}원 (정가 {item['originalPrice']}원)")
    print(f"  이미지: {len(payload['items'][0]['images'])}장 ({IMAGE_RULES})")

    if args.dry_run:
        print("\n[DRY-RUN] API를 호출하지 않았습니다. 전체 페이로드:")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print("\n⚠️  쿠팡 API는 테스트 환경이 없습니다. 등록 즉시 실제 판매 환경에 반영됩니다.")
    if not args.yes and not confirm("정말로 이 상품을 쿠팡에 등록할까요?"):
        print("취소했습니다.")
        return

    print("[Coupang] 상품 등록 중…")
    seller_product_id = client.create_product(payload)
    print(f"[Coupang] 등록 완료! sellerProductId={seller_product_id}")

    if args.request_approval:
        if not args.yes and not confirm("승인요청을 진행할까요? (승인 후 판매 시작)"):
            print("승인요청을 건너뜁니다.")
            return
        print("[Coupang] 승인요청 중…")
        client.request_approval(seller_product_id)
        print("[Coupang] 승인요청 완료!")


if __name__ == "__main__":
    try:
        main()
    except CoupangApiError as e:
        raise SystemExit(f"오류 발생: {e}")

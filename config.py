"""
설정 로드 모듈
.env 파일에서 환경 변수를 읽어 Settings 객체로 반환합니다.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

REQUIRED_VARS = [
    "COUPANG_VENDOR_ID",
    "COUPANG_ACCESS_KEY",
    "COUPANG_SECRET_KEY",
    "THREADS_USER_ID",
    "THREADS_ACCESS_TOKEN",
    "GEMINI_API_KEY",
]


@dataclass
class Settings:
    coupang_vendor_id: str
    coupang_access_key: str
    coupang_secret_key: str
    threads_user_id: str
    threads_access_token: str
    gemini_api_key: str
    gemini_model: str = "gemini-2.0-flash"
    post_style: str = "20대 여성, 친근하고 공감 가는 말투, 반말 위주, 짧고 임팩트 있게"
    dry_run: bool = False


def load_settings() -> Settings:
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        raise SystemExit(
            "필수 환경 변수가 설정되지 않았습니다: " + ", ".join(missing)
            + "\n.env 파일을 확인하세요. (.env.example 참고)"
        )
    return Settings(
        coupang_vendor_id=os.environ["COUPANG_VENDOR_ID"],
        coupang_access_key=os.environ["COUPANG_ACCESS_KEY"],
        coupang_secret_key=os.environ["COUPANG_SECRET_KEY"],
        threads_user_id=os.environ["THREADS_USER_ID"],
        threads_access_token=os.environ["THREADS_ACCESS_TOKEN"],
        gemini_api_key=os.environ["GEMINI_API_KEY"],
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        post_style=os.getenv(
            "POST_STYLE",
            "20대 여성, 친근하고 공감 가는 말투, 반말 위주, 짧고 임팩트 있게",
        ),
    )


# ── 상품 등록용 판매자 프로필 ─────────────────────────
# product_uploader.py 에서만 사용합니다. main.py(스레드 포스팅)에는 필요 없습니다.

SELLER_PROFILE_VARS = [
    "DELIVERY_COMPANY_CODE",
    "RETURN_CENTER_CODE",
    "RETURN_CHARGE_NAME",
    "COMPANY_CONTACT_NUMBER",
    "RETURN_ZIP_CODE",
    "RETURN_ADDRESS",
    "AFTER_SERVICE_CONTACT_NUMBER",
    "OUTBOUND_SHIPPING_PLACE_CODE",
    "VENDOR_USER_ID",
]


@dataclass
class SellerProfile:
    """쿠팡 상품등록에 필요한 판매자 배송/반품 기본값.

    값 확인: 쿠팡 Wing → 상품관리 → 상품등록 화면의 기본 배송/반품 설정,
    출고지/반품지 코드는 Wing → 설정에서 확인.
    """

    delivery_company_code: str       # 택배사 코드 (예: HYUNDAI, KGB, CJGLS)
    delivery_charge_type: str = "FREE"  # FREE | NOT_FREE | CHARGE_ON_RETURN
    delivery_charge: int = 0
    free_ship_over_amount: int = 0
    delivery_charge_on_return: int = 3000
    return_center_code: str = ""     # 반품지 코드 (Wing에서 확인)
    return_charge_name: str = ""     # 반품 담당자명
    company_contact_number: str = ""  # 대표 연락처
    return_zip_code: str = ""
    return_address: str = ""
    return_address_detail: str = ""
    return_charge: int = 3000        # 편도 반품 배송비
    after_service_information: str = "판매자에게 문의"
    after_service_contact_number: str = ""
    outbound_shipping_place_code: str = ""  # 출고지 코드 (Wing에서 확인)
    vendor_user_id: str = ""


def load_seller_profile() -> SellerProfile:
    """환경 변수에서 판매자 프로필 로드. 없으면 안내 후 종료."""
    missing = [v for v in SELLER_PROFILE_VARS if not os.getenv(v)]
    if missing:
        raise SystemExit(
            "상품등록용 판매자 정보가 설정되지 않았습니다: " + ", ".join(missing)
            + "\n.env 파일을 확인하세요. (.env.example의 '판매자 배송/반품 기본값' 참고)"
        )
    return SellerProfile(
        delivery_company_code=os.environ["DELIVERY_COMPANY_CODE"],
        delivery_charge_type=os.getenv("DELIVERY_CHARGE_TYPE", "FREE"),
        delivery_charge=int(os.getenv("DELIVERY_CHARGE", "0")),
        free_ship_over_amount=int(os.getenv("FREE_SHIP_OVER_AMOUNT", "0")),
        delivery_charge_on_return=int(os.getenv("DELIVERY_CHARGE_ON_RETURN", "3000")),
        return_center_code=os.environ["RETURN_CENTER_CODE"],
        return_charge_name=os.environ["RETURN_CHARGE_NAME"],
        company_contact_number=os.environ["COMPANY_CONTACT_NUMBER"],
        return_zip_code=os.environ["RETURN_ZIP_CODE"],
        return_address=os.environ["RETURN_ADDRESS"],
        return_address_detail=os.getenv("RETURN_ADDRESS_DETAIL", ""),
        return_charge=int(os.getenv("RETURN_CHARGE", "3000")),
        after_service_information=os.getenv(
            "AFTER_SERVICE_INFORMATION", "판매자에게 문의"
        ),
        after_service_contact_number=os.environ["AFTER_SERVICE_CONTACT_NUMBER"],
        outbound_shipping_place_code=os.environ["OUTBOUND_SHIPPING_PLACE_CODE"],
        vendor_user_id=os.environ["VENDOR_USER_ID"],
    )

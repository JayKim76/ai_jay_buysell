"""
쿠팡 Wing 오픈 API 클라이언트 (판매자 상품 조회 전용)

인증: HMAC-SHA256 서명 (쿠팡 공식 문서 방식)
  Authorization: CEA algorithm=HmacSHA256, access-key=..., signed-date=..., signature=...
  message = signed-date(UTC, yyMMdd'T'HHmmss'Z') + HTTP method + path + query
"""
import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://api-gateway.coupang.com"
PRODUCTS_PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products"
CATEGORY_PREDICT_PATH = (
    "/v2/providers/openapi/apis/api/v1/categorization/predict"
)
CATEGORY_META_PATH = (
    "/v2/providers/seller_api/apis/api/v1/marketplace/meta/display-category-codes"
)

# 판매 가능으로 인정하는 statusName (쿠팡 API 응답 기준)
ON_SALE_STATUS_NAMES = ("승인완료", "부분승인완료")

# 상품페이지 soft-404 판별 문구
DEAD_PAGE_MARKER = "상품을 찾을 수 없습니다"

SEOUL_TZ = ZoneInfo("Asia/Seoul")


class CoupangApiError(RuntimeError):
    pass


class CoupangClient:
    def __init__(self, vendor_id: str, access_key: str, secret_key: str):
        self.vendor_id = vendor_id
        self.access_key = access_key
        self.secret_key = secret_key

    def _authorization(self, method: str, path: str, query: str = "") -> str:
        signed_date = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
        message = signed_date + method + path + query
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return (
            f"CEA algorithm=HmacSHA256, access-key={self.access_key}, "
            f"signed-date={signed_date}, signature={signature}"
        )

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        query = urlencode(params or {})
        headers = {
            "Authorization": self._authorization(method, path, query),
            "Content-Type": "application/json;charset=UTF-8",
        }
        url = BASE_URL + path + (f"?{query}" if query else "")
        try:
            resp = requests.request(
                method, url, headers=headers, json=json_body, timeout=30
            )
        except requests.RequestException as e:
            raise CoupangApiError(f"쿠팡 API 요청 실패: {e}")
        try:
            body = resp.json()
        except ValueError:
            raise CoupangApiError(f"쿠팡 API 응답 파싱 실패 (HTTP {resp.status_code})")
        code_str = str(body.get("code")).upper()
        if resp.status_code != 200 or code_str not in ("200", "SUCCESS"):
            raise CoupangApiError(
                f"쿠팡 API 오류: code={body.get('code')}, message={body.get('message')}"
            )
        return body

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def list_products(
        self, max_per_page: int = 50, status: str = "APPROVED"
    ) -> list[dict]:
        """승인완료 상품 목록을 페이지네이션으로 조회합니다.

        status: 쿠팡 API status 파라미터 (기본 "APPROVED"=승인완료).
                IN_REVIEW(심사중)/SAVED(임시저장)/APPROVING(승인대기중)/
                PARTIAL_APPROVED(부분승인완료)/DENIED(승인반려)/DELETED(상품삭제)
        """
        products: list[dict] = []
        next_token = None
        while True:
            params: dict = {
                "vendorId": self.vendor_id,
                "maxPerPage": max_per_page,
                "status": status,
            }
            if next_token:
                params["nextToken"] = next_token
            body = self._get(PRODUCTS_PATH, params)
            products.extend(body.get("data") or [])
            next_token = body.get("nextToken")
            if not next_token:
                break
        return products

    def get_product_detail(self, seller_product_id: "int | str") -> dict:
        """상품 상세 조회 (옵션/가격/이미지 포함)."""
        body = self._get(f"{PRODUCTS_PATH}/{seller_product_id}")
        return body.get("data") or {}

    # ── 판매 상태 판별 ──────────────────────────────────

    @staticmethod
    def is_on_sale(detail: dict, now: "datetime | None" = None) -> "tuple[bool, str]":
        """상품 상세 정보 기준으로 실제 판매중인지 판별합니다.

        (판매가능 여부, 사유) 튜플을 반환합니다.
        - statusName이 승인완료/부분승인완료가 아니면 False
        - 현재 시각이 saleStartedAt ~ saleEndedAt 구간을 벗어나면 False
        """
        status_name = (detail.get("statusName") or "").strip()
        if status_name not in ON_SALE_STATUS_NAMES:
            return False, f"판매 불가 상태 (statusName={status_name or '없음'})"

        if now is None:
            now = datetime.now(SEOUL_TZ)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=SEOUL_TZ)

        def _parse_kst(value: "str | None") -> "datetime | None":
            # "yyyy-MM-dd'T'HH:mm:ss" (KST 기준)
            if not value:
                return None
            try:
                dt = datetime.fromisoformat(str(value))
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=SEOUL_TZ)
            return dt

        started = _parse_kst(detail.get("saleStartedAt"))
        ended = _parse_kst(detail.get("saleEndedAt"))
        if started and now < started:
            return False, f"판매 시작 전 (saleStartedAt={detail.get('saleStartedAt')})"
        if ended and now > ended:
            return False, f"판매 기간 종료 (saleEndedAt={detail.get('saleEndedAt')})"
        return True, "판매중"

    @staticmethod
    def build_product_url(detail: dict) -> "str | None":
        """구매자용 상품페이지 URL을 만듭니다.

        상세 응답의 productId(구매자용 상품 ID)와 첫 옵션의 vendorItemId를 사용합니다.
        productId가 없으면 None을 반환합니다.
        """
        product_id = detail.get("productId")
        if not product_id:
            return None
        vendor_item_id = None
        items = detail.get("items") or []
        if items:
            vendor_item_id = items[0].get("vendorItemId")
        url = f"https://www.coupang.com/vp/products/{product_id}"
        if vendor_item_id:
            url += f"?vendorItemId={vendor_item_id}"
        return url

    @staticmethod
    def check_product_page_alive(url: str, timeout: int = 10) -> "bool | None":
        """상품페이지가 실제로 열리는지 확인합니다.

        True: 정상 상품페이지로 판단
        False: '상품을 찾을 수 없습니다' 페이지 확인 (판매중지/삭제 등)
        None: 판단 불가 (네트워크 오류·접근 차단 등)
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException:
            return None
        if resp.status_code in (404, 410):
            return False
        if resp.status_code == 403:
            return None  # 접근 차단 — 판단 불가
        body = resp.text or ""
        if DEAD_PAGE_MARKER in body:
            return False
        return True

    # ── 상품 등록용 ──────────────────────────────────

    def recommend_category(
        self,
        product_name: str,
        product_description: str = "",
        brand: str = "",
        attributes: dict | None = None,
    ) -> dict:
        """상품명으로 노출 카테고리 추천 (머신러닝 모델).

        반환: {"predictedCategoryId": "63950", "predictedCategoryName": "...", ...}
        """
        payload: dict = {"productName": product_name}
        if product_description:
            payload["productDescription"] = product_description
        if brand:
            payload["brand"] = brand
        if attributes:
            payload["attributes"] = attributes
        body = self._request("POST", CATEGORY_PREDICT_PATH, json_body=payload)
        return body.get("data") or {}

    def get_category_meta(self, display_category_code: "int | str") -> dict:
        """카테고리별 필수 고시정보/구매옵션 메타 조회.

        등록 전 notices(고시정보)와 attributes(구매옵션) 매핑에 사용합니다.
        """
        body = self._request("GET", f"{CATEGORY_META_PATH}/{display_category_code}")
        return body.get("data") or {}

    def create_product(self, payload: dict) -> "int | str":
        """상품 등록. 성공 시 sellerProductId를 반환합니다.

        주의: 쿠팡 API는 테스트 환경이 없습니다. 호출 즉시 실제 환경에 반영됩니다.
        """
        body = self._request("POST", PRODUCTS_PATH, json_body=payload)
        data = body.get("data")
        if isinstance(data, dict):
            return data.get("sellerProductId")
        return data

    def request_approval(self, seller_product_id: "int | str") -> dict:
        """상품 승인 요청 (등록 후 판매 시작 전 필요)."""
        body = self._request(
            "PUT", f"{PRODUCTS_PATH}/{seller_product_id}/approvals", json_body={}
        )
        return body.get("data") or {}

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

import requests

BASE_URL = "https://api-gateway.coupang.com"
PRODUCTS_PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products"
CATEGORY_PREDICT_PATH = (
    "/v2/providers/openapi/apis/api/v1/categorization/predict"
)
CATEGORY_META_PATH = (
    "/v2/providers/seller_api/apis/api/v1/marketplace/meta/display-category-codes"
)


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
        if resp.status_code != 200 or str(body.get("code")) != "200":
            raise CoupangApiError(
                f"쿠팡 API 오류: code={body.get('code')}, message={body.get('message')}"
            )
        return body

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def list_products(self, max_per_page: int = 50) -> list[dict]:
        """판매 중인 상품 전체 목록을 페이지네이션으로 조회합니다."""
        products: list[dict] = []
        next_token = None
        while True:
            params: dict = {"vendorId": self.vendor_id, "maxPerPage": max_per_page}
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

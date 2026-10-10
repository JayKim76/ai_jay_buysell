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

    def _get(self, path: str, params: dict | None = None) -> dict:
        query = urlencode(params or {})
        headers = {
            "Authorization": self._authorization("GET", path, query),
            "Content-Type": "application/json;charset=UTF-8",
        }
        url = BASE_URL + path + (f"?{query}" if query else "")
        try:
            resp = requests.get(url, headers=headers, timeout=30)
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

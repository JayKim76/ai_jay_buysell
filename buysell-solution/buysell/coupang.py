"""쿠팡 Wing 오픈 API 클라이언트 (기존 coupang_client.py 확장).

- 상품 조회: 기존과 동일 (판매 상태 판별 포함)
- 주문(발주서) 조회 추가: GET /v2/providers/openapi/apis/api/v5/vendors/{vendorId}/ordersheets
  (Coupang Developer Center '발주서 목록 조회(일단위 페이징)' 기준, 2026-11-01 확인)
  - createdAtFrom/To는 'yyyy-mm-dd+09:00' 형식, 최대 31일, maxPerPage 50, nextToken 페이징
  - status: ACCEPT(결제완료) / INSTRUCT(배송지시) / DEPARTURE(출고) /
            DELIVERING(배송중) / FINAL_DELIVERY(배송완료) 등

인증은 기존과 같은 HMAC-SHA256 방식. 키는 .env에서만 읽고 로그에 남기지 않는다.
requests는 호출 시점에만 import한다 (대시보드 등 다른 모듈은 표준 라이브러리만으로 동작).
"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

BASE_URL = "https://api-gateway.coupang.com"
PRODUCTS_PATH = "/v2/providers/seller_api/apis/api/v1/marketplace/seller-products"
ORDERS_PATH = "/v2/providers/openapi/apis/api/v5/vendors/{vendorId}/ordersheets"

ON_SALE_STATUS_NAMES = ("승인완료", "부분승인완료")
SEOUL_TZ = ZoneInfo("Asia/Seoul")

# 쿠팡 발주서 상태 → 통합 단계 매핑
ORDER_STATUS_TO_STAGE = {
    "ACCEPT": "RECEIVED",
    "INSTRUCT": "RECEIVED",
    "DEPARTURE": "INTL_SHIPPED",
    "DELIVERING": "KR_DELIVERING",
    "FINAL_DELIVERY": "DELIVERED",
    "NONE_TRACKING": "INTL_SHIPPED",
}
ORDER_STATUSES = ["ACCEPT", "INSTRUCT", "DEPARTURE", "DELIVERING", "FINAL_DELIVERY"]


class CoupangApiError(RuntimeError):
    pass


def load_coupang_credentials():
    """쿠팡 3개 키만 읽는다 (기존 config와 달리 Threads/Gemini 키는 요구하지 않음)."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    missing = [v for v in ("COUPANG_VENDOR_ID", "COUPANG_ACCESS_KEY", "COUPANG_SECRET_KEY")
               if not os.getenv(v)]
    if missing:
        raise CoupangApiError("필수 환경 변수가 없습니다: " + ", ".join(missing))
    return os.environ["COUPANG_VENDOR_ID"], os.environ["COUPANG_ACCESS_KEY"], os.environ["COUPANG_SECRET_KEY"]


class CoupangClient:
    def __init__(self, vendor_id: str, access_key: str, secret_key: str):
        self.vendor_id = vendor_id
        self.access_key = access_key
        self.secret_key = secret_key

    def _authorization(self, method: str, path: str, query: str = "") -> str:
        signed_date = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
        message = signed_date + method + path + query
        signature = hmac.new(
            self.secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return (f"CEA algorithm=HmacSHA256, access-key={self.access_key}, "
                f"signed-date={signed_date}, signature={signature}")

    def _request(self, method, path, params=None, json_body=None):
        import requests
        query = urlencode(params or {})
        headers = {
            "Authorization": self._authorization(method, path, query),
            "Content-Type": "application/json;charset=UTF-8",
        }
        url = BASE_URL + path + (f"?{query}" if query else "")
        try:
            resp = requests.request(method, url, headers=headers, json=json_body, timeout=30)
        except requests.RequestException as e:
            raise CoupangApiError(f"쿠팡 API 요청 실패: {e}")
        try:
            body = resp.json()
        except ValueError:
            raise CoupangApiError(f"쿠팡 API 응답 파싱 실패 (HTTP {resp.status_code})")
        code_str = str(body.get("code")).upper()
        if resp.status_code != 200 or code_str not in ("200", "SUCCESS"):
            raise CoupangApiError(
                f"쿠팡 API 오류: code={body.get('code')}, message={body.get('message')}")
        return body

    def _get(self, path, params=None):
        return self._request("GET", path, params=params)

    # ── 상품 ──
    def list_products(self, max_per_page=50, status="APPROVED"):
        products, next_token = [], None
        while True:
            params = {"vendorId": self.vendor_id, "maxPerPage": max_per_page, "status": status}
            if next_token:
                params["nextToken"] = next_token
            body = self._get(PRODUCTS_PATH, params)
            products.extend(body.get("data") or [])
            next_token = body.get("nextToken")
            if not next_token:
                break
        return products

    def get_product_detail(self, seller_product_id):
        body = self._get(f"{PRODUCTS_PATH}/{seller_product_id}")
        return body.get("data") or {}

    @staticmethod
    def is_on_sale(detail, now=None):
        status_name = (detail.get("statusName") or "").strip()
        if status_name not in ON_SALE_STATUS_NAMES:
            return False, f"판매 불가 상태 (statusName={status_name or '없음'})"
        if now is None:
            now = datetime.now(SEOUL_TZ)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=SEOUL_TZ)

        def _parse(value):
            if not value:
                return None
            try:
                dt = datetime.fromisoformat(str(value))
            except ValueError:
                return None
            return dt.replace(tzinfo=SEOUL_TZ) if dt.tzinfo is None else dt

        started, ended = _parse(detail.get("saleStartedAt")), _parse(detail.get("saleEndedAt"))
        if started and now < started:
            return False, "판매 시작 전"
        if ended and now > ended:
            return False, "판매 기간 종료"
        return True, "판매중"

    # ── 주문(발주서) ──
    def list_ordersheets(self, days: int = 7, statuses=None):
        """최근 days일 발주서를 상태별로 일단위 페이징 조회한다."""
        path = ORDERS_PATH.format(vendorId=self.vendor_id)
        today = datetime.now(SEOUL_TZ).date()
        created_from = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d") + "+09:00"
        created_to = (today + timedelta(days=1)).strftime("%Y-%m-%d") + "+09:00"
        sheets = []
        for status in (statuses or ORDER_STATUSES):
            next_token = None
            while True:
                params = {
                    "createdAtFrom": created_from,
                    "createdAtTo": created_to,
                    "status": status,
                    "maxPerPage": 50,
                }
                if next_token:
                    params["nextToken"] = next_token
                body = self._get(path, params)
                sheets.extend(body.get("data") or [])
                next_token = body.get("nextToken")
                if not next_token:
                    break
        return sheets


def normalize_ordersheet(sheet: dict) -> dict:
    """발주서 응답 1건을 통합 orders 레코드로 정규화한다."""
    items = sheet.get("orderItems") or []
    first = items[0] if items else {}
    qty = sum(int(it.get("shippingCount") or 0) for it in items) or 1
    price = 0
    for it in items:
        try:
            price += int(it.get("salesPrice") or 0) * int(it.get("shippingCount") or 1)
        except (TypeError, ValueError):
            pass
    status_raw = str(sheet.get("status") or "")
    receiver = sheet.get("receiver") or {}
    from .timeutil import mask_name
    return {
        "channel_order_id": str(sheet.get("orderId") or ""),
        "shipment_box_id": str(sheet.get("shipmentBoxId") or ""),
        "seller_product_id": str(first.get("sellerProductId") or ""),
        "product_name": first.get("sellerProductName") or first.get("productName") or "",
        "buyer_masked": mask_name((sheet.get("orderer") or {}).get("name") or receiver.get("name")),
        "quantity": qty,
        "order_price": price or None,
        "status_raw": status_raw,
        "stage": ORDER_STATUS_TO_STAGE.get(status_raw, "RECEIVED"),
        "ordered_at": sheet.get("orderedAt"),
        "expected_ship_date": (sheet.get("orderer") or {}).get("expectedShippingDate")
        or sheet.get("expectedShippingDate"),
    }

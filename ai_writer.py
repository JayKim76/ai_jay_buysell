"""
Gemini API를 이용해 스레드 소개글을 자동 생성합니다.
"""
import requests

GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class AiWriterError(RuntimeError):
    pass


def summarize_product(product: dict) -> dict:
    """상품 상세 JSON에서 소개글 생성에 필요한 정보만 추출합니다."""
    name = (
        product.get("displayProductName")
        or product.get("sellerProductName")
        or "상품"
    )
    brand = product.get("brand") or ""
    items = product.get("items") or []
    first = items[0] if items else {}
    sale_price = first.get("salePrice")
    original_price = first.get("originalPrice")
    item_name = first.get("itemName") or ""

    image_url = ""
    images = first.get("images") or []
    for img in images:
        if img.get("imageType") == "REPRESENTATION" and img.get("vendorPath"):
            image_url = img["vendorPath"]
            break
    if not image_url and images:
        image_url = (images[0] or {}).get("vendorPath") or ""
    if image_url.startswith("http://"):
        image_url = "https://" + image_url[len("http://"):]

    return {
        "name": name,
        "brand": brand,
        "item_name": item_name,
        "sale_price": sale_price,
        "original_price": original_price,
        "image_url": image_url,
    }


def generate_post_text(
    product: dict,
    api_key: str,
    model: str,
    style: str,
    max_chars: int = 480,
) -> "tuple[str, str]":
    """(소개글, 대표 이미지 URL)을 반환합니다. 소개글은 max_chars 이하로 자릅니다."""
    info = summarize_product(product)

    price_line = ""
    if info["sale_price"]:
        price_line = f"판매가: {int(info['sale_price']):,}원"
        if info["original_price"] and int(info["original_price"]) > int(
            info["sale_price"]
        ):
            price_line += f" (정가 {int(info['original_price']):,}원)"

    prompt = f"""너는 SNS 마케터야. 아래 쿠팡 판매 상품을 Threads(스레드)에 홍보하는 소개글을 한국어로 작성해줘.

[상품 정보]
- 상품명: {info['name']}
- 브랜드: {info['brand']}
- 옵션: {info['item_name']}
- {price_line}

[작성 조건]
- 말투/스타일: {style}
- 400자 이내, 짧은 문장 위주
- 첫 문장은 스크롤을 멈추게 하는 후크로 시작
- 상품의 핵심 장점을 2~3개 자연스럽게 녹이기 (없는 내용은 지어내지 말 것)
- 마지막에 구매를 유도하는 한 줄 CTA
- 해시태그는 2~3개만 마지막에
- 이모지는 과하지 않게 2~4개
- 소개글 본문만 출력 (제목, 설명 문구 없이)
"""

    url = GENERATE_URL.format(model=model)
    try:
        resp = requests.post(
            url,
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
    except requests.RequestException as e:
        raise AiWriterError(f"Gemini API 요청 실패: {e}")

    try:
        body = resp.json()
    except ValueError:
        raise AiWriterError(f"Gemini API 응답 파싱 실패 (HTTP {resp.status_code})")

    if resp.status_code != 200:
        raise AiWriterError(f"Gemini API 오류 (HTTP {resp.status_code}): {body}")

    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise AiWriterError(f"Gemini API 응답에서 텍스트를 찾지 못했습니다: {body}")

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text, info["image_url"]

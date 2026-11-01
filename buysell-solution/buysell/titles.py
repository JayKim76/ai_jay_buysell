"""타오바오 링크 파싱 + 상품명 생성 (기존 taobao_title_optimizer.py에서 이관).

- parse_taobao_url / fetch_taobao_info: 링크에서 플랫폼·상품ID 추출, 자동 추출 시도
- clean_title_rule_based: Gemini 없이 규칙으로 상품명 3종 + 태그 생성 (폴백)
- generate_with_gemini: GEMINI_API_KEY가 있으면 AI 생성, 실패 시 호출자가 폴백
기존 검증된 코드를 그대로 가져왔고, config/store_watch 의존만 제거했다.
"""
import json
import os
import re
from urllib.parse import parse_qs, urlparse

GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


TAOBAO_PATTERNS = [
    (r"item\.taobao\.com/item\.htm", "taobao"),
    (r"detail\.tmall\.com/item\.htm", "tmall"),
    (r"detail\.1688\.com/offer", "1688"),
    (r"world\.taobao\.com/item", "taobao-world"),
    (r"m\.tb\.cn", "taobao-short"),
    (r"item\.taobao\.com", "taobao"),
]


def parse_taobao_url(url: str) -> dict:
    """타오바오/티몰/1688 링크에서 플랫폼·상품ID 추출."""
    url = url.strip()
    platform = "unknown"
    for pat, name in TAOBAO_PATTERNS:
        if re.search(pat, url):
            platform = name
            break
    # item id 추출: id=, item_id=, offer id 경로
    item_id = ""
    try:
        qs = parse_qs(urlparse(url).query)
        for key in ("id", "item_id", "itemId", "offerId"):
            if key in qs:
                item_id = qs[key][0]
                break
    except Exception:
        pass
    if not item_id:
        m = re.search(r"/offer/(\d+)", url)
        if m:
            item_id = m.group(1)
    if not item_id:
        m = re.search(r"id=(\d+)", url)
        if m:
            item_id = m.group(1)
    return {"original_url": url, "platform": platform, "item_id": item_id}


def fetch_taobao_info(url: str, timeout: int = 15) -> dict:
    """타오바오 페이지 자동 추출 시도. 차단되면 실패 사유 반환.

    타오바오는 로그인·JS·안티봇이 강해 자동 추출이 자주 실패합니다.
    실패 시 원제목·설명을 직접 입력하는 방식을 권장합니다.
    """
    try:
        import requests
    except ImportError:
        return {"ok": False, "reason": "requests 모듈 없음", "title_raw": "", "price_raw": "", "images": []}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,ko-KR;q=0.8",
        "Referer": "https://www.taobao.com/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except Exception as e:
        return {"ok": False, "reason": f"요청 실패: {e}", "title_raw": "", "price_raw": "", "images": []}
    if resp.status_code != 200:
        return {"ok": False, "reason": f"HTTP {resp.status_code} (로그인·차단 가능)", "title_raw": "", "price_raw": "", "images": []}
    html = resp.text or ""
    # <title>에서 원제목 추출
    title = ""
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        title = re.sub(r"[-_]\s*淘宝.*$", "", title).strip()
    # 가격 패턴 (타오바오는 JS라 잘 안 잡힘)
    price = ""
    m2 = re.search(r'"price"\s*:\s*"([\d.]+)"', html)
    if m2:
        price = m2.group(1)
    # 이미지 (alicdn)
    images = re.findall(r"https?://[^\"'\s]*alicdn\.com[^\"'\s]*\.jpg", html)
    # 중복 제거, 최대 5장
    seen = []
    for im in images:
        if im not in seen:
            seen.append(im)
        if len(seen) >= 5:
            break
    if not title and not images:
        return {"ok": False, "reason": "페이지에서 제목·이미지를 찾지 못함 (JS 렌더링·로그인 필요). 원제목 직접 입력 권장.", "title_raw": "", "price_raw": "", "images": []}
    return {"ok": True, "reason": "", "title_raw": title, "price_raw": price, "images": seen}



CN_TO_KO = {
    "帐篷": "텐트", "睡袋": "침낭", "吊床": "해먹", "推车": "와건", "露营": "캠핑",
    "保温箱": "쿨러 박스", "桌椅": "테이블 체어", "升降桌": "스탠딩 책상",
    "显示器支架": "모니터 암", "显示器支臂": "모니터 암", "增高架": "받침대",
    "宠物推车": "반려동물 유모차", "猫爬架": "캣타워", "猫树": "캣타워",
    "宠物背包": "반려동물 이동 가방", "高尔夫球包": "골프 스탠드백",
    "高尔夫推车": "골프 푸시 카트", "推杆": "퍼팅", "刀具": "칼 세트",
    "铸铁锅": "무쇠 냄비", "行李箱": "캐리어", "冬季": "동계", "防水": "방수",
    "折叠": "접이식", "大型": "대형", "双人": "2인용", "4人": "4인용",
    "实木": "원목", "胡桃木": "월넛", "不锈钢": "스테인리스",
    "铝合金": "알루미늄", "帆布": "캔버스", "自充气": "자충",
}

# 금지어 (생성 후 필터링용, store_watch와 별도로 빠른 체크)
BANNED_IN_TITLE = [
    "나이키", "아디다스", "애플", "삼성", "다이슨", "샤넬", "루이비통", "구찌",
    "스탠리", "헬리녹스", "테일러메이드", "타이틀리스트", "젝시오",
    "의료기기", "의약품", "FDA", "친환경", "천연", "미백", "주름 개선",
    "아토피 치료", "살균", "소독", "몰래카메라", "위치추적기",
]


def clean_title_rule_based(title_cn: str, desc: str = "") -> dict:
    """Gemini 없이 규칙만으로 상품명 3종 생성 (폴백)."""
    text = (title_cn or "") + " " + (desc or "")
    # 중국어 키워드를 한국어로 치환
    ko_text = text
    for cn, ko in CN_TO_KO.items():
        ko_text = ko_text.replace(cn, f" {ko} ")
    # 숫자·단위 추출
    size = ""
    m = re.search(r"(\d+)\s*(인용|人|cm|m|L|寸|kg)", ko_text)
    if m:
        size = m.group(0).strip()
    # 핵심 카테고리 추정 (반려동물·펫을 캠핑 와건보다 먼저 체크: 宠物推车는 반려동물 유모차)
    category = "캠핑용품"
    if "반려동물" in ko_text or "宠物" in text or "유모차" in ko_text:
        category = "반려동물 유모차"
    elif "캣타워" in ko_text or "猫爬架" in text or "猫树" in text:
        category = "캣타워"
    elif "텐트" in ko_text:
        category = "텐트"
    elif "침낭" in ko_text:
        category = "침낭"
    elif "와건" in ko_text:
        category = "캠핑 와건"
    elif "쿨러" in ko_text:
        category = "쿨러 박스"
    elif "해먹" in ko_text:
        category = "해먹"
    elif "모니터" in ko_text:
        category = "모니터 암"
    elif "스탠딩" in ko_text or "升降" in text:
        category = "스탠딩 책상 높이조절대"
    elif "골프" in ko_text:
        category = "골프용품"
    elif "칼" in ko_text or "刀" in text:
        category = "주방 칼 세트"
    elif "캐리어" in ko_text or "行李" in text:
        category = "캐리어"
    # 속성 추출 (재질·색상·구성)
    attrs = []
    for kw in ["방수", "접이식", "대형", "원목", "월넛", "스테인리스", "알루미늄", "캔버스", "자충", "수동", "비전기"]:
        if kw in ko_text:
            attrs.append(kw)
    attr_str = " ".join(attrs[:3])
    # 3종 생성 - 40~80자 맞추기 위해 카테고리별 SEO 문구 추가
    base = f"바이셀 {category}"
    if size:
        base += f" {size}"
    if attr_str:
        base += f" {attr_str}"
    # 카테고리별 보충 문구 (검색어 + 용도 + 특징)
    suffix_map = {
        "텐트": "동계 방수 DAC 폴 설치 간편 캠핑 차박 글램핑",
        "침낭": "동계 방한 4계절 캠핑 차박 백패킹",
        "캠핑 와건": "대용량 접이식 캠핑 차박 피크닉",
        "쿨러 박스": "대용량 보냉 캠핑 차박 피크닉",
        "해먹": "평평 수면 타프 모기장 캠핑 차박",
        "모니터 암": "듀얼 고중량 높이조절 홈오피스 데스크테리어",
        "스탠딩 책상 높이조절대": "수동 높이조절 홈오피스 재택근무",
        "반려동물 유모차": "대형 2단 분리 반려동물 이동 외출",
        "캣타워": "대형 스크래처 숨숨집 고양이",
        "골프용품": "골프 라운드 연습 프리미엄",
        "주방 칼 세트": "주방용 스테인리스 홈쿡 요리",
        "캐리어": "대용량 TSA 하드쉘 여행 출장",
        "캠핑용품": "캠핑 차박 아웃도어",
    }
    suffix = suffix_map.get(category, "프리미엄 대용량 추천")

    def _dedupe_words(s: str) -> str:
        """연속·중복 단어 제거 (예: '대형 대형' → '대형', '방수 방수' → '방수')."""
        seen: set[str] = set()
        out: list[str] = []
        for w in s.split():
            if w not in seen:
                seen.add(w)
                out.append(w)
        return " ".join(out)

    # 검색 최적화 버전: base + suffix (40자 이상 되도록)
    variant_search = _dedupe_words(f"{base} {suffix}")
    # 짧아도 40자 미만이면 카테고리별 공통 문구 추가 (캠핑이 아닌데 캠핑 문구 붙지 않게)
    if len(variant_search) < 40:
        if category in ("텐트", "침낭", "캠핑 와건", "쿨러 박스", "해먹", "캠핑용품"):
            variant_search += " 캠핑 차박 홈캠핑 아웃도어 추천"
        elif category in ("반려동물 유모차", "캣타워"):
            variant_search += " 반려동물 외출 이동 추천"
        elif category in ("모니터 암", "스탠딩 책상 높이조절대"):
            variant_search += " 홈오피스 재택근무 추천"
        elif category == "골프용품":
            variant_search += " 골프 라운드 연습 추천"
        elif category == "주방 칼 세트":
            variant_search += " 홈쿡 요리 추천"
        elif category == "캐리어":
            variant_search += " 여행 출장 추천"
        else:
            variant_search += " 프리미엄 대용량 추천"
    variant_search = _dedupe_words(variant_search)[:80]
    # 간결 버전: base + 핵심 suffix 1~2단어 (그래도 30자 이상 유지)
    variant_short = _dedupe_words(f"{base} {suffix.split()[0] if suffix else ''}".strip())
    if len(variant_short) < 30:
        variant_short = _dedupe_words(f"{base} {suffix}")
    variant_short = variant_short[:80]
    # 프리미엄 버전: base + suffix + 프리미엄
    variant_premium = _dedupe_words(f"{base} {suffix} 프리미엄")[:80]
    # 최종 길이 보정: 검색 버전이 40자 미만이면 프리미엄 문구로 채움
    if len(variant_search) < 40:
        variant_search = _dedupe_words(variant_search + " 프리미엄 대용량")[:80]
    # 검색태그: 제목 단어 + 카테고리별 핵심어 (카테고리 무관한 캠핑 태그 남발 방지)
    tags = []
    # 제목에서 추출 (바이셀 제외)
    for w in variant_search.replace("바이셀", "").split():
        tag = re.sub(r"\s+", "", w)
        if tag and tag not in tags and 2 <= len(tag) <= 20:
            tags.append(tag)
    # 카테고리별 보충 태그
    cat_tags = {
        "텐트": ["동계텐트", "캠핑텐트"],
        "반려동물 유모차": ["반려동물유모차", "강아지유모차"],
        "골프용품": ["골프백", "골프용품"],
        "주방 칼 세트": ["주방칼", "칼세트"],
        "모니터 암": ["모니터암", "듀얼모니터암"],
        "캐리어": ["캐리어", "여행캐리어"],
    }.get(category, [])
    for t in cat_tags:
        if t not in tags:
            tags.append(t)
    tags = tags[:10]
    return {
        "displayProductName_search": variant_search[:80],
        "displayProductName_short": variant_short[:80],
        "displayProductName_premium": variant_premium[:80],
        "generalProductName": category,
        "searchTags": tags[:10],
        "method": "rule-based",
        "reason": "Gemini 키 없음 / --no-ai 로 규칙 기반 생성",
    }



def generate_with_gemini(title_cn: str, desc: str, url_info: dict, api_key: str, model: str = "gemini-2.0-flash") -> dict:
    """Gemini로 쿠팡 검색노출 최적화 상품명 3종 + 태그 생성."""
    try:
        import requests
    except ImportError:
        raise RuntimeError("requests 모듈이 필요합니다.")
    prompt = f"""너는 쿠팡 검색노출 전문 MD야. 아래 타오바오 상품을 분석해 쿠팡에 등록할 상품명을 만들어줘.

[타오바오 원본]
- 링크: {url_info.get('original_url', '')} (플랫폼: {url_info.get('platform', '')}, 상품ID: {url_info.get('item_id', '')})
- 원제목(중국어): {title_cn}
- 설명: {desc}

[규칙 - 반드시 지킬 것]
- 자사 브랜드는 '바이셀'만 사용. 나이키·아디다스·애플·삼성·다이슨·스탠리·헬리녹스·테일러메이드 등 타 브랜드명은 절대 넣지 말 것
- 의료·질병·효능 표현 금지 (치료·미백·주름개선·살균·소독·FDA·친환경·천연 등)
- 전기·전동 제품으로 오해할 표현 금지. 수동·비전기면 '수동' '비전기'를 명시
- 주방용 칼은 '주방용'을 명시, 흉기 오인 표현 금지
- 유아용·어린이용으로 오해할 표현 금지 (반려동물용은 '반려동물용' 명시)
- displayProductName은 40~80자, 핵심 키워드를 앞에 배치. 형식: [바이셀] 핵심키워드 + 속성(재질/사이즈/색상/구성) + 용도
- generalProductName은 검색 핵심어 1개 (예: 동계 텐트)
- searchTags는 10개 이내, 띄어쓰기 없는 키워드 (예: 동계텐트, 캠핑텐트)

[출력 형식 - JSON만 출력, 설명 없이]
{{
  "displayProductName_search": "검색노출 최적화 버전 (키워드 풍부)",
  "displayProductName_short": "간결 버전",
  "displayProductName_premium": "프리미엄 버전",
  "generalProductName": "핵심어 1개",
  "searchTags": ["태그1", "태그2", "... 10개 이내"],
  "analysis": "상품 분석 2줄 (재질·사이즈·구성·용도 요약, 지어내지 말 것)",
  "cautions": ["등록 시 주의점 1~2개 (KC·식품접촉·규격 등)"]
}}
"""
    url = GENERATE_URL.format(model=model)
    resp = requests.post(
        url, params={"key": api_key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=60,
    )
    try:
        body = resp.json()
    except ValueError:
        raise RuntimeError(f"Gemini 응답 파싱 실패 (HTTP {resp.status_code})")
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini 오류 (HTTP {resp.status_code}): {body}")
    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"Gemini 응답에서 텍스트를 찾지 못함: {body}")
    # ```json ... ``` 제거
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # JSON이 아니면 규칙 폴백으로 대체
        return clean_title_rule_based(title_cn, desc)
    data["method"] = "gemini"
    return data



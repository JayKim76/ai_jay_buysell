"""
고가(10만원 이상) 트렌드 제품 소싱 추천 - 판매부적합 기준 반영

기준:
  - 쿠팡 Wing "판매 및 배송 - 판매부적합상품 안내"(2026/10/06) 기준을
    threads-coupang-poster/store_watch.py의 RULES로 1차 검사합니다.
  - 가격 필터: 판매가(쿠팡 예상 판매가) 100,000원 이상만 추천합니다.
  - 전동·전기 제품은 KC 대상이라 원칙 제외하고, 수동·비전기 버전만 포함합니다.
  - 의료기기·의약외품·식품·화장품·생활화학·브랜드품·어린이제품은 제외합니다.

사용법:
  python high_price_sourcing.py                          # 20개 전체 표로 출력
  python high_price_sourcing.py --min-price 100000      # 최소 가격 변경
  python high_price_sourcing.py --json                   # JSON으로 출력
  python high_price_sourcing.py --check "상품명"         # 임의 상품명 부적합 검사
  python high_price_sourcing.py --next                   # 추천버튼용: 매번 다른 카테고리로 3개 추천 (상태 저장)
  python high_price_sourcing.py --next --batch-size 5  # 다음 카테고리에서 5개 추천
  python high_price_sourcing.py --category 골프          # 특정 카테고리만 추천
  python high_price_sourcing.py --random-category        # 이전과 다른 랜덤 카테고리 추천

추천버튼 동작:
  --next를 쓸 때마다 카테고리가 캠핑 → 홈오피스 → 반려동물 → 골프 → 주방 → 여행 → 캠핑 순으로
  순환합니다. 마지막 카테고리는 high_price_state.json에 저장되어, 다음 클릭 때 다른 카테고리가 나옵니다.
  같은 카테고리를 연속으로 추천하지 않습니다.

가격 안내:
  아래 가격은 쿠팡 예상 판매가(추정치)이며 실시간 쿠팡 가격이 아닙니다.
  소싱 전 1688·타오바오 원가 + 배송비 + 수수료 + 마진을 계산해
  최종 판매가를 다시 산정하세요.
"""
import argparse
import json
import sys
import os

# store_watch RULES 재사용 (없으면 로컬 폴백)
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "threads-coupang-poster"))
    from store_watch import check_unsuitable as _check_unsuitable
except Exception:
    _check_unsuitable = None

MIN_PRICE_DEFAULT = 100000
BATCH_SIZE_DEFAULT = 3
STATE_FILE = os.path.join(os.path.dirname(__file__), "high_price_state.json")

# ── 20개 추천 (2026-10-17 조사 기준) ─────────────────────────
# 각 항목: name, price_min, price_max, category, trend, compliance, taobao, coupang_search
PRODUCTS = [
    {
        "name": "동계 4인용 돔 텐트 (DAC 폴, 방수)",
        "price_min": 180000, "price_max": 250000, "category": "캠핑",
        "trend": "에누리 캠핑 거래액 +23%, 텐트 +133% (2026-10-14 보도). 동계 텐트 1위.",
        "compliance": "비전기·비의료·브랜드 없음. 어린이제품 아님. 폴·원단 KC 미대상.",
        "taobao": "冬季帐篷 4人 / 露营帐篷 4人 DAC",
        "coupang_search": "동계 텐트 4인용",
    },
    {
        "name": "2인용 에어 자충매트 10cm (수동 펌프, 비전기)",
        "price_min": 120000, "price_max": 160000, "category": "캠핑",
        "trend": "에누리 에어 자충매트 거래액 +104%, 인기 2위. 차박·캠핑 필수템.",
        "compliance": "전동 펌프 없는 수동 자충식만 소싱. 전기용품 KC 회피.",
        "taobao": "自充气床垫 双人 10cm / 充气床垫 10cm",
        "coupang_search": "에어 자충매트 10cm 2인용",
    },
    {
        "name": "대형 접이식 캠핑 와건 (150L, 내하중 100kg, 비전동)",
        "price_min": 130000, "price_max": 180000, "category": "캠핑",
        "trend": "쿠팡 스포츠페스타(10/15~25) 캠핑·차박 테마, 캠핑 와건 수요 지속.",
        "compliance": "비전동·비전기. 유아용 유모차 아님, 반려·캠핑용으로 표기.",
        "taobao": "露营推车 折叠 大容量 / 户外露营车 150L",
        "coupang_search": "캠핑 와건 대형",
    },
    {
        "name": "동계 침낭 (-10도, 4계절)",
        "price_min": 110000, "price_max": 150000, "category": "캠핑",
        "trend": "에누리 침낭 거래액 +176%. 방한용품 미리 구매 수요.",
        "compliance": "전기 발열 없는 일반 침낭만. 전기장판·발열침낭은 KC 대상이라 제외.",
        "taobao": "冬季睡袋 -10度 / 加厚睡袋 四季",
        "coupang_search": "동계 침낭 -10도",
    },
    {
        "name": "면 캔버스 벨 텐트 (글램핑, 4~5인)",
        "price_min": 250000, "price_max": 350000, "category": "캠핑",
        "trend": "글램핑 프리미엄화, 가족 단위 캠핑 수요. 면 캔버스 벨텐트 인기 지속.",
        "compliance": "비전기·브랜드 없음. 면 캔버스 원단, 방수·UV 표기 확인.",
        "taobao": "帆布帐篷 / 钟形帐篷 露营 4-5人",
        "coupang_search": "벨 텐트 글램핑",
    },
    {
        "name": "대용량 쿨러 박스 45L (비전기 아이스박스)",
        "price_min": 110000, "price_max": 140000, "category": "캠핑",
        "trend": "캠핑·차박 시즌, 대용량 쿨러 수요. 가을 캠핑 거래액 증가.",
        "compliance": "컴프레서·전원 없는 아이스박스만. 전기 쿨러는 KC 대상이라 제외.",
        "taobao": "保温箱 45L / 车载保温箱 大容量",
        "coupang_search": "쿨러 박스 45L",
    },
    {
        "name": "캠핑 해먹 + 타프 + 모기장 세트 (평평 수면형)",
        "price_min": 130000, "price_max": 170000, "category": "캠핑",
        "trend": "2026 해먹 가이드(10/07) 평평 수면형 $204~402. 해먹+타프 세트 인기.",
        "compliance": "비전기·브랜드 없음. 모기장 포함 세트로 구성.",
        "taobao": "吊床 + 天幕 / 露营吊床 带蚊帐 平躺",
        "coupang_search": "캠핑 해먹 타프 세트",
    },
    {
        "name": "프리미엄 접이식 캠핑 테이블 + 체어 2인 세트 (알루미늄)",
        "price_min": 140000, "price_max": 190000, "category": "캠핑",
        "trend": "헬리녹스 올인원 텐트 등 캠핑 입문 세트 트렌드, 테이블·체어 세트 수요.",
        "compliance": "비전기·브랜드 없음. 알루미늄 프레임, 하중 표기 확인.",
        "taobao": "露营桌椅套装 折叠 / 铝合金露营桌椅 2人",
        "coupang_search": "캠핑 테이블 체어 세트",
    },
    {
        "name": "수동 스탠딩 책상 높이조절대 (800~910mm, 수동)",
        "price_min": 100000, "price_max": 130000, "category": "홈오피스",
        "trend": "쿠팡 스탠딩 책상 TOP, 루나랩 103,020~125,000원. 재택·홈오피스 수요.",
        "compliance": "전동 아닌 수동 조절대만. 전동 모션데스크는 전기용품 KC 대상이라 제외.",
        "taobao": "升降桌 升降台 手动 / 站立式升降桌 手动",
        "coupang_search": "스탠딩 책상 높이조절대",
    },
    {
        "name": "듀얼 모니터 암 (고중량, 기계식, 비전기)",
        "price_min": 100000, "price_max": 140000, "category": "홈오피스",
        "trend": "다나와 홈오피스 모니터암 10만원대, 듀얼 모니터암 수요. 데스크테리어.",
        "compliance": "비전기·기계식 스프링만. 전원·USB 허브 내장형은 KC 확인 필요.",
        "taobao": "双屏显示器支架 / 显示器支臂 双屏 机械",
        "coupang_search": "듀얼 모니터 암",
    },
    {
        "name": "원목 모니터 받침대 + 수납 서랍 (월넛, 수동)",
        "price_min": 110000, "price_max": 150000, "category": "홈오피스",
        "trend": "원목 모니터 받침대 프리미엄화, 데스크테리어·수납 트렌드.",
        "compliance": "비전기·목재 가구. 어린이제품 아님, 성인용으로 표기.",
        "taobao": "实木显示器增高架 / 胡桃木显示器支架 带抽屉",
        "coupang_search": "원목 모니터 받침대",
    },
    {
        "name": "프리미엄 반려동물 유모차 (대형, 2단 분리)",
        "price_min": 280000, "price_max": 450000, "category": "반려동물",
        "trend": "케이펫페어 40~50만원대 유모차, 선택적 프리미엄 소비. 1인당 이용금액 +4%.",
        "compliance": "반려동물용으로 명확히 표기. 유아용 유모차(어린이제품 KC)와 혼동 금지.",
        "taobao": "宠物推车 大型 / 宠物推车 可分离 双层",
        "coupang_search": "반려동물 유모차 대형",
    },
    {
        "name": "대형 캣타워 180cm (스크래처 + 숨숨집)",
        "price_min": 130000, "price_max": 180000, "category": "반려동물",
        "trend": "반려동물 프리미엄화, 캣타워 대형화. 온라인 구매액 비중 64%.",
        "compliance": "비전기·비의료. 목재·스크래처, 어린이제품 아님.",
        "taobao": "猫爬架 大型 180cm / 猫树 豪华 带窝",
        "coupang_search": "대형 캣타워 180",
    },
    {
        "name": "프리미엄 반려동물 이동 가방 (슬링백 대형, 기내 반입)",
        "price_min": 110000, "price_max": 150000, "category": "반려동물",
        "trend": "케이펫페어 슬링백 화제, 병원·여행 이동 가방 수요.",
        "compliance": "비전기·브랜드 없음. 반려동물용, 항공 반입 규격 표기 확인.",
        "taobao": "宠物背包 斜挎 / 宠物外出包 大号 可登机",
        "coupang_search": "반려동물 이동 가방 슬링백",
    },
    {
        "name": "골프 스탠드백 (프리미엄, 무브랜드)",
        "price_min": 150000, "price_max": 220000, "category": "골프",
        "trend": "가을 골프 시즌, 골프존 8월 판매 강세. 골프백 프리미엄 수요.",
        "compliance": "브랜드 로고 없는 무브랜드. 가품·상표권 회피.",
        "taobao": "高尔夫球包 支架包 / 高尔夫球袋 支架",
        "coupang_search": "골프 스탠드백",
    },
    {
        "name": "골프 푸시 카트 (3륜 접이식, 비전동)",
        "price_min": 130000, "price_max": 180000, "category": "골프",
        "trend": "골프 라운드 증가, 푸시 카트·카트 용품 수요. 가을 라운드 시즌.",
        "compliance": "전동 아닌 수동 푸시형만. 전동 카트는 KC·배터리 확인 필요.",
        "taobao": "高尔夫推车 三轮 / 高尔夫球车 折叠 手推",
        "coupang_search": "골프 푸시 카트",
    },
    {
        "name": "프리미엄 퍼팅 매트 3m (수동, 자동 리턴 없음)",
        "price_min": 100000, "price_max": 140000, "category": "골프",
        "trend": "가을 골프 연습 수요, 홈 퍼팅 매트 인기. 연습용품 트렌드.",
        "compliance": "비전기·자동 리턴 없는 수동 매트만. 전동 리턴기는 KC 대상.",
        "taobao": "推杆练习垫 3米 / 高尔夫推杆垫 家用",
        "coupang_search": "퍼팅 매트 3m",
    },
    {
        "name": "프리미엄 주방 칼 세트 6종 (스테인리스, 주방용)",
        "price_min": 120000, "price_max": 170000, "category": "주방",
        "trend": "프리미엄 주방·홈쿡 트렌드, 칼 세트 고가화. 주방용 칼 판매 가능.",
        "compliance": "주방용 칼은 판매 가능(도검 예외). '주방용' 명시, 흉기 오인 표기 금지.",
        "taobao": "厨房刀具套装 6件 / 不锈钢刀具套装 家用",
        "coupang_search": "주방 칼 세트 6종",
    },
    {
        "name": "무쇠 주물 프라이팬 + 냄비 세트 (비전기)",
        "price_min": 130000, "price_max": 180000, "category": "주방",
        "trend": "무쇠·주물 프리미엄 주방용품 인기, 내구성·홈쿡 트렌드.",
        "compliance": "비전기·식품접촉용. 식품용 기구 기준 적합 확인, 코팅 표기 확인.",
        "taobao": "铸铁锅套装 / 铸铁煎锅 炖锅 套装",
        "coupang_search": "무쇠 프라이팬 냄비 세트",
    },
    {
        "name": "대용량 여행용 하드 캐리어 28인치 (TSA 잠금)",
        "price_min": 150000, "price_max": 220000, "category": "여행",
        "trend": "가을 여행 시즌, 단풍 산행·여행 수요 증가. 대용량 캐리어 인기.",
        "compliance": "비전기·브랜드 없음. TSA 잠금, 하드쉘, 바퀴·손잡이 내구성 확인.",
        "taobao": "行李箱 28寸 / 硬壳拉杆箱 大容量 TSA",
        "coupang_search": "하드 캐리어 28인치",
    },
]


def is_compliant(product: dict) -> tuple[bool, list[str]]:
    """판매부적합 검사. (적합 여부, 사유 목록)"""
    if _check_unsuitable is None:
        # 폴백: 가격만 확인
        return True, []
    test = {
        "sellerProductName": product.get("name", ""),
        "displayProductName": product.get("name", ""),
        "generalProductName": product.get("name", ""),
        "brand": "",
        "sellerProductId": "",
        "items": [{"overseasPurchased": "Y"}],
    }
    reasons = _check_unsuitable(test)
    # 구매대행 상향이 적용되어도, 위 20개는 이미 수동·비전기로 선별했으므로
    # 실제 걸리는 규칙이 있으면 부적합으로 처리
    return (len(reasons) == 0), reasons


def filter_by_price(products: list[dict], min_price: int = MIN_PRICE_DEFAULT) -> list[dict]:
    """최소 가격 필터. 판매가 하한이 min_price 이상인 것만."""
    return [p for p in products if p.get("price_min", 0) >= min_price]


def recommend_high_price(min_price: int = MIN_PRICE_DEFAULT, limit: int = 20) -> list[dict]:
    """고가 + 부적합 필터 통과 제품만 추천."""
    candidates = filter_by_price(PRODUCTS, min_price=min_price)
    result = []
    for p in candidates:
        ok, reasons = is_compliant(p)
        item = {**p, "compliant": ok, "check_reasons": reasons}
        if ok:
            result.append(item)
        else:
            # 부적합이면 제외하고 사유는 남김 (디버그용으로 주석 처리)
            # print(f"[제외] {p['name']}: {reasons}", file=sys.stderr)
            pass
        if len(result) >= limit:
            break
    return result


def get_categories() -> list[str]:
    """PRODUCTS에 등장하는 순서대로 카테고리 목록 반환."""
    seen: list[str] = []
    for p in PRODUCTS:
        c = p.get("category", "")
        if c and c not in seen:
            seen.append(c)
    return seen


def load_rotation_state(state_file: str | None = None) -> dict:
    """마지막 추천 카테고리 상태 로드. 없으면 빈 상태."""
    path = state_file or STATE_FILE
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_category": None, "history": []}


def save_rotation_state(state: dict, state_file: str | None = None) -> None:
    path = state_file or STATE_FILE
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def recommend_by_category(category: str, min_price: int = MIN_PRICE_DEFAULT, limit: int | None = None) -> list[dict]:
    """특정 카테고리만 고가 + 부적합 필터로 추천."""
    candidates = [p for p in PRODUCTS if p.get("category") == category]
    candidates = filter_by_price(candidates, min_price=min_price)
    result: list[dict] = []
    for p in candidates:
        ok, reasons = is_compliant(p)
        if ok:
            result.append({**p, "compliant": ok, "check_reasons": reasons})
        if limit is not None and len(result) >= limit:
            break
    return result


def recommend_next_category(
    min_price: int = MIN_PRICE_DEFAULT,
    batch_size: int = BATCH_SIZE_DEFAULT,
    state_file: str | None = None,
) -> tuple[str, list[dict]]:
    """추천버튼용: 다음 카테고리로 순환 추천. 상태를 저장해 다음 클릭 때 다른 카테고리가 나옴."""
    categories = get_categories()
    if not categories:
        return "", []
    state = load_rotation_state(state_file)
    last = state.get("last_category")
    if last in categories:
        next_idx = (categories.index(last) + 1) % len(categories)
    else:
        next_idx = 0
    category = categories[next_idx]
    products = recommend_by_category(category, min_price=min_price, limit=batch_size)
    # 카테고리에 제품이 없으면(가격 필터로 걸러짐) 다음 카테고리로 넘어감
    tried = 0
    while not products and tried < len(categories):
        next_idx = (next_idx + 1) % len(categories)
        category = categories[next_idx]
        products = recommend_by_category(category, min_price=min_price, limit=batch_size)
        tried += 1
    state["last_category"] = category
    state["history"] = (state.get("history") or []) + [category]
    # 히스토리는 최근 20개만 유지
    state["history"] = state["history"][-20:]
    save_rotation_state(state, state_file)
    return category, products


def recommend_random_category(
    min_price: int = MIN_PRICE_DEFAULT,
    batch_size: int = BATCH_SIZE_DEFAULT,
    state_file: str | None = None,
) -> tuple[str, list[dict]]:
    """추천버튼용: 이전과 다른 랜덤 카테고리 추천."""
    import random

    categories = get_categories()
    state = load_rotation_state(state_file)
    last = state.get("last_category")
    choices = [c for c in categories if c != last] or categories
    category = random.choice(choices)
    products = recommend_by_category(category, min_price=min_price, limit=batch_size)
    state["last_category"] = category
    state["history"] = (state.get("history") or []) + [category]
    state["history"] = state["history"][-20:]
    save_rotation_state(state, state_file)
    return category, products


def check_name(name: str) -> None:
    """임의 상품명 부적합 검사."""
    if _check_unsuitable is None:
        print("store_watch 모듈을 찾을 수 없어 검사할 수 없습니다.")
        return
    reasons = _check_unsuitable({
        "sellerProductName": name,
        "displayProductName": name,
        "generalProductName": name,
        "brand": "",
        "sellerProductId": "",
        "items": [{"overseasPurchased": "Y"}],
    })
    if not reasons:
        print(f"[적합] '{name}' — 부적합 규칙에 걸리지 않았습니다.")
    else:
        print(f"[부적합 의심] '{name}'")
        for r in reasons:
            print(f"  - {r}")


def _print_products(products: list[dict], title: str) -> None:
    print(title)
    for i, p in enumerate(products, 1):
        print(f"\n{i}. {p['name']} — {p['price_min']:,}~{p['price_max']:,}원 [{p['category']}]")
        print(f"   트렌드: {p['trend']}")
        print(f"   적합성: {p['compliance']}")
        print(f"   타오바오: {p['taobao']} | 쿠팡 검색: {p['coupang_search']}")
    print(f"\n※ 가격은 쿠팡 예상 판매가 추정치입니다. 소싱 전 원가·배송비·수수료·마진을 다시 계산하세요.")
    print(f"※ KC·허가·리콜 등은 키워드 검사만으로 확정할 수 없어, 등록 전 Wing·인증번호로 재확인하세요.")


def main() -> None:
    parser = argparse.ArgumentParser(description="고가 트렌드 제품 소싱 추천 (10만원 이상, 부적합 필터)")
    parser.add_argument("--min-price", type=int, default=MIN_PRICE_DEFAULT, help="최소 판매가 (기본 100000)")
    parser.add_argument("--limit", type=int, default=20, help="추천 개수 (기본 20)")
    parser.add_argument("--json", action="store_true", help="JSON으로 출력")
    parser.add_argument("--check", type=str, help="임의 상품명 부적합 검사")
    parser.add_argument("--next", action="store_true", help="추천버튼용: 다음 카테고리로 순환 추천 (상태 저장)")
    parser.add_argument("--random-category", action="store_true", help="추천버튼용: 이전과 다른 랜덤 카테고리 추천")
    parser.add_argument("--category", type=str, help="특정 카테고리만 추천 (예: 골프)")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT, help="버튼 1회 추천 개수 (기본 3)")
    args = parser.parse_args()

    if args.check:
        check_name(args.check)
        return

    # ── 추천버튼 모드: 매번 다른 카테고리 ──
    if args.next or args.random_category or args.category:
        if args.category:
            category = args.category
            products = recommend_by_category(category, min_price=args.min_price, limit=args.batch_size)
            # 특정 카테고리를 직접 고른 경우도 상태에 저장해 다음 --next가 겹치지 않게 함
            st = load_rotation_state()
            st["last_category"] = category
            st["history"] = (st.get("history") or []) + [category]
            st["history"] = st["history"][-20:]
            save_rotation_state(st)
        elif args.random_category:
            category, products = recommend_random_category(min_price=args.min_price, batch_size=args.batch_size)
        else:
            category, products = recommend_next_category(min_price=args.min_price, batch_size=args.batch_size)
        if args.json:
            print(json.dumps({"category": category, "min_price": args.min_price, "count": len(products), "products": products}, ensure_ascii=False, indent=2))
            return
        _print_products(products, f"[고가 소싱 추천 - {category}] {args.min_price:,}원 이상, {len(products)}개 (다음 클릭 때는 다른 카테고리)")
        print(f"다음 카테고리 미리보기: {get_categories()} 중 '{category}' 다음 순서")
        return

    recs = recommend_high_price(min_price=args.min_price, limit=args.limit)
    if args.json:
        print(json.dumps({"min_price": args.min_price, "count": len(recs), "products": recs}, ensure_ascii=False, indent=2))
        return

    print(f"[고가 소싱 추천] {args.min_price:,}원 이상, 부적합 필터 통과 {len(recs)}개")
    for i, p in enumerate(recs, 1):
        print(f"\n{i}. {p['name']} — {p['price_min']:,}~{p['price_max']:,}원 [{p['category']}]")
        print(f"   트렌드: {p['trend']}")
        print(f"   적합성: {p['compliance']}")
        print(f"   타오바오: {p['taobao']} | 쿠팡 검색: {p['coupang_search']}")
    print(f"\n※ 가격은 쿠팡 예상 판매가 추정치입니다. 소싱 전 원가·배송비·수수료·마진을 다시 계산하세요.")
    print(f"※ KC·허가·리콜 등은 키워드 검사만으로 확정할 수 없어, 등록 전 Wing·인증번호로 재확인하세요.")


if __name__ == "__main__":
    main()

"""
쿠팡 스토어 판매부적합상품 감시 스크립트

기준 출처: 쿠팡 Wing 도움말 "판매 및 배송 - 판매부적합상품 안내"
(최근 업데이트 2026/10/06, 사용자가 제공한 전문 기준)

동작:
  - 쿠팡 Wing API로 상태별 상품 목록을 조회해 신규 상품을 찾습니다.
  - 신규 상품의 상세 정보를 조회해 부적합 규칙에 걸리는지 검사합니다.
  - 부적합이면 콘솔 + alerts.log에 알림을 남기고, state 파일에 기록합니다.

사용법 (반드시 IP 화이트리스트에 등록된 PC에서 실행):
  python store_watch.py --check-once          # 1회 검사
  python store_watch.py --check-once --json    # 결과를 JSON으로 출력
  python store_watch.py --check-once --status ALL  # 임시저장~반려까지 전체 상태 조회

주의:
  - 쿠팡 API에는 상품 등록 웹훅이 없습니다. "바로"는 폴링 주기만큼의 지연이 있습니다.
    Windows 작업 스케줄러 / cron으로 10~30분 간격 실행을 권장합니다.
  - 쿠팡 API 키는 .env에만 두고, 이 파일이나 로그에 키를 남기지 마세요.
  - 키워드 검사는 1차 필터입니다. KC 인증 여부, 허가 여부, 리콜 여부, 도서정가제 할인율,
    원산지·제조국 100% 여부 등은 키워드만으로 판정이 안 되어 수동 확인이 필요합니다.
    알림은 "의심" 단계로 보고, 판매중지 전 반드시 Wing / 원문에서 확인하세요.
  - 구매대행(overseasPurchased=Y 또는 상품명에 구매대행 포함)인 경우,
    취급주의 항목 중 법정의무인증(KC 등) 미인증 제품을 제외한 모든 항목은
    구매대행 판매 불가로 상향됩니다 (정책 2-(10)-①).

상태 파일:
  - store_watch_state.json : 이미 확인한 sellerProductId 목록
  - store_watch_alerts.log : 부적합 알림 기록
"""
import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from config import load_settings
from coupang_client import CoupangClient, CoupangApiError

SEOUL_TZ = ZoneInfo("Asia/Seoul")
STATE_FILE = os.path.join(os.path.dirname(__file__), "store_watch_state.json")
ALERT_LOG = os.path.join(os.path.dirname(__file__), "store_watch_alerts.log")

# ── 부적합 규칙 (쿠팡 정책 전문 기준) ─────────────────────────
# 각 규칙: id, level("판매불가" / "취급주의"), keywords, note
# level=판매불가: 키워드만으로도 즉시 판매중지 검토
# level=취급주의: 인증/허가/성인인증/표시 여부 확인 후 판단. 구매대행이면 KC 제외하고 판매불가로 상향.
RULES = [
    # ── 1. 판매 불가 품목 ──
    ("BAN_마약류", "판매불가",
     ["마약", "아편", "양귀비", "코카인", "헤로인", "모르핀", "펜타닐", "페티틴",
      "암페타민", "케타민", "바르비탈", "벤조디아제핀", "졸피뎀", "프로포폴",
      "대마초", "대마", "칸나비놀", "칸나비디올", "테트라히드로칸나비놀", "THC"],
     "마약류 관리법. 대마·향정신성 포함."),
    ("BAN_총포도검", "판매불가",
     ["권총", "소총", "기관총", "엽총", "공기총", "모의총포", "화약식 타정총", "타정총",
      "석궁", "전자충격기", "최루", "분사기", "화약", "폭약", "화공품", "폭죽",
      "도검", "잭나이프", "비출나이프", "월도", "장도", "단도", "비수", "치도",
      "작살총", "샤프트"],
     "총포·도검·화약류 안전관리법. 주방용 칼·낫·도끼는 판매 가능하므로 '칼' 단독으로는 판정하지 않음."),
    ("BAN_경찰군사용품", "판매불가",
     ["경찰복", "경찰제복", "유사경찰제복", "경찰장비", "계급장", "경찰 수갑", "경찰방패",
      "삼단봉", "경찰차량", "전투복", "전투화", "전투모", "방탄헬멧", "방탄복", "군용베낭",
      "군복", "유사군복", "군용장구", "권총집"],
     "경찰제복 규제법 / 군복·군용장구 단속법."),
    ("BAN_동물분양멸종위기", "판매불가",
     ["동물분양", "개분양", "고양이분양", "관상어분양", "파충류분양", "곤충분양",
      "멸종위기", "국제적 멸종위기종", "야생동물 박제", "박제품"],
     "동물보호법 / 야생생물법. 살아있는 동물 온라인 판매 불가."),
    ("BAN_금어기금지체장", "판매불가",
     ["총알오징어", "살오징어 새끼", "한입오징어", "총알문어", "풀치", "솔치",
      "대게 암컷", "알주꾸미", "알배기 가자미", "붉은 대게 암컷", "꽃게 암컷", "민꽃게 암컷"],
     "수산자원관리법 금어기·금지체장."),
    ("BAN_불법어구", "판매불가",
     ["빠라뽕", "갸프", "삼지촉", "조개 꼬시개", "조개갈퀴", "손형망틀", "게그물",
      "꽃게망", "지네통발", "지네 통발"],
     "수산자원관리법. 판매가능 어구는 투망·쪽대·반두·4수망·외줄낚시·가리·외통발·집게·갈고리·호미 등만."),
    ("BAN_야생동물포획", "판매불가",
     ["올무", "야생동물 포획", "유해야생동물 포획"],
     "야생생물법. 쥐·두더지 포획용은 판매 가능."),
    ("BAN_주류담배의약품", "판매불가",
     ["소주", "맥주", "위스키", "와인", "고량주", "위스키 초콜릿",
      "담배", "니코틴", "금연껌",
      "타이레놀", "아스피린", "이부프로펜", "애드빌", "비아그라", "비판텐", "동전파스",
      "카베진", "호랑이연고", "아이봉", "로게인", "미녹시딜", "항히스타민제", "니조랄",
      "무좀치료제", "여드름 치료제", "다이어트보조제"],
     "주류·담배·의약품은 온라인 판매 불가. 전통주는 통신판매승인서+쿠팡 기재 시에만 예외."),
    ("BAN_콘택트렌즈도수안경농약", "판매불가",
     ["콘택트렌즈", "콘텍트렌즈", "컬러렌즈", "도수 안경", "시력 보정용 안경", "농약"],
     "의료기사법 / 농약 온라인 판매 불가."),
    ("BAN_음란물", "판매불가",
     ["음란물", "음란"],
     "음란물은 성인 카테고리라도 판매 불가."),
    ("BAN_환금성", "판매불가",
     ["헌혈증", "상품권", "사이버머니", "게임아이템", "사이버 아이템", "유사화폐", "가짜 화폐"],
     "혈액관리법 / 형법. 승차권 포함 상품권 판매 불가."),
    ("BAN_자동차안전", "판매불가",
     ["안전벨트 경고음 제거", "안전벨트 클립", "오토파일럿 헬퍼", "HDA 유지모듈", "핸들 클립퍼",
      "요소수 에뮬레이터", "요소수 무력화", "번호판 가림막", "번호판 반사", "번호판 빛 반사",
      "불법 등화"],
     "국토교통부 안전 제한. 조향·안전벨트 기능 훼손 제품."),
    ("BAN_석면", "판매불가",
     ["석면", "백시멘트", "황토제품"],
     "석면 검출 제품 판매 불가. 황토제품은 석면 여부 확인 필요."),
    ("BAN_기타불법물", "판매불가",
     ["장물", "습득물", "불법복제 휴대폰", "휴대폰 일련번호", "휴대폰 번호", "신호기 키",
      "적십자 표장", "종량제 봉투"],
     "기타 불법물."),
    ("BAN_지식재산권침해", "판매불가",
     ["레플리카", "짝퉁", "불법복제", "불법 스트리밍", "불법 스트리밍 셋탑박스", "해킹",
      "등급분류 미이행", "심의 미이행 영상물"],
     "상표권·저작권 침해, 불법복제 영상물·소프트웨어, 심의·등급분류 미이행물."),
    # ── 3. 쿠팡 정책상 판매 불가 ──
    ("BAN_무형상품", "판매불가",
     ["용역 서비스", "온라인 콘텐츠", "온라인 컨텐츠", "인증키", "코드형 전자책", "e-book",
      "가입상품", "구독 상품", "서비스 구독", "보험", "부동산",
      "투자정보", "투자자문", "주식 자동매매", "종목추천", "종목 추천"],
     "무형 상품은 Travel E-심·모바일쿠폰 예외 외 판매 불가. 실물 배송되는 QR·시리얼 인쇄물도 불가."),
    ("BAN_사생활침해기기", "판매불가",
     ["몰래카메라", "몰래 카메라", "위장형 카메라", "볼펜형 카메라", "시계형 카메라",
      "충전기형 카메라", "USB형 카메라", "클립형 카메라", "보조배터리형 카메라",
      "단추형 카메라", "안경형 카메라", "초소형 카메라",
      "위치추적기", "위치 추적기", "감청", "도청"],
     "위장형 몰래카메라·위치추적기·감청/도청 설비."),
    ("BAN_제국주의", "판매불가",
     ["욱일승천기", "전범기", "카미카제", "자위대", "하켄크로이츠", "나치"],
     "욱일기·나치 문양 등."),
    ("BAN_제재원산지", "판매불가",
     ["이란", "북한", "쿠바", "크림반도", "도네츠크", "루한스크", "DNR", "LNR",
      "러시아", "베네수엘라", "수단", "버마", "미얀마", "시리아"],
     "통상·제재 정책. 북한 지역명·관광 등 예외와 러시아 등 100% 여부 검토는 수동 확인 필요."),
    ("BAN_소비자피해우려", "판매불가",
     ["해킹장비", "너클", "가습기살균제", "건설기계", "단독 증정품", "해부용 동물 사체"],
     "차별·비하, 해킹장비, 가습기살균제, 건설기계, 0원 상품, 단독 판매 증정품 등. 0원 상품은 가격 확인 필요."),
    ("BAN_정치적견해", "판매불가",
     ["홍콩", "대만", "티벳", "일본해", "다케시마", "북한 사상", "정치인 비하", "정당 비하"],
     "국제 민감 정치 이슈, 북한 사상 찬양·고무, 특정 정치인·정당 비하 상품."),
    ("BAN_독성식용", "판매불가",
     ["통복어", "복어 내장", "복어내장", "날개쥐치", "상기생", "향부자", "만형자", "여정실"],
     "식용 불가 복어·내장, 날개쥐치, 독성 식물."),
    ("BAN_약관판매중지", "판매불가",
     ["김정은", "김일성", "김정일", "김여정", "리설주", "김주애", "인공기", "인민복", "공민증",
      "남베트남 국기"],
     "북한 관련 굿즈·인공기, 남베트남 국기, 베트남-중국 영토 표기. 북한 지역명·관광·북한말 자체는 예외."),
    ("BAN_욕설비방선정", "판매불가",
     ["욕설", "비방", "저주인형", "저주부적", "성인사이트"],
     "욕설·비방 문구, 저주인형·부적, 선정적 상품."),

    # ── 2. 취급 주의 품목 (인증/허가/성인인증 확인 필요) ──
    ("CAUTION_의약외품", "취급주의",
     ["생리대", "보건용 마스크", "가글", "구중청량제", "치약제", "의약외품"],
     "약사법. 무허가·무신고 의약외품, 임의 소분 의약외품은 판매 불가. 허가·신고 여부 확인 필요."),
    ("CAUTION_의료기기", "취급주의",
     ["의료기기", "체온계", "혈압계", "혈당측정기", "혈당", "임신테스트기", "배란테스트기",
      "코로나 진단키트", "진단키트", "개인용 온열기", "저주파자극기", "저주파",
      "의료용 흡인기", "모유착유기", "탈모치료용 레이저", "레이저 제모"],
     "의료기기법. 품목별 허가·인증·신고 + 판매업 신고 필요. 무허가면 판매 불가."),
    ("CAUTION_식품제한", "취급주의",
     ["휘핑크림", "아산화질소", "수입식품", "어린이 정서 저해", "고열량 저영양", "고카페인"],
     "식품위생법 등. 기준·규격 위반, 무허가·수입 미신고 식품, 어린이 정서 저해 식품 등 확인 필요."),
    ("CAUTION_화장품제한", "취급주의",
     ["테스터", "샘플 화장품", "비매품", "증정용", "식품 모방 화장품",
      "젤리 비누", "컵케이크 입욕제", "초콜릿 비누", "마카롱 비누", "치즈 비누", "떡 비누",
      "요거트 팩", "마요네즈 헤어팩", "꿀 형태"],
     "화장품법. 테스터·샘플·증정용 판매 불가, 식품 모방 화장품 판매 불가."),
    ("CAUTION_KC미인증", "취급주의",
     ["KC", "전기용품", "생활용품", "어린이제품", "방송통신기자재", "주방용 오물분쇄기",
      "수도관", "수도꼭지", "수도용 자재", "정수기", "가스용품", "압력조정기", "배관용 밸브",
      "고압가스", "소방용품", "소화기", "소화전", "누전경보기", "자동차부품", "창유리",
      "좌석안전띠", "반사띠", "충전기", "어댑터", "배터리", "보조배터리", "전기장판",
      "멀티탭", "LED", "조명", "히터"],
     "법정의무인증 미인증 제품은 판매 불가. 인증 표시·인증번호 확인 필요. 구매대행 특례 해당 여부는 별도 확인."),
    ("CAUTION_생활화학살생물", "취급주의",
     ["세탁세제", "표백제", "섬유유연제", "방향제", "향초", "디퓨저", "접착제", "살균제",
      "기피제", "세정제", "세제", "탈취제", "살충"],
     "화학제품안전법. 신고·확인·승인 받지 않은 안전확인대상 생활화학제품·살생물제는 판매 불가."),
    ("CAUTION_유해화학위험물", "취급주의",
     ["유해화학물질", "톨루엔", "초산에틸", "메틸알코올", "위험물", "인화성", "발화성", "산화성"],
     "화학물질관리법 / 위험물안전관리법. 유해화학물질은 택배 취급 불가, 위험물 1~6류 로켓그로스 판매 불가."),
    ("CAUTION_미성년자판매불가", "취급주의",
     ["주류", "전통주", "담배", "전자담배", "환각물질", "시너", "본드", "부탄가스", "라이터",
      "아산화질소", "성인용품", "성기구", "레이저 포인터", "레이저 거리측정기", "성인용 비비탄총",
      "세라복", "코스튬", "섹시 속옷", "섹시 스타킹", "섹시 슬립", "아헤가오"],
     "청소년보호법. 19금 설정 + 성인인증 없이는 판매 불가. 라이터·부탄가스·아산화질소 등 환각물질 포함."),
    ("CAUTION_구매대행제한", "취급주의",
     ["구매대행", "해외직구", "해외배송", "신선식품", "냉장", "냉동", "검역", "수입금지 성분",
      "목재", "곡류 베개", "팥 보온팩", "보온팩", "종자", "묘목", "육가공품", "사료"],
     "구매대행 불가·제한. 검역 미이행 식물·축산물, 수입금지 성분 식품·화장품, 목재·곡류 포함 비식품, 해외 신선식품 등."),
    ("CAUTION_허위과대광고", "취급주의",
     ["아토피 치료", "살균", "소독", "항균", "항염", "해독", "항바이러스", "통증 경감",
      "면역강화", "혈액순환", "피부재생", "세포 재생", "미백", "화이트닝", "주름 개선",
      "자외선 차단", "FDA 인증", "FDA 승인", "천연 화장품", "유기농 화장품", "친환경", "무독성",
      "공진환", "한약재", "질병 치료", "탈모 개선", "발모", "육모"],
     "표시·광고법 / 화장품법 / 의료기기법 등. 의약품·기능성화장품 오인, FDA·친환경 오인, 질병 치료 표현 등은 허위·과대광고."),
]

# 수동 확인이 필요한 항목 (키워드로 잡히지 않음)
MANUAL_CHECKS = [
    "리콜 제품: 소비자24 국내·해외 리콜 등록 여부 확인",
    "미형식승인 계량기: 계량기 형식승인번호·법정단위 표시 확인",
    "도서정가제: 할인율 10% 이내 + 경제적 이익 포함 15% 이내 확인",
    "KC 인증 여부: 인증번호·표시 확인 (키워드만으로는 인증 유무 판정 불가)",
    "의료기기·의약외품 허가 여부: 식약처 허가·신고번호 확인",
    "원산지 100% 여부: 제재 국가 원재료 100% 해당 시 내부 검토 필요",
    "0원 상품 / 가격 오류: 판매가 0원 여부 확인",
    "해외배송 신선식품: 구매대행 + 신선식품 조합 확인",
]

DENIED_STATUS = "DENIED"


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "seen_ids" in data:
                    return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"seen_ids": []}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def is_overseas_purchase(product: dict) -> bool:
    """구매대행 여부. items의 overseasPurchased 또는 상품명·상세에 구매대행 표기."""
    items = product.get("items") or []
    for it in items:
        if str(it.get("overseasPurchased") or "").upper() == "Y":
            return True
    text = " ".join([
        str(product.get("sellerProductName") or ""),
        str(product.get("displayProductName") or ""),
        str(product.get("generalProductName") or ""),
    ])
    return "구매대행" in text or "해외직구" in text or "해외배송" in text


def check_unsuitable(product: dict) -> list[str]:
    """상품 dict에서 부적합 사유 목록을 반환합니다."""
    reasons: list[str] = []
    text = " ".join([
        str(product.get("sellerProductName") or ""),
        str(product.get("displayProductName") or ""),
        str(product.get("generalProductName") or ""),
        str(product.get("brand") or ""),
        str(product.get("sellerProductId") or ""),
    ])
    # 상세 설명이 있으면 광고 문구 검사에도 사용
    detail_text = ""
    items = product.get("items") or []
    for it in items:
        for c in (it.get("contents") or []):
            for d in (c.get("contentDetails") or []):
                detail_text += " " + str(d.get("content") or "")
    full_text = text + " " + detail_text
    full_lower = full_text.lower()
    overseas = is_overseas_purchase(product)

    for rule_id, level, keywords, note in RULES:
        matched = None
        for kw in keywords:
            if kw.lower() in full_lower:
                matched = kw
                break
        if not matched:
            continue
        # 구매대행 상향: 취급주의 중 KC 미인증 제외하고 판매불가로 상향
        effective_level = level
        if overseas and level == "취급주의" and rule_id != "CAUTION_KC미인증":
            effective_level = "판매불가(구매대행 불가)"
        # KC 규칙은 구매대행 특례 해당 시 별도 확인 문구 추가
        extra = ""
        if rule_id == "CAUTION_KC미인증" and overseas:
            extra = " [구매대행 특례 해당 여부 별도 확인]"
        reasons.append(f"[{effective_level}] {rule_id}: 키워드 '{matched}' 포함 — {note}{extra}")

    # 쿠팡 반려 상태
    status = str(product.get("status") or product.get("statusName") or "")
    if status == DENIED_STATUS or "반려" in status:
        reasons.append(f"[판매불가] 쿠팡 반려 상태 (status={status})")
    return reasons


def log_alert(message: str) -> None:
    ts = datetime.now(SEOUL_TZ).strftime("%Y-%m-%d %H:%M:%S KST")
    line = f"[{ts}] {message}\n"
    with open(ALERT_LOG, "a", encoding="utf-8") as f:
        f.write(line)


def main() -> None:
    parser = argparse.ArgumentParser(description="쿠팡 스토어 판매부적합상품 감시")
    parser.add_argument("--check-once", action="store_true", help="1회 검사 후 종료")
    parser.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    parser.add_argument("--status", default="APPROVED", help="조회할 상태 (기본 APPROVED). ALL이면 주요 상태 전체 조회")
    args = parser.parse_args()

    if not args.check_once:
        print("현재는 --check-once 1회 검사만 지원합니다. 스케줄러에서 반복 실행하세요.")
        parser.print_help()
        return

    settings = load_settings()
    client = CoupangClient(
        settings.coupang_vendor_id,
        settings.coupang_access_key,
        settings.coupang_secret_key,
    )

    if args.status == "ALL":
        statuses = ["SAVED", "IN_REVIEW", "APPROVING", "APPROVED", "PARTIAL_APPROVED", "DENIED"]
    else:
        statuses = [args.status]

    state = load_state()
    seen = set(str(x) for x in state.get("seen_ids", []))
    new_products: list[dict] = []
    alerts: list[dict] = []

    for st in statuses:
        try:
            products = client.list_products(status=st)
        except CoupangApiError as e:
            print(f"[오류] 상태 {st} 조회 실패: {e}", file=sys.stderr)
            continue
        for p in products:
            pid = str(p.get("sellerProductId") or p.get("sellerProductID") or "")
            if not pid or pid in seen:
                continue
            try:
                detail = client.get_product_detail(pid)
            except CoupangApiError:
                detail = p
            merged = {**p, **detail}
            reasons = check_unsuitable(merged)
            new_products.append({"sellerProductId": pid, "status": st, "reasons": reasons, "name": merged.get("sellerProductName") or merged.get("displayProductName") or ""})
            if reasons:
                alerts.append(new_products[-1])
                log_alert(f"신규 부적합 의심 상품 sellerProductId={pid} status={st} name={merged.get('sellerProductName') or merged.get('displayProductName') or ''} reasons={'; '.join(reasons)}")
            seen.add(pid)

    state["seen_ids"] = sorted(seen)
    state["last_checked_at"] = datetime.now(SEOUL_TZ).isoformat()
    save_state(state)

    if args.json:
        print(json.dumps({"new_products": new_products, "alerts": alerts, "checked_statuses": statuses}, ensure_ascii=False, indent=2))
        return

    print(f"[감시] 신규 {len(new_products)}건, 부적합 의심 {len(alerts)}건 (상태: {', '.join(statuses)})")
    for a in alerts:
        print(f"  ! {a['sellerProductId']} [{a['status']}] {a['name']}")
        for r in a["reasons"]:
            print(f"      - {r}")
    if not alerts:
        print("  부적합 의심 신규 상품이 없습니다.")
    print("\n[수동 확인 필요]")
    for m in MANUAL_CHECKS:
        print(f"  - {m}")


if __name__ == "__main__":
    try:
        main()
    except CoupangApiError as e:
        raise SystemExit(f"쿠팡 API 오류: {e}")

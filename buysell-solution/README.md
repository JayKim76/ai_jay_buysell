# 구매대행 통합 관리 솔루션 — 1단계 + 2단계

바이셀 유통용. 기존 도구(store_watch 판매부적합 감시, 쿠팡 Wing API 연동)를
하나의 SQLite DB와 웹 대시보드로 통합했습니다.
1단계: 주문·배송 + 경보 통합 / 2단계: 소싱·상품 등록 자동화 연결.

## 무엇이 되나

- **주문 수집**: 쿠팡 발주서 API(최근 7일) 동기화. API가 막히면 Wing 주문 CSV 임포트로 대체 가능
- **단계 관리**: 주문 접수 → 타오바오 발주 → 중국 내 배송 → 배대지 입고 → 국제 출고 → 국내 배송 → 배송 완료
  - 운송장(타오바오 주문번호/중국/국제/국내)을 입력하면 단계가 자동으로 전진합니다
- **지연 경보** (대시보드 + 아침 요약):
  - 배송지시 24시간 경과 미발주 / 출고예정 임박·경과(긴급) / 주문 5일 경과 지연
  - 미답변 문의 24시간 경과 / 회신 기한(72시간) 임박·경과(긴급)
  - 판매부적합 의심(기존 34개 규칙 이관) / 판매 불가 상태 상품
  - 조건이 해소되면 경보는 자동 해소됩니다
- **아침 요약**: 매일 아침 경보 요약을 텍스트로 생성 (reports/ 저장)
- 개인정보: 구매자명은 마스킹 저장, 개인통관고유부호는 저장하지 않습니다

## 2단계 — 소싱·등록 (대시보드 '소싱·등록' 화면)

타오바오 링크 + 원제목(중국어) + 구매가 + 무게를 넣으면 한 번에 처리됩니다.

1. **상품명 생성**: 기존 taobao_title_optimizer 이관. .env에 GEMINI_API_KEY가 있으면 AI 생성, 없으면 규칙 생성 (상품명 3종 + 검색태그 10개)
2. **마진 자동 계산**: 구매가·중국 배송비(환율 환산) + 국제배송비(무게×단가 또는 직접 입력) + 관부가세 + 쿠팡 수수료를 반영해 목표 마진으로 판매가 역산. 판매가를 직접 넣으면 그 값으로 마진 재계산, 목표 미달이면 '마진 부족' 상태
   - 기본 가정 (후보별 수정 가능): 환율 200원/CNY, 국제배송 9,000원/kg, 관부가세율 10%, 수수료율 11%, 목표 마진 20%
3. **등록 전 부적합 사전 검사**: 원제목+생성명을 합쳐 34개 규칙 검사. 판매불가면 '등록 불가'로 초안 생성 자체를 차단, 취급주의면 '확인 필요' 표시
4. **등록 초안**: 통과 건만 product_uploader용 JSON을 drafts/에 생성 (카테고리 코드·이미지·고시정보는 채워 넣어야 하며, 등록 전 --dry-run 필수)

CLI로도 됩니다: `python app.py add-candidate --url ... --title ... --price-cny 100 --weight-kg 1.0`,
`python app.py candidates`, `python app.py draft 후보ID`

테스트: `python tests\test_phase1.py`, `python tests\test_phase2.py`

## 실행 환경 (중요)

쿠팡 API는 IP 화이트리스트가 필요합니다. **Wing에 등록한 PC에서 실행**하세요.
대시보드·경보·CSV 임포트는 API 없이도 동작합니다.

## 설치 (Windows PC)

```bat
pip install -r requirements.txt
copy .env.example .env   :: .env에 쿠팡 키 3개 입력 (Wing 발급 값)
python app.py init
```

## 사용법

```bat
python app.py serve            :: 대시보드 http://127.0.0.1:8765
python app.py sync-orders      :: 쿠팡 주문 동기화
python app.py sync-products    :: 상품 동기화 + 부적합 감시
python app.py morning          :: 아침 경보 요약
python app.py import-csv 주문.csv
python app.py demo             :: 동작 확인용 데모 (확인 후 clear-demo)
python app.py clear-demo
```

또는 동봉된 .bat 파일을 더블클릭하세요.

- `run_dashboard.bat` — 대시보드
- `run_sync.bat` — 주문+상품 동기화 후 경보 평가
- `run_morning.bat` — 아침 요약 (작업 스케줄러 등록용)

## 자동 실행 (선택)

`setup_tasks.ps1`을 PowerShell에서 실행하면 작업 스케줄러에 2건이 등록됩니다.

- 30분마다 동기화+경보 평가 (기존 store_watch와 같은 주기)
- 매일 09:00 아침 요약 생성

## 테스트

```bat
python tests\test_phase1.py
```

## 아직 안 정해진 것

- 배대지 운송장 조회 방식: 현재는 대시보드 수동 입력이 기본입니다.
  배대지 업체가 조회 연동(엑셀/사이트)을 제공하면 모듈 2에 붙입니다.
- 11번가 주문: 1단계는 쿠팡만 연결했습니다. 채널 컬럼이 있어 확장 가능합니다.

## 구조

```
app.py                  실행 진입점 (CLI)
buysell/db.py           통합 DB 스키마 (상품·주문·배송·비용·경보·문의)
buysell/coupang.py      쿠팡 API (상품 + 발주서, 기존 클라이언트 확장)
buysell/unsuitable.py   판매부적합 34개 규칙 (store_watch에서 이관)
buysell/sync.py         동기화·CSV 임포트·단계/운송장 갱신
buysell/alerts.py       경보 엔진 + 아침 요약
buysell/dashboard.py    웹 대시보드 (표준 라이브러리만 사용)
data/buysell.db         SQLite DB (자동 생성)
```

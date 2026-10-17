# PC에서 30분마다 쿠팡 부적합상품 감시하기

## 준비물

- `threads-coupang-poster` 폴더가 PC에 있어야 함 (기존 `E:\AI\threads-coupang-poster` 사용)
- `.env`에 쿠팡 Wing API 키가 입력되어 있어야 함 (Vendor ID / Access Key / Secret Key)
- 쿠팡 Wing에 PC 공인 IP가 등록되어 있어야 함 (바뀌면 재등록)
- Python 3.10 이상 + `pip install -r requirements.txt` 완료

## 1. 새 파일 3개 복사

이 폴더에 새로 생긴 파일 3개를 PC 폴더에 넣으세요.

- `store_watch.py` (감시 본체, 기준 34개)
- `UNSUITABLE_CRITERIA.md` (기준 문서)
- `run_store_watch.bat` (30분 실행용)
- `setup_store_watch_task.ps1` (스케줄러 등록용)

이미 `threads-coupang-poster`를 PC에서 쓰고 있다면, 위 4개 파일만 덮어쓰기 / 새로 복사하면 됩니다.

## 2. 1회 테스트 (스케줄러 등록 전)

```bat
cd E:\AI\threads-coupang-poster
python store_watch.py --check-once --status ALL
```

- `신규 0건, 부적합 의심 0건`이면 정상 동작입니다.
- 처음 실행하면 기존 상품 전체가 신규로 잡혀서 알림이 많이 뜰 수 있습니다. 1회 실행으로 기준점이 저장되고, 그 다음부터는 진짜 신규만 알립니다.
- 쿠팡 403 / 차단 오류가 뜨면 Wing에서 IP 재등록이 필요합니다.

## 3. 30분마다 자동 실행 등록

PowerShell을 **관리자 권한**으로 열고:

```powershell
cd E:\AI\threads-coupang-poster
.\setup_store_watch_task.ps1
```

- 작업 이름: `Coupang Store Watch - 30min`
- 주기: 30분마다, 지속 기간 10년, 실행 시간 제한 10분
- 확인: 작업 스케줄러에서 이름으로 검색
- 중지: `Unregister-ScheduledTask -TaskName 'Coupang Store Watch - 30min' -Confirm:$false`

수동으로 등록하려면 작업 스케줄러 → 작업 만들기 → 트리거 30분 반복 → 동작 `run_store_watch.bat` 실행 → 시작 위치 `E:\AI\threads-coupang-poster` 로 해도 됩니다.

## 4. 알림이 오는 방식

- **PC 알림**: 부적합 의심 신규 상품이 있으면 Windows 토스트 알림(또는 메시지 박스)이 뜹니다. `store_watch_alerts.log`에도 기록됩니다.
- **이 채팅 알림**: PC에서 돌리는 스크립트는 이 채팅으로 직접 메시지를 못 보냅니다. 알림이 뜨면 `store_watch_alerts.log`의 마지막 줄을 이 채팅에 붙여넣어 주세요. 그러면 제가 어떤 기준으로 걸렸는지, 판매중지할지 말지 정리해 드립니다.
- **로그 확인**: `store_watch_run.log`에 매 실행 기록, `store_watch_alerts.log`에 알림만 따로 남습니다.

## 5. 알림이 떴을 때 할 일

1. `store_watch_alerts.log` 마지막 줄 확인 (어떤 규칙에 걸렸는지)
2. `UNSUITABLE_CRITERIA.md`에서 해당 항목의 확인 사항 대조 (KC 인증번호, 허가번호, 리콜 여부 등)
3. Wing에서 해당 상품 확인 후 판매중지 / 수정 / 유지 결정
4. 오탐이면 알려주세요. 규칙에서 제외하거나 키워드를 다듬습니다.

## 주의

- 쿠팡 API에는 웹훅이 없어서 30분 간격 폴링입니다. 업로드 직후 바로가 아니라 최대 30분 뒤에 알림이 옵니다.
- PC가 꺼져 있거나 절전 상태면 그 시간대는 건너뜁니다. 켜져 있을 때만 동작합니다.
- IP가 바뀌면 API가 차단되어 체크가 안 됩니다. 그때는 Wing에서 IP 재등록 후 다시 테스트하세요.
- 처음 1회는 기준점 저장이 목적이라 알림이 많아도 정상입니다. 2회째부터 신규만 뜹니다.

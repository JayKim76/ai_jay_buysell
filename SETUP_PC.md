# PC 설치 가이드 (Windows) — threads-coupang-poster

프로그램을 실행할 PC에서 따라 하세요. 전체 소요 시간 약 20~30분.

## 0. 준비물 체크

- [ ] 압축 파일 다운로드 (다운로드 링크는 **10월 11일 만료**)
- [ ] 쿠팡 Wing 오픈 API 키 (업체코드, Access Key, Secret Key)
- [ ] Threads 장기 액세스 토큰 + 숫자 형태 사용자 ID (아래 3번 참고)
- [ ] Gemini API 키 ([Google AI Studio](https://aistudio.google.com) 무료 발급)
- [ ] Python 3.10 이상 ([python.org](https://www.python.org/downloads/)에서 설치, 설치 시 "Add python.exe to PATH" 체크)

## 1. 압축 해제

다운로드한 `threads-coupang-poster.zip`을 `E:\AI` 아래에 압축 해제합니다.
→ `E:\AI\threads-coupang-poster` 폴더가 생겨야 합니다.

## 2. 쿠팡 Wing에 실행 PC의 IP 등록 (필수)

쿠팡 Wing API는 **미리 등록한 IP에서만 호출**할 수 있습니다. 등록하지 않으면 차단됩니다.

1. [내 공인 IP 확인](https://www.whatismyip.com) — 예: `203.0.113.45`
2. 쿠팡 Wing 로그인 → **API 연동** 메뉴
3. 위 IP를 등록/추가 후 저장

> ⚠️ 집 인터넷은 IP가 바뀔 수 있습니다. 나중에 API가 막히면 IP를 다시 등록하세요.
> 고정 IP가 있는 서버에서 실행하면 이 걱정이 없습니다.

## 3. Threads API 토큰 발급

1. [Meta 개발자](https://developers.facebook.com) → 앱 만들기 (유형: "기타")
2. 앱에 **Threads** 유스케이스(제품) 추가
3. 앱 설정 → 기본 설정에서 **Threads 앱 ID**와 **Threads 앱 시크릿** 확인
   (일반 '앱 ID/앱 시크릿'이 아니라 **Threads 전용** 항목입니다. 토큰 교환에는 반드시 Threads 전용 값을 사용하세요)
4. Threads → 시작하기에서 본인 계정을 **테스터**로 등록 후,
   휴대폰 Threads 앱 (설정 → 웹사이트 권한)에서 테스터 초대 수락
5. 아래 URL을 브라우저에 열어 승인하고, 리디렉션된 주소의 `code` 값을 복사
   (redirect_uri는 앱 설정에 등록된 주소여야 합니다)

```
https://threads.net/oauth/authorize?client_id=Threads앱ID&redirect_uri=리디렉션주소&scope=threads_basic,threads_content_publish&response_type=code
```

6. 단기 토큰으로 교환 (1시간 유효):

```bash
curl -X POST "https://graph.threads.net/oauth/access_token" ^
  -d "client_id=Threads앱ID" -d "client_secret=Threads앱시크릿" ^
  -d "grant_type=authorization_code" -d "redirect_uri=리디렉션주소" -d "code=위에서_복사한_code"
```

7. 장기 토큰(60일)으로 교환:

```bash
curl "https://graph.threads.net/access_token?grant_type=th_exchange_token&client_secret=Threads앱시크릿&access_token=단기토큰"
```

8. 숫자 형태 사용자 ID 확인:

```bash
curl "https://graph.threads.net/v1.0/me?fields=id,username&access_token=장기토큰"
```

> 토큰은 60일마다 만료되지만, 프로그램이 실행 시 자동으로 갱신을 시도합니다.

## 4. .env 파일 작성

```bat
cd E:\AI\threads-coupang-poster
pip install -r requirements.txt
copy .env.example .env
```

`.env` 파일을 메모장으로 열어 발급받은 키들을 입력합니다.

## 5. 테스트 실행 (발행 없이 미리보기)

```bat
python main.py --dry-run
```

- 쿠팡 상품이 조회되고, AI 소개글이 출력되면 성공입니다.
- 특정 상품으로 테스트: `python main.py --product-id 상품ID --dry-run`

## 6. 실제 발행

```bat
python main.py
```

## 7. 매일 자동 실행 (Windows 작업 스케줄러)

1. 작업 스케줄러 → "작업 만들기"
2. 트리거: 매일 원하는 시간 (예: 오전 10시)
3. 동작: 프로그램 시작
   - 프로그램: `C:\Users\사용자명\AppData\Local\Programs\Python\Python3xx\python.exe`
   - 인수 추가: `main.py`
   - 시작 위치: `E:\AI\threads-coupang-poster`
4. 조건: "컴퓨터의 AC 전원이 켜져 있는 경우에만 작업 시작" 필요시 해제

> 계정 안전을 위해 하루 1~3회 이하 운영을 권장합니다.

## 문제 해결

| 증상 | 원인 / 해결 |
|------|------------|
| 쿠팡 API 403/차단 | 2번의 IP 등록이 안 됐거나 IP가 바뀜. Wing에서 IP 재등록 |
| Threads 인증 오류 | 토큰 만료 또는 권한 부족. 3번부터 다시 발급 |
| Gemini 오류 | API 키 확인, 무료 할당량 초과 여부 확인 |
| 필수 환경 변수 오류 | `.env` 파일명·위치 확인 (`E:\AI\threads-coupang-poster\.env`) |

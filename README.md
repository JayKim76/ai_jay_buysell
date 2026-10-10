# 쿠팡 상품 → 스레드 자동 포스팅 프로그램

쿠팡에서 판매 중인 상품 중 하나를 **랜덤으로 선정**하고,
Gemini AI가 소개글을 자동 생성한 뒤, **스레드에 이미지와 함께 업로드**합니다.

## 동작 흐름

1. 쿠팡 Wing 오픈 API(HMAC 서명)로 판매 중인 상품 목록 조회 → 1개 랜덤 선정
2. 상품 상세 조회 (상품명, 브랜드, 가격, 대표 이미지)
3. Gemini API로 스레드 소개글 자동 생성 (스타일은 `.env`의 `POST_STYLE`로 조정)
4. Threads API로 발행 (컨테이너 생성 → 30초 대기 → 발행)

## 준비물

### 1. 쿠팡 Wing 오픈 API 키
- 쿠팡 Wing 로그인 → **API 연동** 메뉴에서 Access Key / Secret Key 발급
- 업체코드(Vendor ID)는 `A`로 시작 (Wing 로그인 시 확인 가능)
- `.env`의 `COUPANG_VENDOR_ID`, `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY`에 입력

### 2. Threads API
- [Meta 개발자](https://developers.facebook.com)에서 앱 생성 → **Threads use case** 추가
- 권한: `threads_basic`, `threads_content_publish`
- OAuth로 **장기 액세스 토큰**(60일 유효) 발급
- `THREADS_USER_ID`: 숫자 형태의 Threads 사용자 ID (`@핸들`이 아님)
  - `GET https://graph.threads.net/v1.0/me?fields=id&access_token=토큰` 으로 확인 가능
- 토큰이 만료되면 프로그램이 자동으로 갱신을 시도합니다

### 3. Gemini API 키
- [Google AI Studio](https://aistudio.google.com)에서 발급 (무료)

## 설치 및 실행

```bash
cd threads-coupang-poster
pip install -r requirements.txt

# .env 작성
cp .env.example .env
# → .env 파일을 열어 키 입력

# 미리보기 (발행 없이 상품 선정 + 소개글 생성만)
python main.py --dry-run

# 특정 상품으로 테스트
python main.py --product-id 12345678 --dry-run

# 실제 발행
python main.py
```

## 자동 실행 (매일 정해진 시간에 포스팅)

리눅스/macOS cron 예시 — 매일 오전 10시 실행:

```
0 10 * * * cd /path/to/threads-coupang-poster && /usr/bin/python3 main.py >> run.log 2>&1
```

> 계정 안전을 위해 하루 1~3회 이하로 운영하는 것을 권장합니다.
> (Threads API 한도: 24시간 기준 포스트 250개)

## 파일 구성

| 파일 | 역할 |
|------|------|
| `main.py` | 전체 흐름 조율 (랜덤 선정 → 생성 → 발행) |
| `product_uploader.py` | 쿠팡 상품 원클릭 등록 CLI |
| `product_example.json` | 상품등록용 JSON 예제 |
| `config.py` | `.env` 로드 및 검증 (판매자 배송/반품 프로필 포함) |
| `coupang_client.py` | 쿠팡 Wing API (HMAC 인증, 상품 조회/등록/승인, 카테고리 추천) |
| `ai_writer.py` | Gemini 소개글 생성 |
| `threads_client.py` | Threads API (토큰 갱신, 2단계 발행) |
| `.env.example` | 환경 변수 템플릿 |

## 쿠팡 상품 원클릭 등록 (product_uploader.py)

상품 정보 JSON을 작성해 쿠팡에 바로 등록합니다.

```bash
# 1. 예제 복사 후 실제 값 입력
cp product_example.json my-product.json
# → my-product.json 편집 (상품명, 가격, 이미지 URL, 고시정보 등)

# 2. 카테고리 추천 (상품명으로 쿠팡 카테고리 코드 추천)
python product_uploader.py my-product.json --recommend-category
# → 추천 코드를 my-product.json의 displayCategoryCode에 입력

# 3. 카테고리 필수 고시정보/구매옵션 확인
python product_uploader.py my-product.json --category-meta
# → 필수 notices(고시정보), attributes(구매옵션)를 JSON에 반영

# 4. 미리보기 (API 호출 없이 페이로드 검증)
python product_uploader.py my-product.json --dry-run

# 5. 실제 등록 (확인 프롬프트 표시)
python product_uploader.py my-product.json

# 6. 등록 + 승인요청까지 한 번에
python product_uploader.py my-product.json --yes --request-approval
```

> ⚠️ 쿠팡 API는 테스트 환경이 없습니다. 등록 호출 즉시 실제 판매 환경에 반영됩니다.
> 반드시 `--dry-run`으로 먼저 확인하고, IP 화이트리스트에 등록된 PC에서 실행하세요.
> 등록 후 카테고리는 변경할 수 없습니다.

## 참고

- 스레드 소개글은 480자 이내로 자동 조정됩니다 (Threads 제한 500자).
- 대표 이미지가 없는 상품은 텍스트만 발행됩니다.
- 소개글 생성 AI를 OpenAI/Claude로 바꾸고 싶으면 `ai_writer.py`의
  `generate_post_text` 함수만 교체하면 됩니다.

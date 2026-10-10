"""
설정 로드 모듈
.env 파일에서 환경 변수를 읽어 Settings 객체로 반환합니다.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

REQUIRED_VARS = [
    "COUPANG_VENDOR_ID",
    "COUPANG_ACCESS_KEY",
    "COUPANG_SECRET_KEY",
    "THREADS_USER_ID",
    "THREADS_ACCESS_TOKEN",
    "GEMINI_API_KEY",
]


@dataclass
class Settings:
    coupang_vendor_id: str
    coupang_access_key: str
    coupang_secret_key: str
    threads_user_id: str
    threads_access_token: str
    gemini_api_key: str
    gemini_model: str = "gemini-2.0-flash"
    post_style: str = "20대 여성, 친근하고 공감 가는 말투, 반말 위주, 짧고 임팩트 있게"
    dry_run: bool = False


def load_settings() -> Settings:
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        raise SystemExit(
            "필수 환경 변수가 설정되지 않았습니다: " + ", ".join(missing)
            + "\n.env 파일을 확인하세요. (.env.example 참고)"
        )
    return Settings(
        coupang_vendor_id=os.environ["COUPANG_VENDOR_ID"],
        coupang_access_key=os.environ["COUPANG_ACCESS_KEY"],
        coupang_secret_key=os.environ["COUPANG_SECRET_KEY"],
        threads_user_id=os.environ["THREADS_USER_ID"],
        threads_access_token=os.environ["THREADS_ACCESS_TOKEN"],
        gemini_api_key=os.environ["GEMINI_API_KEY"],
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        post_style=os.getenv(
            "POST_STYLE",
            "20대 여성, 친근하고 공감 가는 말투, 반말 위주, 짧고 임팩트 있게",
        ),
    )

"""시간 처리 (KST 고정). DB에는 ISO 문자열로 저장한다."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SEOUL = ZoneInfo("Asia/Seoul")


def now() -> datetime:
    return datetime.now(SEOUL)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def parse_iso(value):
    """ISO 문자열/날짜를 KST aware datetime으로. 실패하면 None."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SEOUL)
    return dt.astimezone(SEOUL)


def hours_between(start, end=None) -> float | None:
    s = parse_iso(start)
    e = parse_iso(end) if end else now()
    if s is None or e is None:
        return None
    return (e - s).total_seconds() / 3600.0


def mask_name(name: str | None) -> str:
    """구매자명 마스킹: 첫 글자만 남긴다."""
    if not name:
        return ""
    name = str(name).strip()
    if len(name) <= 1:
        return name
    return name[0] + "*" * (len(name) - 1)

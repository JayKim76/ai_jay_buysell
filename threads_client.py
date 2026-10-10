"""
Threads API 클라이언트 (2단계 발행: 컨테이너 생성 → 발행)
필요 권한: threads_basic, threads_content_publish
"""
import time

import requests

GRAPH_BASE = "https://graph.threads.net/v1.0"


class ThreadsApiError(RuntimeError):
    pass


class ThreadsAuthError(ThreadsApiError):
    pass


class ThreadsClient:
    def __init__(self, user_id: str, access_token: str):
        self.user_id = user_id
        self.access_token = access_token

    def _post(self, endpoint: str, data: dict) -> dict:
        data = {**data, "access_token": self.access_token}
        try:
            resp = requests.post(f"{GRAPH_BASE}/{endpoint}", data=data, timeout=30)
        except requests.RequestException as e:
            raise ThreadsApiError(f"Threads API 요청 실패: {e}")
        try:
            body = resp.json()
        except ValueError:
            raise ThreadsApiError(f"Threads API 응답 파싱 실패 (HTTP {resp.status_code})")
        if "error" in body:
            err = body["error"]
            msg = err.get("message", err)
            if err.get("code") == 190 or "access token" in str(msg).lower():
                raise ThreadsAuthError(f"Threads 인증 오류: {msg}")
            raise ThreadsApiError(f"Threads API 오류: {msg}")
        return body

    def validate_token(self) -> bool:
        """토큰 유효성을 확인합니다."""
        try:
            resp = requests.get(
                f"{GRAPH_BASE}/me",
                params={"fields": "id", "access_token": self.access_token},
                timeout=15,
            )
            return "id" in resp.json()
        except Exception:
            return False

    def refresh_token(self) -> str:
        """장기 토큰을 갱신합니다 (60일 유효). 갱신된 토큰을 반환합니다."""
        try:
            resp = requests.get(
                f"{GRAPH_BASE}/refresh_access_token",
                params={
                    "grant_type": "th_refresh_token",
                    "access_token": self.access_token,
                },
                timeout=30,
            )
            body = resp.json()
        except Exception as e:
            raise ThreadsAuthError(f"토큰 갱신 실패: {e}")
        new_token = body.get("access_token")
        if not new_token:
            raise ThreadsAuthError(f"토큰 갱신 실패: {body}")
        self.access_token = new_token
        return new_token

    def _ensure_token(self) -> None:
        if not self.validate_token():
            print("[Threads] 토큰이 만료되어 갱신을 시도합니다…")
            self.refresh_token()
            print("[Threads] 토큰 갱신 완료")

    def publish_post(self, text: str, image_url: "str | None" = None) -> str:
        """소개글을 스레드에 발행하고 게시물 ID를 반환합니다."""
        self._ensure_token()

        payload: dict = {"media_type": "IMAGE" if image_url else "TEXT", "text": text}
        if image_url:
            payload["image_url"] = image_url
        container = self._post(f"{self.user_id}/threads", payload)
        creation_id = container.get("id")
        if not creation_id:
            raise ThreadsApiError(f"컨테이너 생성 실패: {container}")

        time.sleep(30)  # 미디어 처리 대기

        published = self._post(
            f"{self.user_id}/threads_publish", {"creation_id": creation_id}
        )
        post_id = published.get("id")
        if not post_id:
            raise ThreadsApiError(f"발행 실패: {published}")
        return post_id

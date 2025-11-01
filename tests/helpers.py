from __future__ import annotations

from typing import Any


def authenticate_client(client: Any, username: str = "sado0809", password: str = "honges08!!") -> str:
    """Authenticate a FastAPI TestClient and attach the resulting bearer token."""

    response = client.post("/auth/login", json={"username": username, "password": password})
    assert (
        response.status_code == 200
    ), f"인증 실패: {response.status_code} {response.text}"
    payload = response.json()
    token = payload.get("access_token")
    assert token, "로그인 응답에 access_token이 없습니다."
    client.headers.update({"Authorization": f"Bearer {token}"})
    return token

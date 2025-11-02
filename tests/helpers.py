from __future__ import annotations

from typing import Any

import pyotp

from backend.security import auth_manager


def authenticate_client(
    client: Any, email: str = "sado0809@example.com", password: str = "honges08!!"
) -> str:
    """Authenticate a FastAPI TestClient and attach the resulting bearer token."""

    secret = auth_manager.snapshot().totp_secret
    totp_code = pyotp.TOTP(secret).now()
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password, "totp_code": totp_code},
    )
    assert (
        response.status_code == 200
    ), f"인증 실패: {response.status_code} {response.text}"
    payload = response.json()
    token = payload.get("access_token")
    assert token, "로그인 응답에 access_token이 없습니다."
    client.headers.update({"Authorization": f"Bearer {token}"})
    return token

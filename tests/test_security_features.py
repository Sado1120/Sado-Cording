import pytest

import pyotp

try:  # pragma: no cover - optional dependency in tests
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - fallback when FastAPI optional deps missing
    TestClient = None  # type: ignore[assignment]

from backend import app as app_module
from backend.security import auth_manager

from .helpers import authenticate_client


def _require_client():
    if TestClient is None:  # pragma: no cover - FastAPI client unavailable
        pytest.skip("fastapi TestClient unavailable")
    return TestClient(app_module.app)


def test_login_requires_totp_code():
    client = _require_client()
    response = client.post("/auth/login", json={"username": "sado0809", "password": "honges08!!"})
    assert response.status_code == 401

    invalid_format = client.post(
        "/auth/login",
        json={"username": "sado0809", "password": "honges08!!", "totp_code": "12ab34"},
    )
    assert invalid_format.status_code == 401

    secret = auth_manager.snapshot().totp_secret
    totp_code = pyotp.TOTP(secret).now()
    ok_response = client.post(
        "/auth/login",
        json={"username": "sado0809", "password": "honges08!!", "totp_code": totp_code},
    )
    assert ok_response.status_code == 200


def test_auth_profile_requires_token_and_returns_snapshot():
    client = _require_client()

    unauth = client.get("/auth/profile")
    assert unauth.status_code == 401

    authenticate_client(client)
    response = client.get("/auth/profile")
    assert response.status_code == 200
    payload = response.json()

    assert payload["username"] == "sado0809"
    assert payload["totp_enabled"] is True
    assert payload["totp_secret"]
    assert payload["totp_uri"].startswith("otpauth://totp/")
    assert payload["totp_hint"].startswith(payload["totp_secret"][:4])
    assert payload["totp_qr"].startswith("data:image/svg+xml"), "QR data URI should be provided"


def test_password_update_requires_totp_and_changes_credentials():
    client = _require_client()
    token = authenticate_client(client)
    assert token

    wrong_attempt = client.post(
        "/auth/settings/password",
        json={
            "current_password": "honges08!!",
            "new_password": "SecurePass123!",
            "totp_code": "000000",
        },
    )
    assert wrong_attempt.status_code == 401

    secret = auth_manager.snapshot().totp_secret
    totp_code = pyotp.TOTP(secret).now()
    ok_attempt = client.post(
        "/auth/settings/password",
        json={
            "current_password": "honges08!!",
            "new_password": "SecurePass123!",
            "totp_code": totp_code,
        },
    )
    assert ok_attempt.status_code == 200

    duplicate_attempt = client.post(
        "/auth/settings/password",
        json={
            "current_password": "SecurePass123!",
            "new_password": "SecurePass123!",
            "totp_code": pyotp.TOTP(auth_manager.snapshot().totp_secret).now(),
        },
    )
    assert duplicate_attempt.status_code == 400

    client.headers.pop("Authorization", None)
    new_code = pyotp.TOTP(auth_manager.snapshot().totp_secret).now()
    relogin = client.post(
        "/auth/login",
        json={"username": "sado0809", "password": "SecurePass123!", "totp_code": new_code},
    )
    assert relogin.status_code == 200

    client.headers.update({"Authorization": f"Bearer {relogin.json()['access_token']}"})
    revert_code = pyotp.TOTP(auth_manager.snapshot().totp_secret).now()
    revert = client.post(
        "/auth/settings/password",
        json={
            "current_password": "SecurePass123!",
            "new_password": "honges08!!",
            "totp_code": revert_code,
        },
    )
    assert revert.status_code == 200


def test_totp_reset_rotates_secret_and_allows_login():
    client = _require_client()
    authenticate_client(client)

    before_secret = auth_manager.snapshot().totp_secret
    totp_code = pyotp.TOTP(before_secret).now()
    response = client.post(
        "/auth/settings/totp/reset",
        json={"password": "honges08!!", "totp_code": totp_code},
    )
    assert response.status_code == 200
    payload = response.json()
    rotated_secret = payload["secret"]
    assert rotated_secret and rotated_secret != before_secret
    assert payload["enabled"] is True
    assert payload["otpauth_uri"].startswith("otpauth://totp/")
    assert payload["qr_data_uri"].startswith("data:image/svg+xml"), "Reset response should include QR"

    client.headers.pop("Authorization", None)
    new_code = pyotp.TOTP(rotated_secret).now()
    relogin = client.post(
        "/auth/login",
        json={"username": "sado0809", "password": "honges08!!", "totp_code": new_code},
    )
    assert relogin.status_code == 200


def test_totp_reset_requires_authentication():
    client = _require_client()
    before_secret = auth_manager.snapshot().totp_secret
    code = pyotp.TOTP(before_secret).now()

    response = client.post(
        "/auth/settings/totp/reset",
        json={"password": "honges08!!", "totp_code": code},
    )
    # middleware blocks unauthenticated requests before reaching handler
    assert response.status_code == 401

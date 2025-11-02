import pytest

import pyotp

try:  # pragma: no cover - optional dependency in tests
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - fallback when FastAPI optional deps missing
    TestClient = None  # type: ignore[assignment]

from backend import app as app_module
from backend.security import auth_manager
from backend.security_store import CredentialStore

from .helpers import authenticate_client


def _require_client():
    if TestClient is None:  # pragma: no cover - FastAPI client unavailable
        pytest.skip("fastapi TestClient unavailable")
    return TestClient(app_module.app)


def test_registration_flow_enables_totp(tmp_path):
    store_path = tmp_path / "security.json"
    store = CredentialStore(path=store_path)

    code, _expires = store.begin_registration("newuser@example.com", "SecurePass123!")
    ok, secret, uri, qr = store.verify_email_code(code)

    assert ok is True
    assert secret and len(secret) >= 16
    assert uri.startswith("otpauth://totp/")
    assert qr.startswith("data:image") or qr.startswith("https://")

    snapshot = store.snapshot()
    assert snapshot.email == "newuser@example.com"
    assert snapshot.email_verified is True
    assert snapshot.totp_enabled is True


def test_login_requires_totp_code():
    client = _require_client()
    response = client.post("/auth/login", json={"email": "sado0809@example.com", "password": "honges08!!"})
    assert response.status_code == 401

    invalid_format = client.post(
        "/auth/login",
        json={"email": "sado0809@example.com", "password": "honges08!!", "totp_code": "12ab34"},
    )
    assert invalid_format.status_code == 401

    secret = auth_manager.snapshot().totp_secret
    totp_code = pyotp.TOTP(secret).now()
    ok_response = client.post(
        "/auth/login",
        json={"email": "sado0809@example.com", "password": "honges08!!", "totp_code": totp_code},
    )
    assert ok_response.status_code == 200


def test_auth_status_endpoint_reports_totp_requirement():
    client = _require_client()
    response = client.get("/auth/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["email"].endswith("@example.com")
    assert payload["totp_required"] is True
    assert payload["verification_pending"] in {True, False}


def test_auth_profile_requires_token_and_returns_snapshot():
    client = _require_client()

    unauth = client.get("/auth/profile")
    assert unauth.status_code == 401

    authenticate_client(client)
    response = client.get("/auth/profile")
    assert response.status_code == 200
    payload = response.json()

    assert payload["email"] == "sado0809@example.com"
    assert payload["totp_enabled"] is True
    assert payload["totp_secret"]
    assert payload["totp_uri"].startswith("otpauth://totp/")
    assert payload["totp_hint"].startswith(payload["totp_secret"][:4])
    assert payload["totp_qr"].startswith("data:image") or payload["totp_qr"].startswith("https://")


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
        json={"email": "sado0809@example.com", "password": "SecurePass123!", "totp_code": new_code},
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
    assert payload["qr_data_uri"].startswith("data:image") or payload["qr_data_uri"].startswith("https://")

    client.headers.pop("Authorization", None)
    new_code = pyotp.TOTP(rotated_secret).now()
    relogin = client.post(
        "/auth/login",
        json={"email": "sado0809@example.com", "password": "honges08!!", "totp_code": new_code},
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


def test_env_overrides_apply_when_store_initialises(tmp_path, monkeypatch):
    store_path = tmp_path / "security.json"
    monkeypatch.setenv("SECURITY_STORE_PATH", str(store_path))
    monkeypatch.setenv("DASHBOARD_USERNAME", "override-user@example.com")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "OverridePass123!")
    monkeypatch.setenv("DASHBOARD_TOTP_SECRET", "")
    monkeypatch.setenv("DASHBOARD_TOTP_ENABLED", "0")

    store = CredentialStore(path=store_path)
    snapshot = store.snapshot()
    assert snapshot.email == "override-user@example.com"
    assert snapshot.totp_enabled is False
    assert snapshot.totp_secret == ""
    assert store.verify_password("OverridePass123!") is True
    assert store.provisioning_uri() == ""
    assert store.provisioning_qr() == ""

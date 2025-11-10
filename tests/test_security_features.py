import time
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

import pyotp

try:  # pragma: no cover - optional dependency in tests
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - fallback when FastAPI optional deps missing
    TestClient = None  # type: ignore[assignment]

from backend import app as app_module
from backend import email_utils
from backend.app import _extract_bearer_token
from backend.security import auth_manager, hash_admin_password
from backend.security_store import CredentialStore

from .helpers import authenticate_client


def _require_client():
    if TestClient is None:  # pragma: no cover - FastAPI client unavailable
        pytest.skip("fastapi TestClient unavailable")
    return TestClient(app_module.app)


def test_extract_bearer_token_supports_fallback_headers():
    request = SimpleNamespace(headers={"authorization": "Bearer abc123"}, query_params={})
    assert _extract_bearer_token(request) == "abc123"

    request = SimpleNamespace(headers={"x-auth-token": "fallback-token"}, query_params={})
    assert _extract_bearer_token(request) == "fallback-token"

    request = SimpleNamespace(headers={}, query_params={"access_token": "query-token"})
    assert _extract_bearer_token(request) == "query-token"

    request = SimpleNamespace(headers={}, query_params={})
    assert _extract_bearer_token(request) == ""


def test_send_verification_email_uses_smtp(monkeypatch):
    records: dict[str, object] = {}

    class DummySMTP:
        def __init__(self, host: str, port: int, timeout: float = 10.0) -> None:
            records.update({"host": host, "port": port, "timeout": timeout})

        def __enter__(self):  # pragma: no cover - trivial context manager
            return self

        def __exit__(self, exc_type, exc, tb):  # pragma: no cover - trivial context manager
            return False

        def starttls(self, context=None):
            records["tls"] = True

        def login(self, username: str, password: str) -> None:
            records["username"] = username
            records["password"] = password

        def send_message(self, message: EmailMessage) -> None:
            records["message"] = message

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_USERNAME", "api@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM", "no-reply@example.com")
    monkeypatch.setenv("SMTP_USE_TLS", "true")

    monkeypatch.setattr(email_utils, "smtplib", SimpleNamespace(SMTP=DummySMTP))

    sent = email_utils.send_verification_email(
        "user@example.com", "123456", time.time() + 600
    )

    assert sent is True
    assert records["host"] == "smtp.example.com"
    assert records["port"] == 2525
    assert records.get("tls") is True
    assert records.get("username") == "api@example.com"
    assert isinstance(records.get("message"), EmailMessage)
    assert "123456" in records["message"].get_content()


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


def test_totp_qr_fallback_uses_data_uri(monkeypatch, tmp_path):
    store_path = tmp_path / "security.json"
    store = CredentialStore(path=store_path)

    code, _ = store.begin_registration("fallback@example.com", "SecurePass123!")

    class _DummyResponse:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def raise_for_status(self) -> None:  # pragma: no cover - simple stub
            return None

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

    class _DummyHttpx:
        @staticmethod
        def get(_url: str, timeout: float = 5.0) -> _DummyResponse:
            return _DummyResponse(fake_png)

    monkeypatch.setattr("backend.security_store.segno", None)
    monkeypatch.setattr("backend.security_store.httpx", _DummyHttpx)

    ok, _secret, _uri, qr = store.verify_email_code(code)

    assert ok is True
    assert qr.startswith("data:image/png;base64,")


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


def test_admin_login_skips_totp(monkeypatch):
    client = _require_client()
    monkeypatch.setattr(auth_manager.settings, "admin_username", "admin@example.com", raising=False)
    monkeypatch.setattr(
        auth_manager.settings,
        "admin_password_hash",
        hash_admin_password("AdminPass123!"),
        raising=False,
    )

    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "AdminPass123!"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload.get("access_token")


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


def test_register_endpoint_triggers_email(monkeypatch):
    client = _require_client()
    called: dict[str, object] = {}

    def _fake_send(email: str, code: str, expires_at: float) -> bool:
        called.update({"email": email, "code": code, "expires_at": expires_at})
        return True

    monkeypatch.setattr(app_module, "send_verification_email", _fake_send)

    response = client.post(
        "/auth/register",
        json={"email": "newuser@example.com", "password": "StrongPass123!"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload.get("email_sent") is True
    assert called["email"] == "newuser@example.com"


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

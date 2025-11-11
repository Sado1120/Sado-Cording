"""Authentication utilities for Sado Trade Bot dashboard."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict

from fastapi import HTTPException, status

from .security_store import CredentialSnapshot, CredentialStore


@dataclass(slots=True)
class AuthSettings:
    token_ttl: int
    admin_username: str | None = None
    admin_password_hash: str | None = None


AUTH_COOKIE_NAME = "sado_access_token"


class AuthManager:
    """Manage dashboard credentials, tokens, and multi-factor secrets."""

    def __init__(self, store: CredentialStore, settings: AuthSettings) -> None:
        self._store = store
        self._settings = settings
        self._lock = threading.RLock()
        self._tokens: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # public helpers
    @property
    def settings(self) -> AuthSettings:
        return self._settings

    def snapshot(self) -> CredentialSnapshot:
        return self._store.snapshot()

    def requires_totp(self) -> bool:
        return self._store.snapshot().totp_enabled

    # ------------------------------------------------------------------
    # authentication primitives
    def authenticate_primary(self, email: str, password: str) -> bool:
        snapshot = self._store.snapshot()
        if not secrets.compare_digest(email, snapshot.email):
            return False
        return self._store.verify_password(password)

    def authenticate_admin(self, email: str, password: str) -> bool:
        admin_username = (self._settings.admin_username or "").strip()
        admin_hash = self._settings.admin_password_hash or ""
        if not admin_username or not admin_hash:
            return False
        if not secrets.compare_digest(email, admin_username):
            return False
        return _verify_admin_password(password, admin_hash)

    def verify_totp_code(self, code: str | None) -> bool:
        return self._store.verify_totp(code)

    def issue_token(self, subject: str) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + float(self._settings.token_ttl)
        with self._lock:
            self._tokens[token] = expires_at
            self._purge_locked()
        return token

    def validate_token(self, token: str) -> bool:
        now = time.time()
        with self._lock:
            expires_at = self._tokens.get(token)
            if expires_at is None:
                return False
            if expires_at < now:
                self._tokens.pop(token, None)
                return False
            return True

    def revoke_token(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)

    def update_password(self, new_password: str) -> None:
        self._store.set_password(new_password)

    def rotate_totp(self) -> tuple[str, str, str]:
        return self._store.rotate_totp()

    def provisioning_uri(self) -> str:
        return self._store.provisioning_uri()

    def provisioning_qr(self) -> str:
        return self._store.provisioning_qr()

    # ------------------------------------------------------------------
    def _purge_locked(self) -> None:
        cutoff = time.time()
        expired = [token for token, expiry in self._tokens.items() if expiry < cutoff]
        for token in expired:
            self._tokens.pop(token, None)


def load_auth_settings() -> AuthSettings:
    ttl = int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "43200"))  # 12 hours default
    admin_username = os.getenv("DASHBOARD_ADMIN_USERNAME")
    admin_hash = os.getenv("DASHBOARD_ADMIN_PASSWORD_HASH")
    admin_plain = os.getenv("DASHBOARD_ADMIN_PASSWORD")

    if admin_username:
        admin_username = admin_username.strip()
    if admin_hash:
        admin_hash = admin_hash.strip()

    if admin_hash:
        pass
    elif admin_plain:
        admin_hash = hash_admin_password(admin_plain.strip())
    else:
        admin_hash = None

    if not admin_username or not admin_hash:
        admin_username = None
        admin_hash = None

    return AuthSettings(
        token_ttl=ttl,
        admin_username=admin_username,
        admin_password_hash=admin_hash,
    )


_store = CredentialStore()
auth_manager = AuthManager(_store, load_auth_settings())


def ensure_authenticated(token: str | None) -> None:
    if not token or not auth_manager.validate_token(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다.")


def verify_login(email: str, password: str, totp_code: str | None) -> str:
    if auth_manager.authenticate_admin(email, password):
        return auth_manager.issue_token(email)

    if not auth_manager.authenticate_primary(email, password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="아이디 또는 비밀번호가 올바르지 않습니다.")

    snapshot = auth_manager.snapshot()
    if not snapshot.email_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="이메일 인증이 완료되지 않았습니다.")

    if auth_manager.requires_totp() and not auth_manager.verify_totp_code(totp_code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="보안 코드가 올바르지 않습니다.")

    return auth_manager.issue_token(email)


def begin_registration(email: str, password: str) -> tuple[str, float]:
    try:
        return auth_manager._store.begin_registration(email, password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def resend_verification() -> tuple[str, float, str | None]:
    return auth_manager._store.resend_verification()


def hash_admin_password(password: str) -> str:
    """Derive a PBKDF2-hashed administrator password."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390000)
    return f"{base64.b64encode(salt).decode('ascii')}${base64.b64encode(digest).decode('ascii')}"


def _verify_admin_password(password: str, stored_hash: str) -> bool:
    try:
        salt_b64, digest_b64 = stored_hash.split("$", 1)
        salt = base64.b64decode(salt_b64.encode("ascii"))
        digest = base64.b64decode(digest_b64.encode("ascii"))
    except Exception:
        return False

    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390000)
    return hmac.compare_digest(candidate, digest)


def _main() -> None:
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description="Sado Trade Bot security utilities")
    subparsers = parser.add_subparsers(dest="command")

    hash_parser = subparsers.add_parser(
        "hash-admin", help="PBKDF2 해시가 적용된 관리자 비밀번호 문자열 생성"
    )
    hash_parser.add_argument("password", nargs="?", help="새 관리자 비밀번호")
    hash_parser.add_argument(
        "--prompt",
        action="store_true",
        help="비밀번호를 터미널에서 안전하게 입력하도록 요청",
    )

    args = parser.parse_args()

    if args.command != "hash-admin":
        parser.print_help()
        return

    password = args.password
    if args.prompt or password is None:
        password = getpass.getpass("새 관리자 비밀번호: ")
        confirm = getpass.getpass("비밀번호 확인: ")
        if password != confirm:
            raise SystemExit("비밀번호가 일치하지 않습니다.")

    if not password:
        raise SystemExit("비밀번호를 입력하세요.")

    print(hash_admin_password(password))


if __name__ == "__main__":  # pragma: no cover - CLI utility
    _main()


def verify_email_code(code: str) -> tuple[str, str, str]:
    ok, secret, uri, qr = auth_manager._store.verify_email_code(code)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="인증 코드가 올바르지 않거나 만료되었습니다.",
        )
    return secret, uri, qr


def registration_status() -> Dict[str, Any]:
    snapshot = auth_manager.snapshot()
    pending = auth_manager._store.verification_status()
    return {
        "email": snapshot.email,
        "email_verified": snapshot.email_verified,
        "verification_pending": bool(pending.get("pending")),
        "verification_expires_at": pending.get("expires_at"),
    }

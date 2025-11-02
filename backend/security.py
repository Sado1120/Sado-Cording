"""Authentication utilities for Sado Trade Bot dashboard."""
from __future__ import annotations

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
    def authenticate_primary(self, username: str, password: str) -> bool:
        snapshot = self._store.snapshot()
        if not secrets.compare_digest(username, snapshot.username):
            return False
        return self._store.verify_password(password)

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
    return AuthSettings(token_ttl=ttl)


_store = CredentialStore()
auth_manager = AuthManager(_store, load_auth_settings())


def ensure_authenticated(token: str | None) -> None:
    if not token or not auth_manager.validate_token(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다.")


def verify_login(username: str, password: str, totp_code: str | None) -> str:
    if not auth_manager.authenticate_primary(username, password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="아이디 또는 비밀번호가 올바르지 않습니다.")

    if auth_manager.requires_totp() and not auth_manager.verify_totp_code(totp_code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="보안 코드가 올바르지 않습니다.")

    return auth_manager.issue_token(username)

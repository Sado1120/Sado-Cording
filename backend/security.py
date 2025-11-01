"""Authentication utilities for Sado Trade Bot dashboard."""
from __future__ import annotations

import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict

from fastapi import HTTPException, status

_DEFAULT_USERNAME = "sado0809"
_DEFAULT_PASSWORD = "honges08!!"


@dataclass(slots=True)
class AuthSettings:
    """Resolved authentication settings."""

    username: str
    password: str
    token_ttl: int


class AuthManager:
    """Manage dashboard credentials and short-lived bearer tokens."""

    def __init__(self, settings: AuthSettings) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        self._tokens: Dict[str, float] = {}

    @property
    def settings(self) -> AuthSettings:
        return self._settings

    def authenticate(self, username: str, password: str) -> bool:
        """Return ``True`` if the provided credentials match the configured pair."""

        return secrets.compare_digest(username, self._settings.username) and secrets.compare_digest(
            password, self._settings.password
        )

    def issue_token(self, subject: str) -> str:
        """Create a new bearer token and register it for validation."""

        token = secrets.token_urlsafe(32)
        expires_at = time.time() + float(self._settings.token_ttl)
        with self._lock:
            self._tokens[token] = expires_at
            self._purge_locked()
        return token

    def validate_token(self, token: str) -> bool:
        """Validate a token, returning ``True`` when it is known and unexpired."""

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

    def _purge_locked(self) -> None:
        cutoff = time.time()
        expired = [token for token, expiry in self._tokens.items() if expiry < cutoff]
        for token in expired:
            self._tokens.pop(token, None)


def load_auth_settings() -> AuthSettings:
    username = os.getenv("DASHBOARD_USERNAME", _DEFAULT_USERNAME)
    password = os.getenv("DASHBOARD_PASSWORD", _DEFAULT_PASSWORD)
    ttl = int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "43200"))  # 12 hours default
    return AuthSettings(username=username, password=password, token_ttl=ttl)


auth_manager = AuthManager(load_auth_settings())


def ensure_authenticated(token: str | None) -> None:
    if not token or not auth_manager.validate_token(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다.")


def verify_login(username: str, password: str) -> str:
    if not auth_manager.authenticate(username, password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="아이디 또는 비밀번호가 올바르지 않습니다.")

    return auth_manager.issue_token(username)

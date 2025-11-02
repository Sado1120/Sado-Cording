"""Persistent credential storage and TOTP helpers."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

try:  # pragma: no cover - prefer bundled fallback when pyotp missing
    import pyotp  # type: ignore
except Exception:  # pragma: no cover - fallback implementation

    class _FallbackTOTP:
        def __init__(self, secret: str, interval: int = 30, digits: int = 6) -> None:
            self.secret = secret
            self.interval = interval
            self.digits = digits

        def _timecode(self, for_time: float | None = None) -> int:
            return int((for_time or time.time()) / self.interval)

        def _byte_secret(self) -> bytes:
            padding = "=" * (-len(self.secret) % 8)
            return base64.b32decode((self.secret + padding).encode("ascii"), casefold=True)

        def generate_otp(self, counter: int) -> str:
            message = struct.pack(">Q", counter)
            digest = hmac.new(self._byte_secret(), message, hashlib.sha1).digest()
            offset = digest[-1] & 0x0F
            code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
            otp = code % (10**self.digits)
            return f"{otp:0{self.digits}d}"

        def now(self) -> str:
            return self.generate_otp(self._timecode())

        def verify(self, code: str, valid_window: int = 0) -> bool:
            if not code:
                return False
            timestep = self._timecode()
            for delta in range(-valid_window, valid_window + 1):
                candidate_counter = timestep + delta
                if candidate_counter < 0:
                    continue
                if hmac.compare_digest(self.generate_otp(candidate_counter), code):
                    return True
            return False

        def provisioning_uri(self, name: str, issuer_name: str) -> str:
            label = quote(f"{issuer_name}:{name}")
            issuer = quote(issuer_name)
            return (
                f"otpauth://totp/{label}?secret={self.secret}&issuer={issuer}&period={self.interval}"
            )

    def _fallback_random_base32(length: int = 32) -> str:
        raw = base64.b32encode(os.urandom(length)).decode("ascii")
        return raw.rstrip("=")

    class _PyotpCompat:
        TOTP = _FallbackTOTP

        @staticmethod
        def random_base32(length: int = 32) -> str:
            return _fallback_random_base32(length)

    pyotp = _PyotpCompat()  # type: ignore

try:  # pragma: no cover - segno optional during import time
    import segno  # type: ignore
except Exception:  # pragma: no cover - fall back to None and skip QR generation
    segno = None  # type: ignore


_DEFAULT_EMAIL = "sado0809@example.com"
_DEFAULT_PASSWORD = "honges08!!"
_DEFAULT_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # HELLOWORLD base32 – replace in production

_EMAIL_REGEX = re.compile(
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
)

_VERIFICATION_CODE_REGEX = re.compile(r"^[0-9]{6}$")

_VERIFICATION_TTL_SECONDS = 30 * 60  # 30 minutes


def _now_ts() -> float:
    return time.time()


def _encode_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode_bytes(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


def _hash_password(password: str, salt: bytes) -> str:
    import hashlib

    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390000)
    return _encode_bytes(digest)


def _parse_env_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if not value:
        return default
    if value in {"0", "false", "no", "off"}:
        return False
    if value in {"1", "true", "yes", "on"}:
        return True
    return default


def _normalise_totp_code(code: str | None) -> str | None:
    if code is None:
        return None
    stripped = re.sub(r"\s+", "", code)
    if not stripped:
        return None
    if not re.fullmatch(r"\d{6,8}", stripped):
        return None
    return stripped


def _validate_email(email: str) -> str:
    candidate = email.strip()
    if not candidate:
        raise ValueError("이메일을 입력하세요.")
    if not _EMAIL_REGEX.match(candidate):
        raise ValueError("올바른 이메일 형식이 아닙니다.")
    return candidate


def _generate_verification_code() -> str:
    return "".join(str(int.from_bytes(os.urandom(1), "big") % 10) for _ in range(6))


@dataclass(frozen=True)
class CredentialSnapshot:
    """Lightweight projection of persisted credential data."""

    email: str
    email_verified: bool
    totp_secret: str
    totp_enabled: bool
    totp_rotated_at: float | None

    @property
    def username(self) -> str:  # Backwards compatibility for legacy callers
        return self.email


class CredentialStore:
    """Manage credential persistence with thread-safe updates."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        default_username: str | None = None,
        default_password: str | None = None,
        default_totp_secret: str | None = None,
    ) -> None:
        self._path = Path(path or os.getenv("SECURITY_STORE_PATH", "./data/security.json"))
        self._lock = threading.RLock()
        self._data: Dict[str, Any] | None = None
        env_totp_secret = os.getenv("DASHBOARD_TOTP_SECRET")
        if default_totp_secret is not None:
            initial_totp_secret = default_totp_secret
        elif env_totp_secret is not None:
            initial_totp_secret = env_totp_secret
        else:
            initial_totp_secret = _DEFAULT_TOTP_SECRET

        self._defaults = {
            "email": default_username or os.getenv("DASHBOARD_USERNAME", _DEFAULT_EMAIL),
            "password": default_password or os.getenv("DASHBOARD_PASSWORD", _DEFAULT_PASSWORD),
            "totp_secret": initial_totp_secret,
            "totp_enabled": _parse_env_bool(
                os.getenv("DASHBOARD_TOTP_ENABLED"), default=True
            ),
            "email_verified": _parse_env_bool(
                os.getenv("DASHBOARD_EMAIL_VERIFIED"), default=True
            ),
        }
        if not self._defaults["totp_secret"]:
            self._defaults["totp_enabled"] = False
        self._load()

    # ------------------------------------------------------------------
    # loading & persistence
    def _load(self) -> None:
        with self._lock:
            if self._data is not None:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.exists():
                try:
                    raw = json.loads(self._path.read_text("utf-8"))
                except json.JSONDecodeError:
                    raw = {}
            else:
                raw = {}

            if not raw:
                raw = self._initial_payload()
                self._write_locked(raw)
            else:
                if self._apply_env_overrides(raw):
                    self._write_locked(raw)

            self._data = raw

    def _initial_payload(self) -> Dict[str, Any]:
        salt = os.urandom(16)
        password_hash = _hash_password(self._defaults["password"], salt)
        payload = {
            "email": _validate_email(self._defaults["email"]),
            "email_verified": bool(self._defaults.get("email_verified", True)),
            "password": {
                "hash": password_hash,
                "salt": _encode_bytes(salt),
                "updated_at": _now_ts(),
            },
            "totp": {
                "secret": self._defaults["totp_secret"],
                "enabled": bool(self._defaults["totp_enabled"]),
                "rotated_at": None,
            },
            "verification": {},
        }
        if not payload["totp"]["secret"]:
            payload["totp"]["enabled"] = False
        return payload

    def _write_locked(self, payload: Dict[str, Any]) -> None:
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), "utf-8")
        tmp_path.replace(self._path)

    def _ensure_loaded(self) -> None:
        if self._data is None:
            self._load()

    def _password_matches(self, payload: Dict[str, Any], candidate: str) -> bool:
        entry = payload.get("password", {})
        salt_b64 = entry.get("salt")
        stored_hash = entry.get("hash")
        if not salt_b64 or not stored_hash:
            return False
        salt = _decode_bytes(str(salt_b64))
        candidate_hash = _hash_password(candidate, salt)
        return hmac.compare_digest(candidate_hash, str(stored_hash))

    def _ensure_totp_structure(self, payload: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        changed = False
        totp = payload.get("totp")
        if not isinstance(totp, dict):
            totp = {
                "secret": self._defaults["totp_secret"],
                "enabled": bool(self._defaults["totp_enabled"]),
                "rotated_at": None,
            }
            payload["totp"] = totp
            changed = True
        if "secret" not in totp:
            totp["secret"] = self._defaults["totp_secret"]
            changed = True
        if "enabled" not in totp:
            totp["enabled"] = bool(totp.get("secret"))
            changed = True
        if "rotated_at" not in totp:
            totp["rotated_at"] = totp.get("rotated_at", None)
            changed = True
        if not totp.get("secret") and totp.get("enabled", False):
            totp["enabled"] = False
            changed = True
        return totp, changed

    def _apply_env_overrides(self, payload: Dict[str, Any]) -> bool:
        updated = False

        env_email = os.getenv("DASHBOARD_USERNAME")
        if env_email:
            candidate = env_email.strip()
            if candidate:
                try:
                    validated = _validate_email(candidate)
                except ValueError:
                    validated = payload.get("email", self._defaults["email"])
                if payload.get("email") != validated:
                    payload["email"] = validated
                    updated = True

        env_email_verified = os.getenv("DASHBOARD_EMAIL_VERIFIED")
        if env_email_verified is not None:
            desired_verified = _parse_env_bool(env_email_verified, default=True)
            if payload.get("email_verified") != desired_verified:
                payload["email_verified"] = desired_verified
                updated = True

        env_password = os.getenv("DASHBOARD_PASSWORD")
        if env_password:
            password = env_password.strip()
            if password and not self._password_matches(payload, password):
                salt = os.urandom(16)
                payload["password"] = {
                    "hash": _hash_password(password, salt),
                    "salt": _encode_bytes(salt),
                    "updated_at": _now_ts(),
                }
                updated = True

        totp_entry, struct_changed = self._ensure_totp_structure(payload)
        if struct_changed:
            updated = True

        env_totp_secret = os.getenv("DASHBOARD_TOTP_SECRET")
        env_totp_enabled_raw = os.getenv("DASHBOARD_TOTP_ENABLED")

        desired_enabled = bool(totp_entry.get("enabled", True) and totp_entry.get("secret"))

        if env_totp_secret is not None:
            sanitized = re.sub(r"\s+", "", env_totp_secret).upper()
            if sanitized:
                if totp_entry.get("secret") != sanitized:
                    totp_entry["secret"] = sanitized
                    totp_entry["rotated_at"] = _now_ts()
                    updated = True
                desired_enabled = True
            else:
                if totp_entry.get("secret"):
                    totp_entry["secret"] = ""
                    updated = True
                desired_enabled = False

        if env_totp_enabled_raw is not None:
            desired_enabled = _parse_env_bool(env_totp_enabled_raw, default=desired_enabled)

        if desired_enabled and not totp_entry.get("secret"):
            fallback_secret = (
                os.getenv("DASHBOARD_TOTP_SECRET")
                or self._defaults.get("totp_secret")
                or pyotp.random_base32()
            )
            if totp_entry.get("secret") != fallback_secret:
                totp_entry["secret"] = fallback_secret
                totp_entry["rotated_at"] = _now_ts()
                updated = True

        if totp_entry.get("enabled") != desired_enabled:
            totp_entry["enabled"] = desired_enabled
            updated = True

        if not totp_entry.get("secret") and totp_entry.get("enabled"):
            totp_entry["enabled"] = False
            updated = True

        return updated

    # ------------------------------------------------------------------
    # helpers
    def _get_password_entry(self) -> Dict[str, Any]:
        self._ensure_loaded()
        assert self._data is not None
        return self._data["password"]

    def _get_totp_entry(self) -> Dict[str, Any]:
        self._ensure_loaded()
        assert self._data is not None
        return self._data["totp"]

    def _get_verification_entry(self) -> Dict[str, Any]:
        self._ensure_loaded()
        assert self._data is not None
        entry = self._data.get("verification")
        if not isinstance(entry, dict):
            entry = {}
            self._data["verification"] = entry
        return entry

    # ------------------------------------------------------------------
    # public API
    def snapshot(self) -> CredentialSnapshot:
        self._ensure_loaded()
        assert self._data is not None
        totp = self._get_totp_entry()
        email = str(self._data.get("email", self._defaults["email"]))
        verified = bool(self._data.get("email_verified", True))
        return CredentialSnapshot(
            email=email,
            email_verified=verified,
            totp_secret=str(totp.get("secret", "")),
            totp_enabled=bool(totp.get("enabled", True) and totp.get("secret")),
            totp_rotated_at=totp.get("rotated_at"),
        )

    def verify_password(self, password: str) -> bool:
        entry = self._get_password_entry()
        salt = _decode_bytes(entry["salt"])
        expected = entry["hash"]
        candidate = _hash_password(password, salt)
        import secrets

        return secrets.compare_digest(candidate, expected)

    def set_password(self, new_password: str) -> None:
        salt = os.urandom(16)
        hashed = _hash_password(new_password, salt)
        with self._lock:
            self._ensure_loaded()
            assert self._data is not None
            self._data["password"] = {
                "hash": hashed,
                "salt": _encode_bytes(salt),
                "updated_at": _now_ts(),
            }
            self._write_locked(self._data)

    def verify_totp(self, code: str | None) -> bool:
        entry = self._get_totp_entry()
        if not entry.get("enabled", True):
            return True
        sanitized = _normalise_totp_code(code)
        if not sanitized:
            return False
        secret = entry.get("secret")
        if not secret:
            return False
        totp = pyotp.TOTP(secret)
        return bool(totp.verify(sanitized, valid_window=1))

    def rotate_totp(self) -> tuple[str, str, str]:
        secret = pyotp.random_base32()
        with self._lock:
            self._ensure_loaded()
            assert self._data is not None
            self._data["totp"]["secret"] = secret
            self._data["totp"]["enabled"] = True
            self._data["totp"]["rotated_at"] = _now_ts()
            self._write_locked(self._data)
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=self.snapshot().username,
            issuer_name="Sado Trade Bot",
        )
        return secret, uri, self.provisioning_qr()

    def provisioning_uri(self) -> str:
        snap = self.snapshot()
        if not snap.totp_secret:
            return ""
        return pyotp.TOTP(snap.totp_secret).provisioning_uri(
            name=snap.username, issuer_name="Sado Trade Bot"
        )

    def provisioning_qr(self) -> str:
        """Return a data URI containing the provisioning QR code."""

        if segno is None:  # pragma: no cover - dependency missing in constrained envs
            return ""
        uri = self.provisioning_uri()
        if not uri:
            return ""
        try:
            qr = segno.make(uri)
        except Exception:  # pragma: no cover - segno failed to encode data
            return ""
        return qr.svg_data_uri(scale=4)

    def disable_totp(self) -> None:
        with self._lock:
            self._ensure_loaded()
            assert self._data is not None
            self._data["totp"]["enabled"] = False
            self._write_locked(self._data)

    def enable_totp(self) -> None:
        with self._lock:
            self._ensure_loaded()
            assert self._data is not None
            if not self._data["totp"].get("secret"):
                self._data["totp"]["secret"] = pyotp.random_base32()
                self._data["totp"]["rotated_at"] = _now_ts()
            self._data["totp"]["enabled"] = True
            self._write_locked(self._data)

    # ------------------------------------------------------------------
    # registration & verification
    def begin_registration(self, email: str, password: str) -> tuple[str, float]:
        candidate_email = _validate_email(email)
        if len(password.strip()) < 8:
            raise ValueError("비밀번호는 8자 이상이어야 합니다.")

        salt = os.urandom(16)
        hashed_password = _hash_password(password, salt)
        verification_code = _generate_verification_code()
        expires_at = _now_ts() + _VERIFICATION_TTL_SECONDS

        with self._lock:
            self._ensure_loaded()
            assert self._data is not None
            self._data["email"] = candidate_email
            self._data["email_verified"] = False
            self._data["password"] = {
                "hash": hashed_password,
                "salt": _encode_bytes(salt),
                "updated_at": _now_ts(),
            }
            totp_entry = self._get_totp_entry()
            if not totp_entry.get("secret"):
                totp_entry["secret"] = pyotp.random_base32()
            totp_entry["enabled"] = False
            totp_entry["rotated_at"] = None
            verification_entry = self._get_verification_entry()
            verification_entry["code"] = verification_code
            verification_entry["created_at"] = _now_ts()
            verification_entry["expires_at"] = expires_at
            verification_entry["attempts"] = 0
            self._write_locked(self._data)

        return verification_code, expires_at

    def resend_verification(self) -> tuple[str, float]:
        with self._lock:
            self._ensure_loaded()
            assert self._data is not None
            verification_entry = self._get_verification_entry()
            verification_entry["code"] = _generate_verification_code()
            verification_entry["created_at"] = _now_ts()
            verification_entry["expires_at"] = _now_ts() + _VERIFICATION_TTL_SECONDS
            verification_entry["attempts"] = 0
            self._write_locked(self._data)
            return verification_entry["code"], verification_entry["expires_at"]

    def verify_email_code(self, code: str) -> tuple[bool, str, str, str]:
        candidate = code.strip()
        if not _VERIFICATION_CODE_REGEX.match(candidate):
            return False, "", "", ""

        with self._lock:
            self._ensure_loaded()
            assert self._data is not None
            verification_entry = self._get_verification_entry()
            stored_code = verification_entry.get("code")
            expires_at = verification_entry.get("expires_at")
            if not stored_code:
                return False, "", "", ""
            if expires_at and expires_at < _now_ts():
                return False, "", "", ""
            if not secrets.compare_digest(str(stored_code), candidate):
                verification_entry["attempts"] = int(verification_entry.get("attempts", 0)) + 1
                self._write_locked(self._data)
                return False, "", "", ""

            totp_entry = self._get_totp_entry()
            if not totp_entry.get("secret"):
                totp_entry["secret"] = pyotp.random_base32()
            totp_entry["enabled"] = True
            totp_entry["rotated_at"] = _now_ts()

            self._data["email_verified"] = True
            verification_entry.clear()
            self._write_locked(self._data)

            snapshot = self.snapshot()
            secret = snapshot.totp_secret
            uri = self.provisioning_uri()
            qr = self.provisioning_qr()
            return True, secret, uri, qr

    def verification_status(self) -> Dict[str, Any]:
        entry = self._get_verification_entry().copy()
        return {
            "pending": bool(entry.get("code")),
            "expires_at": entry.get("expires_at"),
            "attempts": entry.get("attempts", 0),
        }


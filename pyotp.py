"""Minimal TOTP implementation compatible with pyotp usage in tests."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote


def random_base32(length: int = 32) -> str:
    """Return a base32 secret without padding characters."""

    raw = base64.b32encode(os.urandom(length)).decode("ascii")
    return raw.rstrip("=")


@dataclass
class TOTP:
    secret: str
    interval: int = 30
    digits: int = 6

    def _byte_secret(self) -> bytes:
        padding = "=" * (-len(self.secret) % 8)
        return base64.b32decode((self.secret + padding).encode("ascii"), casefold=True)

    def _timecode(self, for_time: Optional[float] = None) -> int:
        return int((for_time or time.time()) / self.interval)

    def _generate_otp(self, counter: int) -> str:
        message = struct.pack(">Q", counter)
        digest = hmac.new(self._byte_secret(), message, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        otp = code % (10**self.digits)
        return f"{otp:0{self.digits}d}"

    def at(self, for_time: float) -> str:
        return self._generate_otp(self._timecode(for_time))

    def now(self) -> str:
        return self._generate_otp(self._timecode())

    def verify(self, code: Optional[str], valid_window: int = 0) -> bool:
        if code is None:
            return False
        candidate = "".join(str(code).split())
        if not candidate.isdigit():
            return False
        timestep = self._timecode()
        for delta in range(-valid_window, valid_window + 1):
            counter = timestep + delta
            if counter < 0:
                continue
            if hmac.compare_digest(self._generate_otp(counter), candidate):
                return True
        return False

    def provisioning_uri(self, name: str, issuer_name: str) -> str:
        label = quote(f"{issuer_name}:{name}")
        issuer = quote(issuer_name)
        return (
            f"otpauth://totp/{label}?secret={self.secret}&issuer={issuer}&period={self.interval}"
        )


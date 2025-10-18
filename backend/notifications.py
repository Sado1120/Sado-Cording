"""Utility helpers for notifying Synology Chat or other webhook targets."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

try:  # pragma: no cover - optional dependency during import
    import httpx
except ModuleNotFoundError:  # pragma: no cover - handled lazily in function
    httpx = None  # type: ignore[assignment]


_last_attempt_at: Optional[datetime] = None
_last_success_at: Optional[datetime] = None
_last_error: Optional[str] = None
_last_message: Optional[str] = None


def _record_attempt(*, success: bool, message: str, error: Optional[str]) -> None:
    global _last_attempt_at, _last_success_at, _last_error, _last_message

    timestamp = datetime.now(timezone.utc)
    _last_attempt_at = timestamp
    _last_message = message
    if success:
        _last_success_at = timestamp
        _last_error = None
    else:
        _last_error = error


def notify_synology_chat(
    message: str,
    *,
    webhook_url: Optional[str] = None,
    client: Optional[Any] = None,
    timeout: float = 5.0,
) -> bool:
    """Send a message to Synology Chat via webhook.

    The webhook URL can be provided directly or via the ``SADO_CHAT_WEBHOOK``
    or ``SYNOLOGY_CHAT_WEBHOOK`` environment variables.  The helper swallows
    network errors and returns ``False`` when delivery fails so the caller can
    decide whether to retry without interrupting trading flows.
    """

    url = (
        webhook_url
        or os.getenv("SADO_CHAT_WEBHOOK")
        or os.getenv("SYNOLOGY_CHAT_WEBHOOK")
    )
    if not url:
        _record_attempt(success=False, message=message, error="웹훅 URL이 설정되지 않았습니다.")
        return False

    payload = {"text": message}
    created_client = False
    if client is None:
        if httpx is None:  # dependency unavailable
            _record_attempt(success=False, message=message, error="httpx 패키지가 설치되어 있지 않습니다.")
            return False
        client = httpx.Client(timeout=timeout)
        created_client = True

    try:
        response = client.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        _record_attempt(success=True, message=message, error=None)
        return True
    except Exception as exc:  # pragma: no cover - network failures handled gracefully
        _record_attempt(success=False, message=message, error=str(exc))
        return False
    finally:
        if created_client:
            client.close()


def get_synology_chat_status() -> dict:
    return {
        "configured": bool(
            os.getenv("SADO_CHAT_WEBHOOK") or os.getenv("SYNOLOGY_CHAT_WEBHOOK")
        ),
        "last_attempt_at": _last_attempt_at,
        "last_success_at": _last_success_at,
        "last_error": _last_error,
        "last_message": _last_message,
    }


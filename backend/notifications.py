"""Utility helpers for notifying Synology Chat or other webhook targets."""
from __future__ import annotations

import os
from typing import Any, Optional

try:  # pragma: no cover - optional dependency during import
    import httpx
except ModuleNotFoundError:  # pragma: no cover - handled lazily in function
    httpx = None  # type: ignore[assignment]


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
        return False

    payload = {"text": message}
    created_client = False
    if client is None:
        if httpx is None:  # dependency unavailable
            return False
        client = httpx.Client(timeout=timeout)
        created_client = True

    try:
        response = client.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        return True
    except Exception:  # pragma: no cover - network failures handled gracefully
        return False
    finally:
        if created_client:
            client.close()


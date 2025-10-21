"""Utility helpers for notifying Synology Chat or other webhook targets."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

try:  # pragma: no cover - optional dependency during import
    import httpx
except ModuleNotFoundError:  # pragma: no cover - handled lazily in function
    httpx = None  # type: ignore[assignment]


_last_attempt_at: Optional[datetime] = None
_last_success_at: Optional[datetime] = None
_last_error: Optional[str] = None
_last_message: Optional[str] = None
_change_digests: Dict[str, str] = {}


def _clean_webhook_url(raw: Optional[str]) -> str:
    """Return a normalised webhook URL without stray quotes or encodings."""

    if not raw:
        return ""

    cleaned = raw.strip().strip("'\"")
    if not cleaned:
        return ""

    # 사용자들이 토큰 앞뒤에 따옴표를 붙이거나 %22로 인코딩하는 경우가 많아 제거한다.
    cleaned = cleaned.replace("%22", "")

    try:
        parsed = urlparse(cleaned)
    except Exception:  # pragma: no cover - parsing 실패 시 원본 사용
        return cleaned

    query_items = []
    if parsed.query:
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if value is None:
                new_value = ""
            else:
                new_value = unquote(value).strip().strip("'\"")
            query_items.append((key, new_value))
    rebuilt = parsed._replace(query=urlencode(query_items, doseq=True))
    return urlunparse(rebuilt)


def _resolve_webhook_url(override: Optional[str] = None) -> str:
    candidates = (
        override,
        os.getenv("SADO_CHAT_WEBHOOK"),
        os.getenv("SYNOLOGY_CHAT_WEBHOOK"),
    )
    for candidate in candidates:
        cleaned = _clean_webhook_url(candidate)
        if cleaned:
            return cleaned
    return ""


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

    url = _resolve_webhook_url(webhook_url)
    if not url:
        _record_attempt(success=False, message=message, error="웹훅 URL이 설정되지 않았습니다.")
        return False

    payload = {"text": message}
    form_payload = {"payload": json.dumps(payload, ensure_ascii=False)}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    created_client = False
    if client is None:
        if httpx is None:  # dependency unavailable
            _record_attempt(success=False, message=message, error="httpx 패키지가 설치되어 있지 않습니다.")
            return False
        client = httpx.Client(timeout=timeout)
        created_client = True

    try:
        response = client.post(url, data=form_payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        parsed: Optional[Any]
        try:
            parsed = response.json()
        except Exception:  # pragma: no cover - non JSON response is allowed
            parsed = None
        if isinstance(parsed, dict):
            success_flag = parsed.get("success")
            if isinstance(success_flag, str):
                success_flag = success_flag.lower() != "false"
            if success_flag is False:
                error_detail = parsed.get("error") or parsed.get("message") or "Synology Chat 응답 오류"
                raise RuntimeError(str(error_detail))
        _record_attempt(success=True, message=message, error=None)
        return True
    except Exception as exc:  # pragma: no cover - network failures handled gracefully
        _record_attempt(success=False, message=message, error=str(exc))
        return False
    finally:
        if created_client:
            client.close()


def notify_synology_chat_on_change(
    key: str,
    message: str,
    *,
    webhook_url: Optional[str] = None,
) -> bool:
    """Send ``message`` when it differs from the last payload for ``key``.

    The helper is best-effort: it quietly skips delivery if no webhook URL is
    configured and only forwards the notification when the content actually
    changed so Synology Chat 채널이 중복 메시지로 과부하되지 않는다.
    """

    url = _resolve_webhook_url(webhook_url)
    if not url:
        return False

    digest = hashlib.sha256(message.encode("utf-8", "ignore")).hexdigest()
    previous = _change_digests.get(key)
    if previous == digest:
        return False

    sent = notify_synology_chat(message, webhook_url=url)
    if sent:
        _change_digests[key] = digest
    return sent


def get_synology_chat_status() -> dict:
    return {
        "configured": bool(_resolve_webhook_url()),
        "last_attempt_at": _last_attempt_at,
        "last_success_at": _last_success_at,
        "last_error": _last_error,
        "last_message": _last_message,
    }


def _format_timestamp(value: Optional[datetime]) -> str:
    if value is None:
        return "-"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_status_for_cli() -> str:
    status = get_synology_chat_status()
    lines = [
        "Synology Chat Webhook 상태",
        "-------------------------",
        f"Configured : {'예' if status['configured'] else '아니오'}",
        f"Last Attempt: {_format_timestamp(status['last_attempt_at'])}",
        f"Last Success: {_format_timestamp(status['last_success_at'])}",
        f"Last Error  : {status['last_error'] or '-'}",
        f"Last Message: {status['last_message'] or '-'}",
    ]
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - lightweight CLI wrapper
    import argparse
    import textwrap

    parser = argparse.ArgumentParser(
        prog="python -m backend.notifications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent(
            """
            Synology Chat 웹훅 상태를 확인하거나 테스트 메시지를 전송합니다.

            환경 변수 SADO_CHAT_WEBHOOK 또는 --webhook-url 옵션 중 하나는 반드시
            설정되어 있어야 하며, 설정 후에는 다음과 같이 검증할 수 있습니다.

              python -m backend.notifications --status
              python -m backend.notifications "Sado Trade Bot 연결 테스트"
            """
        ),
    )
    parser.add_argument(
        "message",
        nargs="?",
        default="Sado Trade Bot Synology Chat 테스트",
        help="전송할 메시지 (기본값: 테스트 메시지)",
    )
    parser.add_argument(
        "--webhook-url",
        dest="webhook_url",
        help="직접 지정할 Synology Chat 웹훅 URL",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="웹훅 구성 및 최근 전송 이력을 출력만 합니다.",
    )

    args = parser.parse_args()

    if args.status:
        print(_format_status_for_cli())
        return

    success = notify_synology_chat(args.message, webhook_url=args.webhook_url)
    print(_format_status_for_cli())
    if success:
        print("\n✅ Synology Chat으로 메시지를 전송했습니다.")
    else:
        print("\n⚠️  메시지 전송에 실패했습니다. URL과 토큰을 다시 확인하세요.")


if __name__ == "__main__":  # pragma: no cover - module CLI entry point
    main()


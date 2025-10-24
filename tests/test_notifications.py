import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import json as jsonlib

from fastapi import HTTPException

from backend import notifications
import backend.app as app_module
from backend.app import post_chat_notification
from backend.schemas import ChatDigestFlushRequest, ChatDigestFlushResponse, ChatDigestReport, ChatNotificationRequest


def reset_chat_state():
    notifications._last_attempt_at = None  # type: ignore[attr-defined]
    notifications._last_success_at = None  # type: ignore[attr-defined]
    notifications._last_error = None  # type: ignore[attr-defined]
    notifications._last_message = None  # type: ignore[attr-defined]
    notifications._change_digests.clear()  # type: ignore[attr-defined]
    notifications.reset_digest_state()


def test_notify_synology_chat_with_stub_client(monkeypatch):
    reset_chat_state()
    calls = []

    class StubClient:
        def post(self, url, data=None, json=None, timeout=None, headers=None):  # noqa: ARG002
            payload = data or {}
            calls.append((url, payload.get("payload"), headers))
            text = jsonlib.loads(payload["payload"])  # type: ignore[index]
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {"success": True, "echo": text},
            )

        def close(self):
            calls.append(("close", None))

    monkeypatch.setenv("SADO_CHAT_WEBHOOK", "https://example.com/webhook")

    client = StubClient()
    result = notifications.notify_synology_chat("테스트", client=client)

    assert result is True
    assert calls[0][0] == "https://example.com/webhook"
    assert "테스트" in jsonlib.loads(calls[0][1])["text"]

    status = notifications.get_synology_chat_status()
    assert status["configured"] is True
    assert status["last_message"] == "테스트"
    assert status["last_error"] is None
    assert status["last_success_at"] is not None


def test_notify_synology_chat_strips_encoded_quotes(monkeypatch):
    reset_chat_state()
    calls = []

    class StubClient:
        def post(self, url, data=None, json=None, timeout=None, headers=None):  # noqa: ARG002
            calls.append(url)
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"success": True})

        def close(self):
            return None

    monkeypatch.setenv(
        "SADO_CHAT_WEBHOOK",
        "http://example.com/webapi/entry.cgi?token=%22ABCDEF123%22",
    )

    result = notifications.notify_synology_chat("테스트", client=StubClient())

    assert result is True
    assert calls[0] == "http://example.com/webapi/entry.cgi?token=ABCDEF123"


def test_chat_notification_endpoint(monkeypatch):
    reset_chat_state()
    received = {}

    def fake_notify(message: str, **_kwargs):
        received["message"] = message
        notifications._record_attempt(success=True, message=message, error=None)  # type: ignore[attr-defined]
        return True

    monkeypatch.setattr(notifications, "notify_synology_chat", fake_notify)

    response = post_chat_notification(ChatNotificationRequest(message="시놀로지 전송"))
    assert response["status"] == "sent"
    assert received["message"] == "시놀로지 전송"

    status = notifications.get_synology_chat_status()
    assert status["last_message"] == "시놀로지 전송"
    assert status["last_error"] is None


def test_chat_status_without_webhook(monkeypatch):
    reset_chat_state()
    monkeypatch.delenv("SADO_CHAT_WEBHOOK", raising=False)
    monkeypatch.delenv("SYNOLOGY_CHAT_WEBHOOK", raising=False)

    status = notifications.get_synology_chat_status()
    assert status["configured"] is False


def test_chat_notification_failure_returns_detail(monkeypatch):
    reset_chat_state()

    def fake_notify(message: str, **_kwargs):  # noqa: ARG001
        notifications._record_attempt(success=False, message="msg", error="timeout")  # type: ignore[attr-defined]
        return False

    monkeypatch.setattr(notifications, "notify_synology_chat", fake_notify)

    with pytest.raises(HTTPException) as exc:
        post_chat_notification(ChatNotificationRequest(message="메시지"))

    assert exc.value.status_code == 502
    assert "timeout" in exc.value.detail


def test_notify_on_change_deduplicates(monkeypatch):
    reset_chat_state()
    sent: list[str] = []

    def fake_notify(message: str, **_kwargs):
        sent.append(message)
        return True

    monkeypatch.setattr(notifications, "notify_synology_chat", fake_notify)
    monkeypatch.setenv("SADO_CHAT_WEBHOOK", "https://example.com/webhook")

    first = notifications.notify_synology_chat_on_change("market", "KRW-BTC 매수 집중")
    second = notifications.notify_synology_chat_on_change("market", "KRW-BTC 매수 집중")
    third = notifications.notify_synology_chat_on_change("market", "KRW-ETH 관망")

    assert first is True
    assert second is False
    assert third is True
    assert sent == ["KRW-BTC 매수 집중", "KRW-ETH 관망"]


def test_push_chat_summary_filters_non_critical(monkeypatch):
    reset_chat_state()
    captured: list[tuple[str, str]] = []

    def fake_notify(key: str, message: str):
        captured.append((key, message))
        return True

    monkeypatch.setattr(app_module.notifications, "notify_synology_chat_on_change", fake_notify)

    app_module._push_chat_summary("ai-copilot", "[AI 코파일럿] KRW-BTC 분석")
    assert captured == []

    app_module._push_chat_summary("autopilot-status", "[오토파일럿] ON | 시장 KRW-BTC")
    assert captured == []

    app_module._push_chat_summary("autopilot-status", "[오토파일럿] 최근 체결 매수 체결")
    assert captured == [("autopilot-status", "[오토파일럿] 최근 체결 매수 체결")]


def test_resolve_webhook_url_reads_env_file(tmp_path, monkeypatch):
    reset_chat_state()
    monkeypatch.delenv("SADO_CHAT_WEBHOOK", raising=False)
    monkeypatch.delenv("SYNOLOGY_CHAT_WEBHOOK", raising=False)

    env_file = tmp_path / "custom.env"
    env_file.write_text("SADO_CHAT_WEBHOOK=https://example.com/from-env\n")

    monkeypatch.setenv("SADO_ENV_FILE", str(env_file))
    notifications._reset_env_cache()  # type: ignore[attr-defined]

    resolved = notifications._resolve_webhook_url()  # type: ignore[attr-defined]
    assert resolved == "https://example.com/from-env"

    notifications._reset_env_cache()  # type: ignore[attr-defined]


def test_ensure_env_from_file_populates_missing_keys(tmp_path, monkeypatch):
    reset_chat_state()
    monkeypatch.delenv("SADO_CHAT_WEBHOOK", raising=False)
    monkeypatch.delenv("UPBIT_BASE_URL", raising=False)

    env_file = tmp_path / "bot.env"
    env_file.write_text(
        "\n".join(
            [
                "SADO_CHAT_WEBHOOK=https://example.com/from-env",
                "UPBIT_BASE_URL=https://proxy.upbit.local/api/",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("SADO_ENV_FILE", str(env_file))
    notifications._reset_env_cache()  # type: ignore[attr-defined]

    loaded = notifications.ensure_env_from_file()

    assert loaded["SADO_CHAT_WEBHOOK"] == "https://example.com/from-env"
    assert os.getenv("SADO_CHAT_WEBHOOK") == "https://example.com/from-env"
    assert os.getenv("UPBIT_BASE_URL") == "https://proxy.upbit.local/api/"

    notifications._reset_env_cache()  # type: ignore[attr-defined]


def test_digest_enqueue_and_flush(monkeypatch):
    reset_chat_state()
    captured: list[str] = []

    def fake_notifier(message: str) -> bool:
        captured.append(message)
        return True

    base_time = datetime(2024, 1, 5, 12, tzinfo=timezone.utc)
    notifications.enqueue_digest(
        "recommendations",
        "12:00 · minute60\n1. KRW-BTC · 롱",
        timestamp=base_time,
    )

    reports = notifications.flush_due_digests(
        now=base_time.replace(day=6),
        notifier=fake_notifier,
    )

    assert len(reports) == 1
    assert "KRW-BTC" in captured[0]
    assert reports[0]["sent"] is True


def test_digest_flush_endpoint(monkeypatch):
    reset_chat_state()

    dispatched: list[dict] = []

    def fake_flush(**kwargs):  # noqa: D401
        dispatched.append(kwargs)
        return [
            {
                "category": "recommendations",
                "date": "2024-01-05",
                "message": "[일일 리포트] AI 추천 리포트",
                "sent": True,
            }
        ]

    monkeypatch.setattr(app_module.notifications, "flush_due_digests", fake_flush)

    response = app_module.flush_chat_digest(ChatDigestFlushRequest(category="recommendations", force=True))
    assert isinstance(response, ChatDigestFlushResponse)
    assert response.reports == [
        ChatDigestReport(
            category="recommendations",
            date=datetime(2024, 1, 5, tzinfo=timezone.utc).date(),
            sent=True,
            message_preview="[일일 리포트] AI 추천 리포트",
        )
    ]

    assert dispatched[0]["category"] == "recommendations"
    assert dispatched[0]["force"] is True

from types import SimpleNamespace

from backend import notifications
from backend.app import post_chat_notification
from backend.schemas import ChatNotificationRequest


def reset_chat_state():
    notifications._last_attempt_at = None  # type: ignore[attr-defined]
    notifications._last_success_at = None  # type: ignore[attr-defined]
    notifications._last_error = None  # type: ignore[attr-defined]
    notifications._last_message = None  # type: ignore[attr-defined]


def test_notify_synology_chat_with_stub_client(monkeypatch):
    reset_chat_state()
    calls = []

    class StubClient:
        def post(self, url, json, timeout):
            calls.append((url, json["text"]))
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None)

        def close(self):
            calls.append(("close", None))

    monkeypatch.setenv("SADO_CHAT_WEBHOOK", "https://example.com/webhook")

    client = StubClient()
    result = notifications.notify_synology_chat("테스트", client=client)

    assert result is True
    assert calls[0][0] == "https://example.com/webhook"
    assert "테스트" in calls[0][1]

    status = notifications.get_synology_chat_status()
    assert status["configured"] is True
    assert status["last_message"] == "테스트"
    assert status["last_error"] is None
    assert status["last_success_at"] is not None


def test_notify_synology_chat_strips_encoded_quotes(monkeypatch):
    reset_chat_state()
    calls = []

    class StubClient:
        def post(self, url, json, timeout):  # noqa: ARG002
            calls.append(url)
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None)

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

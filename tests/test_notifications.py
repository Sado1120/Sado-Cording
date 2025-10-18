from types import SimpleNamespace

from backend import notifications
from backend.app import post_chat_notification
from backend.schemas import ChatNotificationRequest


def test_notify_synology_chat_with_stub_client(monkeypatch):
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


def test_chat_notification_endpoint(monkeypatch):
    received = {}

    def fake_notify(message: str, **_kwargs):
        received["message"] = message
        return True

    monkeypatch.setattr(notifications, "notify_synology_chat", fake_notify)

    response = post_chat_notification(ChatNotificationRequest(message="시놀로지 전송"))
    assert response["status"] == "sent"
    assert received["message"] == "시놀로지 전송"

import pytest

import backend.log_utils as log_utils
import backend.app as app_module

try:  # pragma: no cover - optional dependency
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - FastAPI client unavailable
    TestClient = None  # type: ignore[assignment]


def test_log_event_persists_to_tail(tmp_path, monkeypatch):
    log_utils.reset_logging_for_tests()
    monkeypatch.setenv("SADO_LOG_DIR", str(tmp_path))

    log_utils.log_event("unit-test.event", detail="ok")
    entries = log_utils.read_log_tail()

    assert entries, "structured log tail should not be empty"
    assert entries[-1]["event"] == "unit-test.event"
    assert entries[-1]["detail"] == "ok"


def test_diagnostics_logs_endpoint_returns_entries(tmp_path, monkeypatch):
    log_utils.reset_logging_for_tests()
    monkeypatch.setenv("SADO_LOG_DIR", str(tmp_path))
    log_utils.log_event("api-log.before", source="test")

    if TestClient is None:
        pytest.skip("fastapi TestClient unavailable")

    client = TestClient(app_module.app)
    log_utils.log_event("api-log.after", source="test")

    response = client.get("/diagnostics/logs", params={"limit": 5})
    assert response.status_code == 200

    payload = response.json()
    assert payload["entries"], "log endpoint should return entries"
    events = [entry["event"] for entry in payload["entries"]]
    assert "api-log.after" in events or "api-log.before" in events
    assert payload["log_path"].endswith(".log")

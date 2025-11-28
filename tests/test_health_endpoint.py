import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from backend.app import app


def test_health_is_public():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json().get("status") == "ok"

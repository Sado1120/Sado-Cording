from __future__ import annotations

import pytest

try:  # pragma: no cover - optional dependency in tests
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - fallback when FastAPI optional deps missing
    TestClient = None  # type: ignore[assignment]


def _make_client():
    if TestClient is None:  # pragma: no cover - FastAPI client unavailable
        pytest.skip("fastapi TestClient unavailable")
    from backend import app as app_module

    return TestClient(app_module.app)


def test_core_dashboard_endpoints_operate_without_network(monkeypatch):
    client = _make_client()
    monkeypatch.setenv("UPBIT_ENABLE_NETWORK", "0")

    sim_response = client.post(
        "/strategies/simulate",
        json={"market": "KRW-BTC", "use_live_data": False},
    )
    assert sim_response.status_code == 200
    sim_data = sim_response.json()
    assert sim_data["equity_curve"], "시뮬레이션 결과가 비어 있습니다."

    reco_response = client.get("/market/recommendations?base=KRW&interval=minute60&limit=3")
    assert reco_response.status_code == 200
    reco_data = reco_response.json()
    assert reco_data["recommendations"], "AI 추천 결과가 없습니다."

    ai_response = client.get("/ai/market/intelligence?market=KRW-BTC&interval=minute60")
    assert ai_response.status_code == 200
    ai_data = ai_response.json()
    assert ai_data["market"] == "KRW-BTC"

    paper_response = client.get("/trading/paper/status")
    assert paper_response.status_code == 200
    paper_data = paper_response.json()
    assert "heartbeat_state" in paper_data

    portfolio_response = client.post(
        "/ai/portfolio/optimize",
        json={"capital": 10_000_000, "risk_appetite": 0.5},
    )
    assert portfolio_response.status_code == 200
    portfolio_data = portfolio_response.json()
    assert portfolio_data["allocations"], "포트폴리오 배분 결과가 비어 있습니다."

    # Clean up to avoid leaking state into other tests.
    monkeypatch.delenv("UPBIT_ENABLE_NETWORK", raising=False)

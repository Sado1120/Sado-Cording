from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from backend import ai
from backend.autopilot import AutoTrader, AutoTraderConfig
from backend.execution import PaperBroker
from backend.market import MarketData
from backend.schemas import OrderMode
from backend.trading import Candle

import backend.app as app_module


def _make_market_data(count: int = 120) -> MarketData:
    base_time = datetime.now(timezone.utc) - timedelta(minutes=count)
    candles = []
    price = 10_000_000.0
    for index in range(count):
        timestamp = base_time + timedelta(minutes=index)
        close = price + index * 12_000
        candles.append(
            Candle(
                timestamp=timestamp,
                open=close - 8_000,
                high=close + 9_000,
                low=close - 12_000,
                close=close,
                volume=1.5 + index * 0.01,
            )
        )
    return MarketData(candles=candles, source="synthetic")


def _fake_candle_fetcher(*, market: str, interval: str, count: int) -> MarketData:
    return _make_market_data(count)


def _fake_news_fetcher(limit: int) -> list[dict]:
    return []


def _fake_autopilot_builder(**kwargs) -> ai.AutoPilotOrderPlan:
    insight = kwargs["insight"]
    return ai.AutoPilotOrderPlan(
        market=insight.market,
        side="bid",
        bias="long",
        order_type="market",
        suggested_price=None,
        position_size_pct=5.0,
        stop_loss_pct=2.0,
        take_profit_pct=4.0,
        trailing_stop_pct=1.5,
        confidence_pct=70.0,
        reasoning=["테스트 환경 자동매수"],
        monitoring=["가격 추세 감시"],
    )


def _build_trader(notifier=lambda _message: True) -> tuple[AutoTrader, PaperBroker]:
    broker = PaperBroker()
    trader = AutoTrader(
        broker=broker,
        candle_fetcher=_fake_candle_fetcher,
        news_fetcher=_fake_news_fetcher,
        analyse_market=ai.analyse_market,
        autopilot_builder=_fake_autopilot_builder,
        portfolio_builder=lambda **_: None,
        time_provider=lambda: datetime.now(timezone.utc),
        notifier=notifier,
    )
    return trader, broker


def test_autotrader_runs_single_cycle_and_places_order():
    trader, broker = _build_trader()
    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.6,
        capital=20_000_000,
        poll_interval=600.0,
        include_portfolio=False,
        max_position_pct=0.2,
        min_confidence_pct=50.0,
    )

    state = trader.start(config)
    try:
        assert state.running is True
        assert state.last_plan is not None
        assert state.last_plan.side == "bid"
        assert state.last_execution is not None
        assert state.last_execution.side == "bid"
        assert state.logs, "오토파일럿 로그가 비어 있습니다."

        snapshot = broker.snapshot()
        assert any(pos.market == "KRW-BTC" for pos in snapshot.positions)
    finally:
        stop_state = trader.stop()
        assert stop_state.running is False


def test_autotrader_notifier_invoked():
    notifications: list[str] = []

    def notifier(message: str) -> bool:
        notifications.append(message)
        return True

    trader, _ = _build_trader(notifier=notifier)
    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.6,
        capital=15_000_000,
        poll_interval=300.0,
        include_portfolio=False,
        max_position_pct=0.25,
        min_confidence_pct=50.0,
    )

    trader.start(config)
    try:
        assert notifications, "알림이 호출되지 않았습니다."
        assert any("Sado Trade Bot" in message for message in notifications)
    finally:
        trader.stop()


def test_autopilot_api_endpoints(monkeypatch):
    trader, _ = _build_trader()
    original_trader = app_module._auto_trader
    monkeypatch.setattr(app_module, "_auto_trader", trader)
    client = TestClient(app_module.app)

    try:
        payload = {
            "mode": "paper",
            "market": "KRW-BTC",
            "interval": "minute60",
            "risk_appetite": 0.6,
            "capital": 15_000_000,
            "poll_interval": 300,
            "max_position_pct": 0.2,
            "min_confidence_pct": 50,
            "include_portfolio": False,
        }
        start_response = client.post("/trading/autopilot/start", json=payload)
        assert start_response.status_code == 200
        start_data = start_response.json()
        assert start_data["running"] is True
        assert start_data["last_plan"]["side"] == "bid"

        status_response = client.get("/trading/autopilot/status")
        assert status_response.status_code == 200
        status_data = status_response.json()
        assert status_data["config"]["market"] == "KRW-BTC"

        stop_response = client.post("/trading/autopilot/stop")
        assert stop_response.status_code == 200
        stop_data = stop_response.json()
        assert stop_data["running"] is False
    finally:
        stop_state = trader.stop()
        assert stop_state.running is False
        monkeypatch.setattr(app_module, "_auto_trader", original_trader)

from datetime import datetime, timedelta, timezone

import pytest

import backend.app as app_module

from backend import trading
from backend.app import (
    get_paper_status,
    get_trade_history,
    mark_paper,
    reset_paper,
    submit_order,
)
from fastapi import HTTPException

from backend.autopilot import AutoTraderExecution
from backend.execution import ExecutionError, PaperBroker, create_upbit_client_from_env, paper_broker
from backend.schemas import OrderMode, OrderRequest, PaperMarkRequest, PaperResetRequest
from types import SimpleNamespace


def reset_global_broker(initial_cash: float = 10_000_000):
    broker = paper_broker()
    broker.reset(initial_cash=initial_cash)
    return broker


def test_paper_broker_cycle_handles_buy_sell_and_mark():
    broker = PaperBroker(fee_rate=0.0, initial_cash=1_000_000)
    buy_snapshot = broker.submit_order(
        market="KRW-BTC", side="bid", price=1_000_000, volume=0.5
    )
    assert pytest.approx(buy_snapshot.cash, rel=1e-6) == 500_000
    assert buy_snapshot.positions[0].market == "KRW-BTC"
    assert buy_snapshot.positions[0].volume == pytest.approx(0.5)

    broker.mark_price(market="KRW-BTC", price=1_100_000)
    status = broker.snapshot()
    position = status.positions[0]
    assert position.market_price == pytest.approx(1_100_000)
    assert position.unrealized_pnl == pytest.approx(50_000)

    sell_snapshot = broker.submit_order(
        market="KRW-BTC", side="ask", price=1_150_000, volume=0.5
    )
    assert sell_snapshot.cash == pytest.approx(1_075_000)
    assert not sell_snapshot.positions
    assert sell_snapshot.orders[0].realized_pnl == pytest.approx(75_000)


def test_paper_endpoints_support_reset_mark_and_order():
    reset_paper(PaperResetRequest(initial_cash=5_000_000))
    order_response = submit_order(
        OrderRequest(
            mode=OrderMode.PAPER,
            market="KRW-BTC",
            side="bid",
            ord_type="limit",
            price=1_000_000,
            volume=0.1,
        )
    )
    assert order_response.mode is OrderMode.PAPER
    assert order_response.balance.positions
    first_timestamp = order_response.balance.last_updated

    mark_response = mark_paper(PaperMarkRequest(market="KRW-BTC", price=1_050_000))
    assert mark_response.positions[0].market_price == 1_050_000
    assert mark_response.last_updated >= first_timestamp
    assert mark_response.price_source == "manual"
    assert mark_response.market == "KRW-BTC"
    assert mark_response.interval == "minute1"

    status_response = get_paper_status()
    assert status_response.portfolio_value > 0
    assert status_response.last_updated >= mark_response.last_updated
    assert status_response.last_updated <= datetime.now(timezone.utc)
    assert status_response.price_source in {"manual", "synthetic", "upbit"}
    assert status_response.market == "KRW-BTC"
    assert status_response.interval == "minute1"
    assert status_response.heartbeat_state in {"online", "warning", "offline"}
    assert status_response.heartbeat_reason in {"live", "delayed", "manual", "synthetic", "stale"}


def test_get_paper_status_refreshes_market(monkeypatch):
    reset_paper(PaperResetRequest(initial_cash=2_000_000))

    class DummyData:
        def __init__(self, price: float):
            self.candles = [
                trading.Candle(
                    timestamp=datetime.now(timezone.utc),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=1.0,
                )
            ]
            self.source = "upbit"

    captured = {}

    def fake_fetch(*, market: str, interval: str, count: int):  # noqa: ARG001
        captured["market"] = market
        captured["interval"] = interval
        return DummyData(price=31_000_000)

    monkeypatch.setattr("backend.app.fetch_upbit_candles", fake_fetch)

    first = get_paper_status(market="krw-eth", interval="minute15")
    second = get_paper_status(market="KRW-ETH", interval="minute15")

    assert captured["market"] == "KRW-ETH"
    assert captured["interval"] == "minute15"
    assert second.last_updated >= first.last_updated
    assert second.last_updated <= datetime.now(timezone.utc)
    assert first.price_source == "upbit"
    assert second.price_source == "upbit"
    assert first.market == "KRW-ETH"
    assert first.interval == "minute15"
    assert second.market == "KRW-ETH"
    assert second.interval == "minute15"
    assert first.heartbeat_state in {"online", "warning", "offline"}
    assert second.heartbeat_state in {"online", "warning", "offline"}


def test_get_paper_status_auto_refreshes_stale_snapshot(monkeypatch):
    broker = reset_global_broker()
    broker.last_update = datetime.now(timezone.utc) - timedelta(hours=8)
    broker.last_prices.clear()

    class DummyData:
        def __init__(self, price: float):
            self.candles = [
                trading.Candle(
                    timestamp=datetime.now(timezone.utc),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=1.0,
                )
            ]
            self.source = "upbit"

    calls = {"count": 0}

    def fake_fetch(*, market: str, interval: str, count: int):  # noqa: ARG001
        calls["count"] += 1
        return DummyData(price=32_000_000)

    monkeypatch.setattr("backend.app.fetch_upbit_candles", fake_fetch)

    status = get_paper_status()

    assert calls["count"] >= 1
    assert status.price_source == "upbit"
    assert status.last_updated >= datetime.now(timezone.utc) - timedelta(minutes=1)
    assert status.heartbeat_state in {"online", "warning"}
    assert status.heartbeat_reason in {"live", "delayed"}


def test_get_trade_history_combines_paper_and_autopilot(monkeypatch):
    broker = reset_global_broker(initial_cash=5_000_000)
    broker.submit_order(market="KRW-BTC", side="bid", price=25_000_000, volume=0.1)

    executed_at = datetime.now(timezone.utc)
    fake_execution = AutoTraderExecution(
        mode=OrderMode.PAPER,
        market="KRW-ETH",
        side="ask",
        price=1_900_000,
        volume=0.2,
        value=380_000,
        executed_at=executed_at,
        detail="테스트 자동 체결",
    )

    class DummyTrader:
        def status(self):
            return SimpleNamespace(executions=[fake_execution])

    import backend.app as app_module

    original_trader = app_module._auto_trader
    monkeypatch.setattr(app_module, "_auto_trader", DummyTrader())

    try:
        history = get_trade_history(limit=10)
    finally:
        monkeypatch.setattr(app_module, "_auto_trader", original_trader)

    assert history.items, "거래 내역이 비어 있습니다."
    assert any(item.source == "paper" for item in history.items)
    assert any(item.source == "autopilot" for item in history.items)
    timestamps = [item.executed_at for item in history.items]
    assert timestamps == sorted(timestamps, reverse=True)


def test_market_orders_use_marked_price_and_hide_empty_positions():
    broker = PaperBroker(fee_rate=0.0, initial_cash=1_000_000)

    broker.mark_price(market="KRW-XRP", price=500)
    snapshot = broker.snapshot()
    assert not any(pos.market == "KRW-XRP" for pos in snapshot.positions)

    buy_snapshot = broker.submit_order(
        market="KRW-XRP", side="bid", price=None, volume=100, ord_type="market"
    )
    assert buy_snapshot.positions[0].average_price == pytest.approx(500)
    assert buy_snapshot.cash == pytest.approx(950_000)

    broker.mark_price(market="KRW-XRP", price=520)
    sell_snapshot = broker.submit_order(
        market="KRW-XRP", side="ask", price=None, volume=100, ord_type="market"
    )
    assert sell_snapshot.cash == pytest.approx(1_002_000)
    assert not sell_snapshot.positions


def test_submit_market_order_without_price_triggers_refresh(monkeypatch):
    reset_paper(PaperResetRequest(initial_cash=5_000_000))

    broker = paper_broker()
    broker.last_prices.clear()

    class DummyData:
        def __init__(self, price: float):
            self.candles = [
                trading.Candle(
                    timestamp=datetime.now(timezone.utc),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=10.0,
                )
            ]
            self.source = "synthetic"

    monkeypatch.setattr(
        "backend.app.fetch_upbit_candles",
        lambda market="KRW-ETH", interval="minute1", count=1: DummyData(price=2_500_000),
    )

    response = submit_order(
        OrderRequest(
            mode=OrderMode.PAPER,
            market="KRW-ETH",
            side="bid",
            ord_type="market",
            price=None,
            volume=0.1,
        )
    )

    assert response.status == "filled"
    assert response.balance.positions
    assert response.balance.positions[0].market == "KRW-ETH"
    assert response.balance.positions[0].volume == pytest.approx(0.1)


def test_submit_order_retries_after_execution_error(monkeypatch):
    reset_paper(PaperResetRequest(initial_cash=5_000_000))

    broker = app_module._paper_broker
    original_submit = broker.submit_order
    submit_calls = {"count": 0}

    def flaky_submit(**kwargs):
        submit_calls["count"] += 1
        if submit_calls["count"] == 1:
            raise ExecutionError("시장가 주문을 실행하려면 최신 시세를 먼저 동기화하세요.")
        return original_submit(**kwargs)

    monkeypatch.setattr(broker, "submit_order", flaky_submit)

    refresh_calls = {"count": 0}
    original_refresh = app_module._refresh_paper_market

    def tracking_refresh(*, market, interval):
        refresh_calls["count"] += 1
        return original_refresh(market=market, interval=interval)

    monkeypatch.setattr(app_module, "_refresh_paper_market", tracking_refresh)

    response = submit_order(
        OrderRequest(
            mode=OrderMode.PAPER,
            market="KRW-XRP",
            side="bid",
            ord_type="market",
            price=None,
            volume=0.2,
        )
    )

    assert response.status == "filled"
    assert submit_calls["count"] >= 2
    assert refresh_calls["count"] >= 1


def test_live_order_requires_keys(monkeypatch):
    reset_global_broker()
    monkeypatch.delenv("UPBIT_ACCESS_KEY", raising=False)
    monkeypatch.delenv("UPBIT_SECRET_KEY", raising=False)

    with pytest.raises(HTTPException) as exc:
        submit_order(
            OrderRequest(
                mode=OrderMode.LIVE,
                market="KRW-BTC",
                side="bid",
                ord_type="limit",
                price=1_000_000,
                volume=0.01,
            )
        )
    assert exc.value.status_code == 400
    assert "UPBIT" in exc.value.detail


def test_create_upbit_client_from_env_requires_credentials(monkeypatch):
    monkeypatch.delenv("UPBIT_ACCESS_KEY", raising=False)
    monkeypatch.delenv("UPBIT_SECRET_KEY", raising=False)
    with pytest.raises(ExecutionError):
        create_upbit_client_from_env()

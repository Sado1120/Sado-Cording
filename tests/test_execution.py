from datetime import datetime

import pytest
from datetime import datetime

import pytest

from backend import trading
from backend.app import (
    get_paper_status,
    mark_paper,
    reset_paper,
    submit_order,
)
from fastapi import HTTPException

from backend.execution import ExecutionError, PaperBroker, create_upbit_client_from_env, paper_broker
from backend.schemas import OrderMode, OrderRequest, PaperMarkRequest, PaperResetRequest


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
    assert status_response.last_updated <= datetime.utcnow()
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
                    timestamp=datetime.utcnow(),
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
    assert second.last_updated <= datetime.utcnow()
    assert first.price_source == "upbit"
    assert second.price_source == "upbit"
    assert first.market == "KRW-ETH"
    assert first.interval == "minute15"
    assert second.market == "KRW-ETH"
    assert second.interval == "minute15"
    assert first.heartbeat_state in {"online", "warning", "offline"}
    assert second.heartbeat_state in {"online", "warning", "offline"}


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
                    timestamp=datetime.utcnow(),
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

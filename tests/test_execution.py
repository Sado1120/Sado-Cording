from datetime import datetime

import pytest
from fastapi import HTTPException

from backend.app import (
    get_paper_status,
    mark_paper,
    reset_paper,
    submit_order,
)
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

    status_response = get_paper_status()
    assert status_response.portfolio_value > 0
    assert status_response.last_updated >= mark_response.last_updated
    assert status_response.last_updated <= datetime.utcnow()


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

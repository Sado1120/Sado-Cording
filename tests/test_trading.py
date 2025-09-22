from datetime import datetime

import pytest

from backend import trading


def test_generate_synthetic_prices_len_and_sorting():
    candles = trading.generate_synthetic_prices(days=10, seed=42)
    assert len(candles) == 10
    timestamps = [c.timestamp for c in candles]
    assert timestamps == sorted(timestamps)


def test_run_ema_strategy_generates_trades():
    candles = trading.generate_synthetic_prices(days=60, seed=7)
    report = trading.run_ema_strategy(candles, fast_period=8, slow_period=21)
    assert report.trades  # strategy should have at least one trade
    assert report.total_return_pct != 0
    assert report.max_drawdown_pct >= 0


def test_rebalance_portfolio_orders_sum_to_zero():
    orders = trading.rebalance_portfolio(
        current_positions={"SPY": 2000000, "BTC": 1000000},
        target_allocations={"SPY": 0.5, "BTC": 0.3, "ETH": 0.2},
        portfolio_value=4_000_000,
    )
    total_current = 3_000_000
    assert pytest.approx(sum(orders.values()), abs=1e-6) == 4_000_000 - total_current
    assert orders["ETH"] > 0


def test_summarize_trades_handles_empty():
    summary = trading.summarize_trades([])
    assert summary == {"count": 0, "win_rate": 0.0, "avg_return_pct": 0.0}


def test_run_ema_strategy_raises_when_fast_not_slower():
    candles = [
        trading.Candle(
            timestamp=datetime.utcnow(),
            open=1,
            high=1,
            low=1,
            close=1,
            volume=1,
        )
    ]
    with pytest.raises(ValueError):
        trading.run_ema_strategy(candles, fast_period=30, slow_period=10)

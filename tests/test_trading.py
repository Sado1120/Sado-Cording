from datetime import datetime, timedelta, timezone
import math

import pytest

import backend.app as app_module
from backend import trading
from backend.schemas import SimulationRequest


def test_generate_synthetic_prices_len_and_sorting():
    candles = trading.generate_synthetic_prices(days=10, seed=42)
    assert len(candles) == 10
    timestamps = [c.timestamp for c in candles]
    assert timestamps == sorted(timestamps)


def test_run_ema_strategy_generates_trades():
    candles = trading.generate_synthetic_prices(days=60, seed=7)
    report = trading.run_ema_strategy(candles, fast_period=8, slow_period=21, market="KRW-BTC")
    assert report.trades  # strategy should have at least one trade
    assert report.total_return_pct != 0
    assert report.max_drawdown_pct >= 0
    assert report.ulcer_index >= 0
    assert report.market == "KRW-BTC"
    assert all(trade.market == "KRW-BTC" for trade in report.trades)


def test_run_ema_strategy_reports_risk_metrics():
    candles = trading.generate_synthetic_prices(days=90, seed=11)
    report = trading.run_ema_strategy(candles)

    assert report.volatility_pct >= 0
    assert report.exposure_time_pct >= 0
    assert report.exposure_time_pct <= 100
    # Sharpe and Sortino ratios should be finite numbers even when returns are flat
    assert not math.isnan(report.sharpe_ratio)
    assert not math.isnan(report.sortino_ratio)
    assert not math.isnan(report.calmar_ratio)
    assert not math.isnan(report.profit_factor)
    assert not math.isnan(report.expectancy_pct)
    assert report.avg_trade_duration_bars >= 0
    assert math.isfinite(report.value_at_risk_pct)
    assert report.downside_deviation_pct >= 0
    assert math.isfinite(report.recovery_factor)
    assert math.isfinite(report.tail_ratio)
    assert math.isfinite(report.omega_ratio)
    assert math.isfinite(report.kelly_fraction_pct)
    assert report.max_consecutive_wins >= 0
    assert report.max_consecutive_losses >= 0
    assert not math.isnan(report.skewness)
    assert not math.isnan(report.kurtosis)
    assert report.average_drawdown_pct >= 0
    assert report.pain_index >= 0
    assert report.max_runup_pct >= 0
    assert report.average_drawdown_pct <= report.max_drawdown_pct + 1e-6
    assert 0.0 <= report.integrity_score <= 100.0
    assert isinstance(report.integrity_flags, list)
    assert set(report.monte_carlo_summary.keys()) == {
        "median_return_pct",
        "p05_return_pct",
        "p95_return_pct",
        "average_return_pct",
    }


def test_run_ema_strategy_reports_equity_totals():
    candles = trading.generate_synthetic_prices(days=45, seed=21)
    report = trading.run_ema_strategy(candles, initial_capital=2_000_000)

    assert report.initial_capital == 2_000_000
    assert pytest.approx(report.ending_equity, rel=1e-6) == pytest.approx(
        report.equity_curve[-1], rel=1e-6
    )
    assert isinstance(report.ending_equity, float)


def test_simulate_strategy_returns_profit_fields():
    payload = SimulationRequest(use_live_data=False, seed=99, market="KRW-BTC")
    response = app_module.simulate_strategy(payload)

    assert response.initial_capital == pytest.approx(payload.initial_capital)
    expected_profit = response.ending_equity - response.initial_capital
    assert response.profit_krw == pytest.approx(expected_profit)
    assert response.price_source in {"synthetic", "upbit", "manual"}
    assert response.market in {None, "KRW-BTC"}
    assert 0 <= response.integrity_score <= 100
    assert isinstance(response.integrity_flags, list)


def test_rebalance_portfolio_orders_sum_to_zero():
    orders = trading.rebalance_portfolio(
        current_positions={"SPY": 2000000, "BTC": 1000000},
        target_allocations={"SPY": 0.5, "BTC": 0.3, "ETH": 0.2},
        portfolio_value=4_000_000,
    )
    total_current = 3_000_000
    assert pytest.approx(sum(orders.values()), abs=1e-6) == 4_000_000 - total_current
    assert orders["ETH"] > 0


def test_rebalance_portfolio_validates_allocations():
    with pytest.raises(ValueError):
        trading.rebalance_portfolio(
            current_positions={},
            target_allocations={},
            portfolio_value=1_000_000,
        )

    with pytest.raises(ValueError):
        trading.rebalance_portfolio(
            current_positions={},
            target_allocations={"SPY": -0.2, "QQQ": 1.2},
            portfolio_value=1_000_000,
        )

    with pytest.raises(ValueError):
        trading.rebalance_portfolio(
            current_positions={},
            target_allocations={"SPY": 0.4, "QQQ": 0.4},
            portfolio_value=1_000_000,
        )


def test_summarize_trades_handles_empty():
    summary = trading.summarize_trades([])
    assert summary == {
        "count": 0,
        "win_rate": 0.0,
        "avg_return_pct": 0.0,
        "profit_factor": 0.0,
        "expectancy_pct": 0.0,
        "avg_duration_bars": 0.0,
        "average_win_pct": 0.0,
        "average_loss_pct": 0.0,
        "win_loss_ratio": 0.0,
        "largest_win_pct": 0.0,
        "largest_loss_pct": 0.0,
        "median_return_pct": 0.0,
        "payoff_ratio": 0.0,
    }


def test_run_ema_strategy_applies_stop_loss_and_risk_controls():
    start = datetime(2024, 1, 1)
    prices = [100, 105, 110, 80, 90, 95]
    candles = [
        trading.Candle(
            timestamp=start + timedelta(days=idx),
            open=price,
            high=price * 1.02,
            low=price * 0.7,
            close=price,
            volume=100,
        )
        for idx, price in enumerate(prices)
    ]

    report = trading.run_ema_strategy(
        candles,
        fast_period=2,
        slow_period=4,
        initial_capital=10_000,
        fee_rate=0.0005,
        risk_per_trade_pct=0.02,
        stop_loss_pct=0.05,
    )

    assert report.trades, "Strategy should create at least one trade"
    trade = report.trades[0]
    # Stop loss should cap downside at the defined threshold
    assert pytest.approx(trade.exit_price, rel=1e-6) == trade.entry_price * (1 - 0.05)
    # Duration bars should be tracked for professional reporting
    assert trade.duration_bars >= 0
    # Quantity should respect risk budget (2% of equity with 5% stop)
    max_qty_by_risk = (10_000 * 0.02) / (trade.entry_price * 0.05)
    assert trade.quantity <= max_qty_by_risk + 1e-6
    assert trade.exit_reason == "stop_loss"


def test_run_ema_strategy_honors_take_profit_and_trailing():
    start = datetime(2024, 2, 1)
    prices = [100, 108, 120, 140, 150, 160]
    candles = [
        trading.Candle(
            timestamp=start + timedelta(days=idx),
            open=price,
            high=price * 1.03,
            low=price * 0.98,
            close=price,
            volume=120,
        )
        for idx, price in enumerate(prices)
    ]

    report = trading.run_ema_strategy(
        candles,
        fast_period=2,
        slow_period=4,
        initial_capital=10_000,
        fee_rate=0.0005,
        risk_per_trade_pct=0.05,
        stop_loss_pct=0.05,
        take_profit_pct=0.1,
        trailing_stop_pct=0.04,
    )

    assert report.trades
    exit_reasons = {trade.exit_reason for trade in report.trades}
    assert "take_profit" in exit_reasons or "trailing_stop" in exit_reasons


def test_run_ema_strategy_raises_when_fast_not_slower():
    candles = [
        trading.Candle(
            timestamp=datetime.now(timezone.utc),
            open=1,
            high=1,
            low=1,
            close=1,
            volume=1,
        )
    ]
    with pytest.raises(ValueError):
        trading.run_ema_strategy(candles, fast_period=30, slow_period=10)


def test_design_risk_budgeted_portfolio_balances_buckets():
    plan = trading.design_risk_budgeted_portfolio(
        capital=10_000_000,
        risk_profile=0.6,
        stable_assets=[
            {
                "symbol": "BND",
                "weight": 0.7,
                "expected_return_pct": 4.0,
                "expected_volatility_pct": 5.0,
            },
            {
                "symbol": "JEPI",
                "weight": 0.3,
                "expected_return_pct": 6.2,
                "expected_volatility_pct": 7.4,
            },
        ],
        aggressive_assets=[
            {
                "symbol": "BTC",
                "weight": 0.5,
                "expected_return_pct": 35.0,
                "expected_volatility_pct": 65.0,
            },
            {
                "symbol": "ETH",
                "weight": 0.5,
                "expected_return_pct": 25.0,
                "expected_volatility_pct": 55.0,
            },
        ],
    )

    total_weight = sum(item["weight_pct"] for item in plan["allocations"])
    assert pytest.approx(total_weight, abs=1e-6) == 100

    stable_amount = sum(item["amount"] for item in plan["allocations"] if item["bucket"] == "stable")
    aggressive_amount = sum(item["amount"] for item in plan["allocations"] if item["bucket"] == "aggressive")
    assert pytest.approx(stable_amount + aggressive_amount, abs=1e-2) == 10_000_000
    assert pytest.approx(stable_amount, rel=1e-3) == 4_000_000  # 40% stable bucket
    assert pytest.approx(aggressive_amount, rel=1e-3) == 6_000_000

    summary = plan["summary"]
    assert summary["expected_return_pct"] > 0
    assert summary["expected_volatility_pct"] > 0


def test_design_risk_budgeted_portfolio_handles_single_bucket():
    plan = trading.design_risk_budgeted_portfolio(
        capital=5_000_000,
        risk_profile=1.0,
        stable_assets=[
            {
                "symbol": "BIL",
                "weight": 1.0,
                "expected_return_pct": 3.2,
                "expected_volatility_pct": 1.2,
            }
        ],
        aggressive_assets=[],
    )

    stable_amount = sum(item["amount"] for item in plan["allocations"] if item["bucket"] == "stable")
    assert pytest.approx(stable_amount, abs=1e-2) == 5_000_000
    assert plan["summary"]["bucket_weights_pct"]["stable"] == 100
    assert plan["summary"]["bucket_weights_pct"]["aggressive"] == 0

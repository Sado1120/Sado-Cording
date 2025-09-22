"""Core trading and portfolio logic for the ILJIN Copilot project."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence
import math
import random


@dataclass
class Candle:
    """Represents a single OHLCV candle."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Trade:
    """Captures the lifecycle of a single trade."""

    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.quantity

    @property
    def return_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        return (self.exit_price - self.entry_price) / self.entry_price * 100


@dataclass
class StrategyReport:
    """Summary of the strategy simulation."""

    trades: List[Trade]
    equity_curve: List[float]
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float


def generate_synthetic_prices(
    *,
    days: int = 120,
    base_price: float = 1_000_000,
    daily_volatility: float = 0.035,
    seed: Optional[int] = None,
) -> List[Candle]:
    """Generate pseudo-random price data that mimics a trending coin market.

    The generator is deterministic when a ``seed`` is provided which is very
    useful for testing and documentation examples.
    """

    if days <= 0:
        raise ValueError("`days` must be positive")

    rng = random.Random(seed)
    timestamp = datetime.utcnow() - timedelta(days=days)
    price = base_price
    candles: List[Candle] = []

    for _ in range(days):
        # Introduce gentle drift and random shocks to mimic trending markets.
        drift = rng.uniform(-0.01, 0.018)
        shock = rng.gauss(0, daily_volatility)
        price = max(1.0, price * (1 + drift + shock))
        high = price * (1 + rng.uniform(0, 0.02))
        low = price * (1 - rng.uniform(0, 0.02))
        open_price = price * (1 - rng.uniform(-0.01, 0.01))
        volume = rng.uniform(50, 250)
        candles.append(
            Candle(
                timestamp=timestamp,
                open=open_price,
                high=high,
                low=low,
                close=price,
                volume=volume,
            )
        )
        timestamp += timedelta(days=1)

    return candles


def _ema(prices: Sequence[float], period: int) -> List[float]:
    if period <= 0:
        raise ValueError("EMA period must be positive")
    if not prices:
        return []

    multiplier = 2 / (period + 1)
    ema_values = [prices[0]]
    for price in prices[1:]:
        ema_values.append((price - ema_values[-1]) * multiplier + ema_values[-1])
    return ema_values


def _max_drawdown(equity_curve: Sequence[float]) -> float:
    peak = -math.inf
    max_dd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak <= 0:
            continue
        drawdown = (peak - value) / peak * 100
        max_dd = max(max_dd, drawdown)
    return max_dd


def run_ema_strategy(
    candles: Sequence[Candle],
    *,
    fast_period: int = 12,
    slow_period: int = 26,
    initial_capital: float = 5_000_000,
    fee_rate: float = 0.0005,
) -> StrategyReport:
    """Execute a simple EMA crossover strategy on the provided candles."""

    if fast_period >= slow_period:
        raise ValueError("fast_period must be smaller than slow_period")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")

    closes = [candle.close for candle in candles]
    fast = _ema(closes, fast_period)
    slow = _ema(closes, slow_period)

    trades: List[Trade] = []
    equity_curve: List[float] = []
    cash = initial_capital
    position_qty = 0.0
    entry_price = 0.0

    for idx in range(len(candles)):
        price = closes[idx]
        equity = cash + position_qty * price
        equity_curve.append(equity)

        if idx == 0 or idx >= len(fast) or idx >= len(slow):
            continue

        # Determine signals: bullish crossover -> buy, bearish -> sell
        prev_fast = fast[idx - 1]
        prev_slow = slow[idx - 1]
        current_fast = fast[idx]
        current_slow = slow[idx]

        crossed_up = prev_fast <= prev_slow and current_fast > current_slow
        crossed_down = prev_fast >= prev_slow and current_fast < current_slow

        if crossed_up and cash > 0:
            position_qty = (cash / price) * (1 - fee_rate)
            entry_price = price
            cash = 0.0
        elif crossed_down and position_qty > 0:
            gross = position_qty * price
            fee = gross * fee_rate
            cash = gross - fee
            trades.append(
                Trade(
                    entry_time=candles[idx - 1].timestamp,
                    exit_time=candles[idx].timestamp,
                    entry_price=entry_price,
                    exit_price=price,
                    quantity=position_qty,
                )
            )
            position_qty = 0.0

    # Liquidate any remaining position at the final price
    if position_qty > 0:
        final_price = closes[-1]
        gross = position_qty * final_price
        fee = gross * fee_rate
        cash = gross - fee
        trades.append(
            Trade(
                entry_time=candles[-2].timestamp if len(candles) >= 2 else candles[-1].timestamp,
                exit_time=candles[-1].timestamp,
                entry_price=entry_price,
                exit_price=final_price,
                quantity=position_qty,
            )
        )
        position_qty = 0.0

    equity_curve.append(cash)
    total_return_pct = (cash - initial_capital) / initial_capital * 100

    days = max((candles[-1].timestamp - candles[0].timestamp).days, 1)
    annualized_return_pct = ((1 + total_return_pct / 100) ** (365 / days) - 1) * 100
    max_drawdown_pct = _max_drawdown(equity_curve)

    return StrategyReport(
        trades=trades,
        equity_curve=equity_curve,
        total_return_pct=total_return_pct,
        annualized_return_pct=annualized_return_pct,
        max_drawdown_pct=max_drawdown_pct,
    )


def rebalance_portfolio(
    *,
    current_positions: Dict[str, float],
    target_allocations: Dict[str, float],
    portfolio_value: float,
) -> Dict[str, float]:
    """Suggest trade amounts to rebalance the portfolio to target weights.

    Returns a dictionary mapping ticker symbols to the amount of currency that
    should be bought (positive) or sold (negative).
    """

    if portfolio_value <= 0:
        raise ValueError("portfolio_value must be positive")
    if not math.isclose(sum(target_allocations.values()), 1.0, rel_tol=1e-3):
        raise ValueError("target allocations must sum to 1.0")

    orders: Dict[str, float] = {}
    for ticker, target_weight in target_allocations.items():
        current_value = current_positions.get(ticker, 0.0)
        target_value = target_weight * portfolio_value
        orders[ticker] = target_value - current_value
    return orders


def summarize_trades(trades: Iterable[Trade]) -> Dict[str, float]:
    """Compute quick KPIs for displaying on dashboards."""

    trade_list = list(trades)
    if not trade_list:
        return {"count": 0, "win_rate": 0.0, "avg_return_pct": 0.0}

    wins = sum(1 for trade in trade_list if trade.pnl > 0)
    avg_return = sum(trade.return_pct for trade in trade_list) / len(trade_list)
    return {
        "count": len(trade_list),
        "win_rate": wins / len(trade_list) * 100,
        "avg_return_pct": avg_return,
    }

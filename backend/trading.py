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
    duration_bars: int
    exit_reason: str

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
    volatility_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    exposure_time_pct: float
    calmar_ratio: float
    value_at_risk_pct: float
    profit_factor: float
    expectancy_pct: float
    avg_trade_duration_bars: float
    ulcer_index: float
    downside_deviation_pct: float
    recovery_factor: float
    average_win_pct: float
    average_loss_pct: float
    win_loss_ratio: float
    tail_ratio: float
    monte_carlo_summary: Dict[str, float]
    omega_ratio: float
    kelly_fraction_pct: float
    max_consecutive_wins: int
    max_consecutive_losses: int


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


def _ulcer_index(equity_curve: Sequence[float]) -> float:
    peak = -math.inf
    squared_drawdowns = []
    for value in equity_curve:
        peak = max(peak, value)
        if peak <= 0:
            continue
        drawdown_pct = (value - peak) / peak * 100
        squared_drawdowns.append(drawdown_pct**2)
    if not squared_drawdowns:
        return 0.0
    return math.sqrt(sum(squared_drawdowns) / len(squared_drawdowns))


def _tail_ratio(returns: Sequence[float]) -> float:
    if not returns:
        return 0.0
    sorted_returns = sorted(returns)
    upper_index = max(int(len(sorted_returns) * 0.95) - 1, 0)
    lower_index = max(int(len(sorted_returns) * 0.05) - 1, 0)
    upper = sorted_returns[upper_index]
    lower = sorted_returns[lower_index]
    if lower == 0:
        return float("inf") if upper > 0 else 0.0
    return upper / abs(lower)


def _monte_carlo_bootstrap(
    trade_returns_pct: Sequence[float],
    *,
    iterations: int = 750,
    horizon: Optional[int] = None,
    seed: Optional[int] = None,
) -> Dict[str, float]:
    if not trade_returns_pct:
        return {
            "median_return_pct": 0.0,
            "p05_return_pct": 0.0,
            "p95_return_pct": 0.0,
            "average_return_pct": 0.0,
        }

    rng = random.Random(seed)
    horizon = horizon or max(len(trade_returns_pct), 30)
    outcomes: List[float] = []

    for _ in range(iterations):
        cumulative = 1.0
        for _ in range(horizon):
            sampled = rng.choice(trade_returns_pct)
            cumulative *= 1 + sampled / 100
        outcomes.append((cumulative - 1) * 100)

    outcomes.sort()
    avg_return = sum(outcomes) / len(outcomes)
    median_return = outcomes[len(outcomes) // 2]
    lower_idx = max(int(len(outcomes) * 0.05) - 1, 0)
    upper_idx = min(int(len(outcomes) * 0.95) - 1, len(outcomes) - 1)
    return {
        "median_return_pct": median_return,
        "p05_return_pct": outcomes[lower_idx],
        "p95_return_pct": outcomes[upper_idx],
        "average_return_pct": avg_return,
    }


def run_ema_strategy(
    candles: Sequence[Candle],
    *,
    fast_period: int = 12,
    slow_period: int = 26,
    initial_capital: float = 5_000_000,
    fee_rate: float = 0.0005,
    risk_per_trade_pct: float = 0.02,
    stop_loss_pct: float = 0.03,
    take_profit_pct: float | None = None,
    trailing_stop_pct: float | None = None,
) -> StrategyReport:
    """Execute a simple EMA crossover strategy on the provided candles."""

    if fast_period >= slow_period:
        raise ValueError("fast_period must be smaller than slow_period")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    if not 0 < risk_per_trade_pct <= 1:
        raise ValueError("risk_per_trade_pct must be in (0, 1]")
    if not 0 < stop_loss_pct < 1:
        raise ValueError("stop_loss_pct must be in (0, 1)")
    if take_profit_pct is not None and not 0 < take_profit_pct < 2:
        raise ValueError("take_profit_pct must be between 0 and 2")
    if trailing_stop_pct is not None and not 0 < trailing_stop_pct < 1:
        raise ValueError("trailing_stop_pct must be between 0 and 1")

    if not candles:
        raise ValueError("candles must not be empty")

    closes = [candle.close for candle in candles]
    fast = _ema(closes, fast_period)
    slow = _ema(closes, slow_period)

    trades: List[Trade] = []
    equity_curve: List[float] = []
    cash = initial_capital
    position_qty = 0.0
    entry_price = 0.0
    entry_index: Optional[int] = None
    exposure_bars = 0
    trade_durations: List[int] = []
    highest_price = 0.0

    for idx in range(len(candles)):
        price = closes[idx]
        equity_curve.append(cash + position_qty * price)

        stop_triggered = False

        def _record_trade(exit_price: float, exit_reason: str) -> None:
            nonlocal cash, position_qty, entry_price, entry_index, highest_price
            if position_qty <= 0:
                return
            gross = position_qty * exit_price
            fee = gross * fee_rate
            cash += gross - fee
            duration = idx - entry_index if entry_index is not None else 0
            duration_value = max(int(duration), 0)
            if entry_index is not None:
                entry_time = candles[entry_index].timestamp
            else:
                entry_time = candles[idx].timestamp
            trades.append(
                Trade(
                    entry_time=entry_time,
                    exit_time=candles[idx].timestamp,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=position_qty,
                    duration_bars=duration_value,
                    exit_reason=exit_reason,
                )
            )
            trade_durations.append(duration_value)
            position_qty = 0.0
            entry_price = 0.0
            entry_index = None
            highest_price = 0.0
            equity_curve[-1] = cash

        if position_qty > 0:
            exposure_bars += 1
            highest_price = max(highest_price, candles[idx].high, price)
            stop_price = entry_price * (1 - stop_loss_pct)
            trailing_price = (
                highest_price * (1 - trailing_stop_pct)
                if trailing_stop_pct is not None and highest_price > 0
                else None
            )
            take_profit_price = (
                entry_price * (1 + take_profit_pct)
                if take_profit_pct is not None
                else None
            )

            exit_price: Optional[float] = None
            exit_reason = ""

            if trailing_price is not None and candles[idx].low <= trailing_price:
                exit_price = trailing_price
                exit_reason = "trailing_stop"
            elif candles[idx].low <= stop_price:
                exit_price = stop_price
                exit_reason = "stop_loss"
            elif take_profit_price is not None and candles[idx].high >= take_profit_price:
                exit_price = take_profit_price
                exit_reason = "take_profit"

            if exit_price is not None:
                _record_trade(exit_price, exit_reason)
                stop_triggered = True

        if idx == 0 or idx >= len(fast) or idx >= len(slow):
            continue

        # Determine signals: bullish crossover -> buy, bearish -> sell
        prev_fast = fast[idx - 1]
        prev_slow = slow[idx - 1]
        current_fast = fast[idx]
        current_slow = slow[idx]

        crossed_up = prev_fast <= prev_slow and current_fast > current_slow
        crossed_down = prev_fast >= prev_slow and current_fast < current_slow

        if not stop_triggered and crossed_down and position_qty > 0:
            _record_trade(price, "ema_cross")
        elif crossed_up and cash > 0 and not stop_triggered:
            equity = cash + position_qty * price
            qty_by_cash = cash / (price * (1 + fee_rate)) if cash > 0 else 0.0
            qty_by_risk = (
                equity * risk_per_trade_pct / (price * stop_loss_pct)
                if stop_loss_pct > 0
                else qty_by_cash
            )
            position_qty = min(qty_by_cash, qty_by_risk)
            if position_qty <= 0:
                continue
            cost = position_qty * price
            fee = cost * fee_rate
            cash -= cost + fee
            entry_price = price
            entry_index = idx
            highest_price = price
            equity_curve[-1] = cash + position_qty * price

    # Liquidate any remaining position at the final price
    if position_qty > 0:
        final_price = closes[-1]
        gross = position_qty * final_price
        fee = gross * fee_rate
        cash = gross - fee
        duration = len(candles) - 1 - entry_index if entry_index is not None else 0
        duration_value = max(int(duration), 0)
        trades.append(
            Trade(
                entry_time=candles[entry_index].timestamp if entry_index is not None else candles[-1].timestamp,
                exit_time=candles[-1].timestamp,
                entry_price=entry_price,
                exit_price=final_price,
                quantity=position_qty,
                duration_bars=duration_value,
                exit_reason="end_of_data",
            )
        )
        trade_durations.append(duration_value)
        position_qty = 0.0

    equity_curve[-1] = cash
    total_return_pct = (cash - initial_capital) / initial_capital * 100

    days = max((candles[-1].timestamp - candles[0].timestamp).days, 1)
    annualized_return_pct = ((1 + total_return_pct / 100) ** (365 / days) - 1) * 100
    max_drawdown_pct = _max_drawdown(equity_curve)

    returns = [
        equity_curve[idx] / equity_curve[idx - 1] - 1
        for idx in range(1, len(equity_curve))
        if equity_curve[idx - 1] > 0
    ]
    if returns:
        avg_daily_return = sum(returns) / len(returns)
        if len(returns) >= 2:
            variance = sum((r - avg_daily_return) ** 2 for r in returns) / (len(returns) - 1)
            daily_volatility = math.sqrt(variance)
        else:
            daily_volatility = 0.0
    else:
        avg_daily_return = 0.0
        daily_volatility = 0.0

    volatility_pct = daily_volatility * math.sqrt(365) * 100
    sharpe_ratio = (
        (avg_daily_return / daily_volatility) * math.sqrt(365)
        if daily_volatility > 0
        else 0.0
    )

    downside_returns = [r for r in returns if r < 0]
    if downside_returns:
        downside_avg = sum(downside_returns) / len(downside_returns)
        downside_variance = sum((r - downside_avg) ** 2 for r in downside_returns) / len(
            downside_returns
        )
        downside_deviation = math.sqrt(downside_variance)
    else:
        downside_deviation = 0.0
    sortino_ratio = (
        (avg_daily_return / downside_deviation) * math.sqrt(365)
        if downside_deviation > 0
        else 0.0
    )

    sorted_returns = sorted(returns)
    if sorted_returns:
        var_index = max(int(len(sorted_returns) * 0.05) - 1, 0)
        value_at_risk_pct = sorted_returns[var_index] * 100
    else:
        value_at_risk_pct = 0.0

    exposure_time_pct = exposure_bars / len(candles) * 100 if candles else 0.0

    total_profits = sum(trade.pnl for trade in trades if trade.pnl > 0)
    total_losses = sum(-trade.pnl for trade in trades if trade.pnl < 0)
    profit_factor = (
        total_profits / total_losses if total_losses > 1e-9 else (total_profits / 1e-9 if total_profits > 0 else 0.0)
    )

    expectancy_pct = (
        sum(trade.return_pct for trade in trades) / len(trades) if trades else 0.0
    )

    avg_trade_duration_bars = (
        sum(trade_durations) / len(trade_durations) if trade_durations else 0.0
    )

    calmar_ratio = (
        (annualized_return_pct / abs(max_drawdown_pct)) if max_drawdown_pct > 0 else 0.0
    )

    downside_deviation_pct = downside_deviation * math.sqrt(365) * 100
    ulcer_index = _ulcer_index(equity_curve)
    recovery_factor = (
        total_return_pct / max_drawdown_pct if max_drawdown_pct > 0 else 0.0
    )

    winning_returns = [trade.return_pct for trade in trades if trade.return_pct > 0]
    losing_returns = [trade.return_pct for trade in trades if trade.return_pct < 0]
    average_win_pct = (
        sum(winning_returns) / len(winning_returns) if winning_returns else 0.0
    )
    average_loss_pct = (
        sum(losing_returns) / len(losing_returns) if losing_returns else 0.0
    )
    win_loss_ratio = (
        average_win_pct / abs(average_loss_pct)
        if average_loss_pct < 0
        else (float("inf") if average_win_pct > 0 else 0.0)
    )

    positive_excess = [max(r, 0.0) for r in returns]
    negative_excess = [max(-r, 0.0) for r in returns]
    neg_sum = sum(negative_excess)
    pos_sum = sum(positive_excess)
    omega_ratio = (
        pos_sum / neg_sum if neg_sum > 1e-12 else (float("inf") if pos_sum > 0 else 0.0)
    )

    winning_amounts = [trade.pnl for trade in trades if trade.pnl > 0]
    losing_amounts = [-trade.pnl for trade in trades if trade.pnl < 0]
    win_probability = len(winning_amounts) / len(trades) if trades else 0.0
    if (
        winning_amounts
        and losing_amounts
        and 0 < win_probability < 1
        and (avg_loss := sum(losing_amounts) / len(losing_amounts)) > 0
    ):
        avg_win = sum(winning_amounts) / len(winning_amounts)
        payoff = avg_win / avg_loss
        if payoff > 0:
            kelly_fraction = win_probability - (1 - win_probability) / payoff
        else:
            kelly_fraction = 0.0
    else:
        kelly_fraction = 0.0
    kelly_fraction_pct = max(min(kelly_fraction * 100, 100.0), -100.0)

    max_consecutive_wins = 0
    max_consecutive_losses = 0
    current_wins = 0
    current_losses = 0
    for trade in trades:
        if trade.pnl > 0:
            current_wins += 1
            current_losses = 0
        elif trade.pnl < 0:
            current_losses += 1
            current_wins = 0
        else:
            current_wins = 0
            current_losses = 0
        max_consecutive_wins = max(max_consecutive_wins, current_wins)
        max_consecutive_losses = max(max_consecutive_losses, current_losses)

    tail_ratio = _tail_ratio(returns)
    monte_carlo_summary = _monte_carlo_bootstrap(
        [trade.return_pct for trade in trades],
        horizon=len(trades) if trades else None,
    )

    return StrategyReport(
        trades=trades,
        equity_curve=equity_curve,
        total_return_pct=total_return_pct,
        annualized_return_pct=annualized_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        volatility_pct=volatility_pct,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        exposure_time_pct=exposure_time_pct,
        calmar_ratio=calmar_ratio,
        value_at_risk_pct=value_at_risk_pct,
        profit_factor=profit_factor,
        expectancy_pct=expectancy_pct,
        avg_trade_duration_bars=avg_trade_duration_bars,
        ulcer_index=ulcer_index,
        downside_deviation_pct=downside_deviation_pct,
        recovery_factor=recovery_factor,
        average_win_pct=average_win_pct,
        average_loss_pct=average_loss_pct,
        win_loss_ratio=win_loss_ratio,
        tail_ratio=tail_ratio,
        monte_carlo_summary=monte_carlo_summary,
        omega_ratio=omega_ratio,
        kelly_fraction_pct=kelly_fraction_pct,
        max_consecutive_wins=max_consecutive_wins,
        max_consecutive_losses=max_consecutive_losses,
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
    if not target_allocations:
        raise ValueError("target allocations must not be empty")
    if any(weight < 0 or weight > 1 for weight in target_allocations.values()):
        raise ValueError("each target allocation must be between 0 and 1")
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
        return {
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

    wins = sum(1 for trade in trade_list if trade.pnl > 0)
    avg_return = sum(trade.return_pct for trade in trade_list) / len(trade_list)
    total_profits = sum(trade.pnl for trade in trade_list if trade.pnl > 0)
    total_losses = sum(-trade.pnl for trade in trade_list if trade.pnl < 0)
    profit_factor = (
        total_profits / total_losses if total_losses > 1e-9 else (total_profits / 1e-9 if total_profits > 0 else 0.0)
    )
    avg_duration = (
        sum(trade.duration_bars for trade in trade_list) / len(trade_list)
    )
    winning_returns = [trade.return_pct for trade in trade_list if trade.return_pct > 0]
    losing_returns = [trade.return_pct for trade in trade_list if trade.return_pct < 0]
    average_win_pct = (
        sum(winning_returns) / len(winning_returns) if winning_returns else 0.0
    )
    average_loss_pct = (
        sum(losing_returns) / len(losing_returns) if losing_returns else 0.0
    )
    win_loss_ratio = (
        average_win_pct / abs(average_loss_pct)
        if average_loss_pct < 0
        else (float("inf") if average_win_pct > 0 else 0.0)
    )
    returns_sorted = sorted(trade.return_pct for trade in trade_list)
    median_return = returns_sorted[len(returns_sorted) // 2]

    return {
        "count": len(trade_list),
        "win_rate": wins / len(trade_list) * 100,
        "avg_return_pct": avg_return,
        "profit_factor": profit_factor,
        "expectancy_pct": avg_return,
        "avg_duration_bars": avg_duration,
        "average_win_pct": average_win_pct,
        "average_loss_pct": average_loss_pct,
        "win_loss_ratio": win_loss_ratio,
        "largest_win_pct": max(returns_sorted) if returns_sorted else 0.0,
        "largest_loss_pct": min(returns_sorted) if returns_sorted else 0.0,
        "median_return_pct": median_return,
        "payoff_ratio": win_loss_ratio,
    }


def design_risk_budgeted_portfolio(
    *,
    capital: float,
    risk_profile: float,
    stable_assets: Sequence[Dict[str, float]],
    aggressive_assets: Sequence[Dict[str, float]],
) -> Dict[str, object]:
    """Design a two-bucket portfolio split between stable and aggressive assets.

    The function distributes the provided ``capital`` between stable and
    aggressive asset buckets based on ``risk_profile`` (0 → fully stable,
    1 → fully aggressive). Within each bucket the funds are allocated according
    to the relative ``weight`` of each asset.

    ``expected_return_pct`` and ``expected_volatility_pct`` are used to
    calculate holistic portfolio projections that surface the expected reward
    and variability of the proposed mix.
    """

    if capital <= 0:
        raise ValueError("capital must be positive")
    if not 0 <= risk_profile <= 1:
        raise ValueError("risk_profile must be between 0 and 1")
    if not stable_assets and not aggressive_assets:
        raise ValueError("at least one asset must be provided")

    def _normalise_bucket(
        bucket: Sequence[Dict[str, float]],
    ) -> List[Dict[str, float]]:
        if not bucket:
            return []
        total_weight = sum(asset["weight"] for asset in bucket)
        if total_weight <= 0:
            raise ValueError("asset weights must sum to a positive value")
        normalised = [
            {
                "symbol": asset["symbol"],
                "weight": asset["weight"] / total_weight,
                "expected_return_pct": asset["expected_return_pct"],
                "expected_volatility_pct": asset["expected_volatility_pct"],
            }
            for asset in bucket
        ]
        return normalised

    stable_norm = _normalise_bucket(stable_assets)
    aggressive_norm = _normalise_bucket(aggressive_assets)

    if stable_norm and aggressive_norm:
        stable_share = 1 - risk_profile
        aggressive_share = risk_profile
    elif stable_norm:
        stable_share = 1.0
        aggressive_share = 0.0
    else:
        stable_share = 0.0
        aggressive_share = 1.0

    allocations: List[Dict[str, float]] = []
    expected_return_decimal = 0.0
    expected_volatility_components = 0.0
    weighted_vol_sum = 0.0

    for bucket_name, bucket_share, bucket_assets in (
        ("stable", stable_share, stable_norm),
        ("aggressive", aggressive_share, aggressive_norm),
    ):
        for asset in bucket_assets:
            final_weight = bucket_share * asset["weight"]
            if final_weight <= 0:
                continue
            amount = capital * final_weight
            weight_pct = final_weight * 100
            expected_return_decimal += (final_weight) * (
                asset["expected_return_pct"] / 100
            )
            expected_volatility_components += (
                final_weight * asset["expected_volatility_pct"] / 100
            ) ** 2
            weighted_vol_sum += final_weight * (
                asset["expected_volatility_pct"] / 100
            )
            allocations.append(
                {
                    "symbol": asset["symbol"],
                    "bucket": bucket_name,
                    "weight_pct": weight_pct,
                    "amount": amount,
                    "expected_return_pct": asset["expected_return_pct"],
                    "expected_volatility_pct": asset["expected_volatility_pct"],
                }
            )

    total_weight_pct = sum(item["weight_pct"] for item in allocations)
    if allocations and abs(total_weight_pct - 100) > 0.5:
        # Guard against floating point drift when buckets are sparse
        scale = 100 / total_weight_pct
        for item in allocations:
            item["weight_pct"] *= scale
            item["amount"] = capital * (item["weight_pct"] / 100)

    expected_volatility_decimal = math.sqrt(expected_volatility_components)
    diversification_ratio = (
        weighted_vol_sum / expected_volatility_decimal
        if expected_volatility_decimal > 0
        else 0.0
    )

    bucket_amounts = {
        "stable": capital * stable_share,
        "aggressive": capital * aggressive_share,
    }

    summary = {
        "capital": capital,
        "expected_return_pct": expected_return_decimal * 100,
        "expected_return_currency": capital * expected_return_decimal,
        "expected_volatility_pct": expected_volatility_decimal * 100,
        "diversification_ratio": diversification_ratio,
        "bucket_weights_pct": {
            "stable": stable_share * 100,
            "aggressive": aggressive_share * 100,
        },
        "bucket_amounts": bucket_amounts,
    }

    return {"allocations": allocations, "summary": summary}

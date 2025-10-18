"""Pydantic schemas shared by the FastAPI service."""
from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, root_validator, validator


class CandlePayload(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class SimulationRequest(BaseModel):
    fast_period: int = Field(12, ge=2, le=60)
    slow_period: int = Field(26, ge=3, le=200)
    initial_capital: float = Field(5_000_000, gt=0)
    fee_rate: float = Field(0.0005, ge=0, le=0.01)
    risk_per_trade_pct: float = Field(0.02, gt=0, le=1)
    stop_loss_pct: float = Field(0.03, gt=0, lt=1)
    take_profit_pct: Optional[float] = Field(
        None, gt=0, lt=2, description="Optional take-profit target in decimal form"
    )
    trailing_stop_pct: Optional[float] = Field(
        None, gt=0, lt=1, description="Trailing stop distance in decimal form"
    )
    seed: Optional[int] = Field(None, description="Seed for reproducible synthetic data")
    prices: Optional[List[CandlePayload]] = None

    @validator("slow_period")
    def _validate_periods(cls, slow_period: int, values):
        fast_period = values.get("fast_period")
        if fast_period and slow_period <= fast_period:
            raise ValueError("slow_period must be greater than fast_period")
        return slow_period


class TradePayload(BaseModel):
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    return_pct: float
    duration_bars: int
    exit_reason: str


class MonteCarloSummary(BaseModel):
    median_return_pct: float
    p05_return_pct: float
    p95_return_pct: float
    average_return_pct: float


class SimulationResponse(BaseModel):
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
    omega_ratio: float
    kelly_fraction_pct: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    skewness: float
    kurtosis: float
    average_drawdown_pct: float
    pain_index: float
    max_runup_pct: float
    trades: List[TradePayload]
    equity_curve: List[float]
    trade_summary: dict
    monte_carlo_summary: MonteCarloSummary


class RebalanceRequest(BaseModel):
    current_positions: dict
    target_allocations: dict
    portfolio_value: float

    @validator("portfolio_value")
    def _portfolio_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("portfolio_value must be positive")
        return value

    @validator("target_allocations")
    def _validate_target_allocations(cls, value: Dict[str, float]) -> Dict[str, float]:
        if not value:
            raise ValueError("target_allocations must not be empty")
        if any(weight < 0 or weight > 1 for weight in value.values()):
            raise ValueError("each target allocation must be between 0 and 1")
        total = sum(value.values())
        if not math.isclose(total, 1.0, rel_tol=1e-3):
            raise ValueError("target allocations must sum to 1.0")
        return value


class RebalanceResponse(BaseModel):
    orders: dict


class PortfolioAsset(BaseModel):
    symbol: str = Field(..., min_length=1)
    weight: float = Field(..., gt=0)
    expected_return_pct: float = Field(..., ge=-100, le=300)
    expected_volatility_pct: float = Field(..., gt=0, le=500)


class PortfolioBlueprintRequest(BaseModel):
    capital: float = Field(..., gt=0)
    risk_profile: float = Field(0.5, ge=0, le=1)
    stable_assets: List[PortfolioAsset] = Field(default_factory=list)
    aggressive_assets: List[PortfolioAsset] = Field(default_factory=list)

    @root_validator
    def _ensure_assets_exist(cls, values: dict) -> dict:
        if not values.get("stable_assets") and not values.get("aggressive_assets"):
            raise ValueError("at least one asset must be supplied")
        return values


class PortfolioAllocation(BaseModel):
    symbol: str
    bucket: Literal["stable", "aggressive"]
    weight_pct: float
    amount: float
    expected_return_pct: float
    expected_volatility_pct: float


class PortfolioSummary(BaseModel):
    capital: float
    expected_return_pct: float
    expected_return_currency: float
    expected_volatility_pct: float
    diversification_ratio: float
    bucket_weights_pct: Dict[str, float]
    bucket_amounts: Dict[str, float]


class PortfolioBlueprintResponse(BaseModel):
    allocations: List[PortfolioAllocation]
    summary: PortfolioSummary


class OrderMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class OrderRequest(BaseModel):
    mode: OrderMode = OrderMode.PAPER
    market: str = Field(..., min_length=3)
    side: Literal["bid", "ask"]
    ord_type: Literal["limit", "market", "price"] = "limit"
    price: Optional[float] = Field(None, ge=0)
    volume: Optional[float] = Field(None, gt=0)

    @validator("market")
    def _upper_market(cls, value: str) -> str:
        return value.upper()

    @root_validator
    def _validate_price_volume(cls, values: dict) -> dict:
        side = values.get("side")
        price = values.get("price")
        volume = values.get("volume")
        if volume is None or volume <= 0:
            raise ValueError("volume must be positive")
        if side == "bid" and (price is None or price <= 0):
            raise ValueError("price must be positive for buy orders")
        return values


class PaperPositionPayload(BaseModel):
    market: str
    volume: float
    average_price: float
    market_price: float
    market_value: float
    unrealized_pnl: float


class PaperOrderPayload(BaseModel):
    order_id: str
    market: str
    side: Literal["bid", "ask"]
    price: float
    volume: float
    fee: float
    realized_pnl: float
    executed_at: datetime


class PaperBalancePayload(BaseModel):
    cash: float
    portfolio_value: float
    last_updated: datetime
    positions: List[PaperPositionPayload]
    orders: List[PaperOrderPayload]


class PaperStatusResponse(PaperBalancePayload):
    pass


class PaperResetRequest(BaseModel):
    initial_cash: float = Field(..., gt=0)


class PaperMarkRequest(BaseModel):
    market: str
    price: float = Field(..., gt=0)


class OrderResponse(BaseModel):
    mode: OrderMode
    order_id: str
    market: str
    side: Literal["bid", "ask"]
    price: Optional[float]
    volume: Optional[float]
    fee: Optional[float]
    status: str
    realized_pnl: Optional[float] = None
    balance: Optional[PaperBalancePayload] = None
    raw_response: Optional[dict] = None


class LiveBalancesResponse(BaseModel):
    balances: List[dict]


class MarketCandlesResponse(BaseModel):
    market: str
    interval: str
    source: Literal["upbit", "synthetic"]
    candles: List[CandlePayload]


class MarketInsightsResponse(BaseModel):
    market: str
    interval: str
    source: Literal["upbit", "synthetic"]
    latest_close: float
    latest_timestamp: datetime
    ema_fast: float
    ema_slow: float
    ema_signal: str
    rsi: float
    macd: float
    macd_signal: float
    macd_histogram: float
    volatility_pct: float
    trend_strength: float
    regime: str
    recommended_action: str
    confidence_pct: float
    insight_summary: str


class NewsItem(BaseModel):
    title: str
    url: str
    source: str
    published_at: str


class NewsResponse(BaseModel):
    generated_at: datetime
    items: List[NewsItem]


class MarketAIMetricsPayload(BaseModel):
    fast_ema: float
    slow_ema: float
    rsi: float
    macd: float
    macd_signal: float
    macd_histogram: float
    volatility_pct: float
    trend_strength: float
    regime_score: float
    probability_of_trend: float
    price_change_pct: float
    support_level: float
    resistance_level: float
    atr: float
    hurst_exponent: float
    bollinger_bandwidth_pct: float
    institutional_sentiment: float
    liquidity_score: float
    breakout_probability: float
    volatility_regime: str


class RiskControlAdvicePayload(BaseModel):
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: Optional[float]
    position_size_pct: float
    confidence_note: str
    notes: List[str]


class TimeframeConsensusPayload(BaseModel):
    dominant_trend: str
    agreement_pct: float
    intervals: List[str]
    details: List[str]


class MarketAIResponse(BaseModel):
    market: str
    interval: str
    regime: str
    recommended_action: str
    confidence_pct: float
    summary: str
    signals: List[str]
    metrics: MarketAIMetricsPayload
    risk: RiskControlAdvicePayload
    generated_at: datetime
    news: List[NewsItem]
    timeframe_consensus: "TimeframeConsensusPayload"
    institutional_confidence_pct: float
    institutional_commentary: str


class PortfolioAssetInput(BaseModel):
    symbol: str = Field(..., min_length=1)
    name: Optional[str] = None
    asset_type: Literal["etf", "crypto", "cash", "other"] = "crypto"
    expected_return_pct: float = Field(..., ge=-100, le=500)
    expected_volatility_pct: float = Field(..., gt=0, le=1000)
    risk_score: float = Field(..., ge=0, le=1)
    narrative: Optional[str] = Field(
        None, description="Allocation rationale such as factor exposure or thesis"
    )
    market: Optional[str] = Field(
        None, description="Upbit market ticker (e.g. KRW-BTC) for live analysis"
    )


class PortfolioAIAllocation(BaseModel):
    symbol: str
    name: str
    asset_type: str
    weight: float
    allocation_krw: float
    expected_return_pct: float
    expected_volatility_pct: float
    rationale: str


class MarketBriefingPayload(BaseModel):
    market: str
    regime: str
    action: str
    confidence_pct: str
    summary: str


class PortfolioOptimizationRequest(BaseModel):
    risk_appetite: float = Field(0.5, ge=0, le=1)
    capital: float = Field(10_000_000, gt=0)
    include_cash: bool = True
    preferred_markets: Optional[List[str]] = None
    custom_assets: Optional[List[PortfolioAssetInput]] = Field(
        None, description="Custom asset universe overriding the default AI library"
    )


class PortfolioOptimizationResponse(BaseModel):
    generated_at: datetime
    risk_profile_label: str
    risk_appetite: float
    expected_return_pct: float
    expected_volatility_pct: float
    sharpe_estimate: float
    diversification_score_pct: float
    tail_risk_guard_pct: float
    allocations: List[PortfolioAIAllocation]
    hedging_notes: List[str]
    methodology: str
    market_briefings: List[MarketBriefingPayload]


class AutoPilotPlanPayload(BaseModel):
    market: str
    side: Literal["bid", "ask", "flat"]
    bias: Literal["long", "short", "neutral"]
    order_type: Literal["market", "limit", "monitor"]
    suggested_price: Optional[float]
    position_size_pct: float
    stop_loss_pct: float
    take_profit_pct: Optional[float]
    trailing_stop_pct: Optional[float]
    confidence_pct: float
    reasoning: List[str]
    monitoring: List[str]


class CopilotRequest(BaseModel):
    question: str = Field(..., min_length=2)
    market: str = Field("KRW-BTC", min_length=3)
    interval: str = Field("minute60")
    mode: OrderMode = OrderMode.PAPER
    risk_appetite: float = Field(0.55, ge=0, le=1)
    capital: float = Field(20_000_000, gt=0)
    include_portfolio: bool = True


class CopilotResponse(BaseModel):
    generated_at: datetime
    answer: str
    summary_points: List[str]
    risk_notices: List[str]
    action_items: List[str]
    highlights: List[str]
    autopilot: AutoPilotPlanPayload
    insight: MarketAIResponse
    news: List[NewsItem]


class AutoPilotConfigRequest(BaseModel):
    mode: OrderMode = OrderMode.PAPER
    market: str = Field("KRW-BTC", min_length=3)
    interval: str = Field("minute60")
    risk_appetite: float = Field(0.55, ge=0, le=1)
    capital: float = Field(20_000_000, gt=0)
    poll_interval: float = Field(120.0, ge=15.0, le=900.0)
    include_portfolio: bool = True
    max_position_pct: float = Field(0.25, ge=0.01, le=1.0)
    min_confidence_pct: float = Field(55.0, ge=0.0, le=100.0)


class AutoPilotLogEntryPayload(BaseModel):
    timestamp: datetime
    level: str
    message: str


class AutoPilotExecutionPayload(BaseModel):
    mode: OrderMode
    market: str
    side: Literal["bid", "ask"]
    price: float
    volume: float
    value: float
    executed_at: datetime
    detail: str


class AutoPilotConfigPayload(BaseModel):
    mode: OrderMode
    market: str
    interval: str
    risk_appetite: float
    capital: float
    poll_interval: float
    include_portfolio: bool
    max_position_pct: float
    min_confidence_pct: float


class AutoPilotStatusResponse(BaseModel):
    running: bool
    config: Optional[AutoPilotConfigPayload]
    last_plan: Optional[AutoPilotPlanPayload]
    last_execution: Optional[AutoPilotExecutionPayload]
    last_error: Optional[str]
    last_cycle_started_at: Optional[datetime]
    last_cycle_completed_at: Optional[datetime]
    logs: List[AutoPilotLogEntryPayload]


class DiagnosticCheckPayload(BaseModel):
    name: str
    status: Literal["ok", "warning", "error"]
    detail: str
    latency_ms: float


class DiagnosticsResponse(BaseModel):
    generated_at: datetime
    checks: List[DiagnosticCheckPayload]


class ChatNotificationRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)

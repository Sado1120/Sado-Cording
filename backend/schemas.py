"""Pydantic schemas shared by the FastAPI service."""
from __future__ import annotations

import math
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

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
    market: Optional[str] = Field(
        None,
        regex=r"^[A-Z]{3,5}-[A-Z0-9]{2,10}$",
        description="Optional Upbit market code such as KRW-BTC",
    )
    interval: Optional[str] = Field(
        None,
        description="Optional Upbit candle interval (e.g. minute60, day)",
    )
    use_live_data: bool = Field(
        False,
        description="When true the simulation pulls recent Upbit candles for the selected market.",
    )

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
    market: Optional[str] = None


class MonteCarloSummary(BaseModel):
    median_return_pct: float
    p05_return_pct: float
    p95_return_pct: float
    average_return_pct: float


class SimulationResponse(BaseModel):
    market: Optional[str] = None
    initial_capital: float
    ending_equity: float
    profit_krw: float
    price_source: Literal["upbit", "upbit_stale", "synthetic", "manual"] = "manual"
    price_message: Optional[str] = None
    price_detail: Optional[str] = None
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
    integrity_score: float = Field(ge=0, le=100, default=100.0)
    integrity_flags: List[str] = Field(default_factory=list)


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
        ord_type = values.get("ord_type")
        if volume is None or volume <= 0:
            raise ValueError("volume must be positive")

        if ord_type == "market":
            if price is not None and price < 0:
                raise ValueError("price must be non-negative")
            if price in {0, 0.0}:
                values["price"] = None
            return values

        if ord_type == "limit" and (price is None or price <= 0):
            raise ValueError("limit orders require a positive price")

        if ord_type == "price" and (price is None or price <= 0):
            raise ValueError("price orders require a positive amount")

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
    initial_cash: float
    positions: List[PaperPositionPayload]
    orders: List[PaperOrderPayload]


class PaperStatusResponse(PaperBalancePayload):
    market: str = "KRW-BTC"
    interval: str = "minute1"
    price_source: Literal["upbit", "upbit_stale", "synthetic", "manual"] = "manual"
    heartbeat_state: Literal["online", "warning", "offline"] = "offline"
    heartbeat_reason: Literal["live", "delayed", "manual", "synthetic", "stale"] = "manual"


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
    source: Literal["upbit", "upbit_stale", "synthetic"]
    candles: List[CandlePayload]
    status: str = "unknown"
    message: str = ""
    detail: Optional[str] = None
    checked_at: Optional[datetime] = None
    stale: bool = False
    fetched_at: Optional[datetime] = None


class MarketInfoPayload(BaseModel):
    market: str
    korean_name: str
    english_name: str
    base_currency: str
    quote_currency: str
    market_warning: str
    trading_suspended: bool


class MarketGroupPayload(BaseModel):
    key: str
    label: str
    description: str
    markets: List[str]


class MarketListResponse(BaseModel):
    generated_at: datetime
    source: Literal["upbit", "fallback"]
    markets: List[MarketInfoPayload]
    groups: List[MarketGroupPayload] = Field(default_factory=list)
    status: str = "unknown"
    message: str = ""
    detail: Optional[str] = None
    checked_at: Optional[datetime] = None
    backoff_seconds_remaining: float = 0.0


class MarketRecommendationPayload(BaseModel):
    market: str
    korean_name: str
    english_name: str
    base_currency: str
    quote_currency: str
    score: float
    confidence_pct: float
    regime: str
    recommended_action: str
    last_price: float
    price_change_pct: float
    trend_strength_pct: float
    volatility_pct: float
    institutional_sentiment_pct: float
    breakout_probability_pct: float
    summary: str
    reason: str
    source: Literal["upbit", "upbit_stale", "synthetic"]


class MarketRecommendationsResponse(BaseModel):
    generated_at: datetime
    interval: str
    base_currency: str
    limit: int
    analysed_markets: int
    analysis_duration_ms: float
    analysis_source: Literal["upbit", "synthetic", "mixed"]
    recommendations: List[MarketRecommendationPayload]
    errors: List[str] = Field(default_factory=list)


class MarketInsightsResponse(BaseModel):
    market: str
    interval: str
    source: Literal["upbit", "upbit_stale", "synthetic"]
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
    stale: bool = False
    fetched_at: Optional[datetime] = None


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


class TechnicalConfluencePayload(BaseModel):
    score: float
    label: str
    drivers: List[str]
    volume_confirmation: float
    pattern: Optional[str]
    squeeze_signal: Optional[str]


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
    technical_confluence: TechnicalConfluencePayload
    generated_at: datetime
    news: List[NewsItem]
    timeframe_consensus: "TimeframeConsensusPayload"
    institutional_confidence_pct: float
    institutional_commentary: str


class LossRecoveryStepPayload(BaseModel):
    title: str
    objective: str
    threshold_pct: float
    actions: List[str]
    guardrails: List[str]
    metrics: Dict[str, float]


class LossRecoveryPlaybookResponse(BaseModel):
    generated_at: datetime
    realized_loss_krw: float
    unrealized_loss_krw: float
    loss_markets: List[str]
    recovery_horizon: str
    steps: List[LossRecoveryStepPayload]
    risk_commandments: List[str]
    chart_playbook: List[str]
    institutional_briefs: List[str]
    proprietary_edge: str


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


class AssistantRequest(BaseModel):
    question: str = Field(..., min_length=2)
    market: str = Field("KRW-BTC", min_length=3)
    interval: str = Field("minute60")
    risk_appetite: float = Field(0.55, ge=0, le=1)
    capital: float = Field(20_000_000, gt=0)
    include_autopilot: bool = True
    include_chat_push: bool = False


class AssistantResponse(BaseModel):
    generated_at: datetime
    question: str
    answer: str
    insights: List[str]
    next_steps: List[str]
    risk_notices: List[str]
    autopilot: Optional[AutoPilotPlanPayload]
    insight: MarketAIResponse
    pushed_to_chat: bool = False


class ChatAssistantRequest(BaseModel):
    question: str = Field(..., min_length=3)
    market: str = Field("KRW-BTC", min_length=3)
    interval: str = Field("minute60")
    risk_appetite: float = Field(0.55, ge=0, le=1)
    capital: float = Field(20_000_000, gt=0)


class ChatAssistantResponse(BaseModel):
    question: str
    answer: str
    next_steps: List[str]
    risk_notices: List[str]
    sent: bool
    message: str


class AssistantHistoryEntryPayload(BaseModel):
    generated_at: datetime
    question: str
    answer: str
    insights: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    risk_notices: List[str] = Field(default_factory=list)
    pushed_to_chat: bool = False


class AssistantHistoryListResponse(BaseModel):
    entries: List[AssistantHistoryEntryPayload]
    count: int


DEFAULT_AUTOPILOT_MARKET = "KRW-BTC"


class AutoPilotConfigRequest(BaseModel):
    mode: OrderMode = OrderMode.PAPER
    market: str = Field(DEFAULT_AUTOPILOT_MARKET, min_length=0)
    interval: str = Field("minute60")
    risk_appetite: float = Field(0.55, ge=0, le=1)
    capital: float = Field(20_000_000, gt=0)
    poll_interval: float = Field(120.0, ge=15.0, le=900.0)
    include_portfolio: bool = True
    max_position_pct: float = Field(0.25, ge=0.01, le=1.0)
    min_confidence_pct: float = Field(55.0, ge=0.0, le=100.0)
    auto_select_market: bool = True
    recommendation_base: str = Field("ALL", min_length=2)
    recommendation_interval: str = Field("minute60")
    recommendation_max_markets: int = Field(180, ge=5, le=200)
    recommendation_include_warnings: bool = False
    max_trades_per_cycle: int = Field(1, ge=1, le=5)

    @root_validator(pre=True)
    def _normalise_market(cls, values: dict) -> dict:
        raw_market = values.get("market", DEFAULT_AUTOPILOT_MARKET)
        auto_select = bool(values.get("auto_select_market", True))

        market = str(raw_market or "").strip().upper()
        if not market:
            if auto_select:
                market = DEFAULT_AUTOPILOT_MARKET
            else:
                raise ValueError("auto_select_market가 꺼진 경우 마켓을 반드시 입력해야 합니다.")

        values["market"] = market

        base = values.get("recommendation_base")
        if base is not None:
            values["recommendation_base"] = str(base).strip().upper() or "ALL"

        interval = values.get("interval")
        if interval is not None:
            values["interval"] = str(interval).strip() or "minute60"

        rec_interval = values.get("recommendation_interval")
        if rec_interval is not None:
            values["recommendation_interval"] = str(rec_interval).strip() or "minute60"

        return values


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
    auto_select_market: bool
    recommendation_base: str
    recommendation_interval: str
    recommendation_max_markets: int
    recommendation_include_warnings: bool
    max_trades_per_cycle: int


class AutoPilotStatusResponse(BaseModel):
    running: bool
    config: Optional[AutoPilotConfigPayload]
    last_plan: Optional[AutoPilotPlanPayload]
    last_execution: Optional[AutoPilotExecutionPayload]
    last_error: Optional[str]
    last_cycle_started_at: Optional[datetime]
    last_cycle_completed_at: Optional[datetime]
    next_cycle_due_at: Optional[datetime]
    logs: List[AutoPilotLogEntryPayload]
    recent_recommendations: List[str] = Field(default_factory=list)
    recommendation_source: Optional[str] = None
    recent_executions: List[AutoPilotExecutionPayload] = Field(default_factory=list)
    last_skip_reason: Optional[str] = None
    candidate_markets: List[str] = Field(default_factory=list)
    candidate_market_count: int = 0
    analysis_markets: List[str] = Field(default_factory=list)
    analysis_market_count: int = 0
    candidate_rotation_cursor: int = 0
    repeat_market_count: int = 0
    network_status: Optional[str] = None
    network_message: Optional[str] = None
    network_detail: Optional[str] = None
    network_checked_at: Optional[datetime] = None
    network_backoff_seconds: float = 0.0


class TradeHistoryItemPayload(BaseModel):
    executed_at: datetime
    market: str
    side: Literal["bid", "ask"]
    mode: OrderMode
    price: float
    volume: float
    value: float
    fee: float
    realized_pnl: float
    source: Literal["paper", "autopilot"]
    note: Optional[str] = None


class TradeHistoryResponse(BaseModel):
    generated_at: datetime
    items: List[TradeHistoryItemPayload]


class DiagnosticCheckPayload(BaseModel):
    name: str
    status: Literal["ok", "warning", "error"]
    detail: str
    latency_ms: float


class ConnectivitySuggestion(BaseModel):
    title: str
    detail: str


class DiagnosticsResponse(BaseModel):
    generated_at: datetime
    checks: List[DiagnosticCheckPayload]
    upbit_guidance: List[ConnectivitySuggestion] = Field(default_factory=list)


class AISelfCheckIssue(BaseModel):
    component: str
    severity: Literal["info", "warning", "critical"]
    summary: str
    detail: Optional[str] = None
    suggested_action: Optional[str] = None


class AISelfCheckResponse(BaseModel):
    generated_at: datetime
    overall_severity: Literal["nominal", "warning", "critical"]
    summary: str
    issues: List[AISelfCheckIssue]


class UpbitRecoveryStepPayload(BaseModel):
    timestamp: datetime
    reason: str
    action: str
    status: Literal["success", "failed", "skipped", "info"]
    detail: Optional[str] = None


class UpbitRecoveryResponse(BaseModel):
    performed_at: datetime
    reason: str
    status: Literal["success", "failed", "skipped", "cooldown"]
    steps: List[UpbitRecoveryStepPayload]


class UpbitRecoveryLogResponse(BaseModel):
    entries: List[UpbitRecoveryStepPayload]


class ChatNotificationRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class ChatNotificationStatus(BaseModel):
    configured: bool
    last_attempt_at: Optional[datetime]
    last_success_at: Optional[datetime]
    last_error: Optional[str]
    last_message: Optional[str]
    webhook_host: Optional[str]


class ChatDigestFlushRequest(BaseModel):
    category: Optional[str] = Field(None, description="요약을 전송할 카테고리 (기본: 전체)")
    force: bool = Field(
        False,
        description="True일 경우 당일 데이터까지 즉시 보고서를 전송합니다.",
    )


class ChatDigestReport(BaseModel):
    category: str
    date: date
    sent: bool
    message_preview: str


class ChatDigestFlushResponse(BaseModel):
    reports: List[ChatDigestReport]


class LogEntryPayload(BaseModel):
    timestamp: Optional[datetime]
    level: str
    event: str
    data: Dict[str, Any] = Field(default_factory=dict)
    raw: str


class LogTailResponse(BaseModel):
    generated_at: datetime
    entries: List[LogEntryPayload]
    log_path: Optional[str] = None

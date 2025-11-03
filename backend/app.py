"""FastAPI application exposing Sado Trade Bot capabilities."""
from __future__ import annotations

import json
import math
import os
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from . import ai, notifications, trading

# Ensure environment variables from .env are loaded before other modules
# (such as market data fetchers) evaluate their configuration.
notifications.ensure_env_from_file()
from .adaptive import AdaptiveProfile
from .email_utils import send_verification_email
from .autopilot import AutoTrader, AutoTraderConfig, AutoTraderExecution, AutoTraderState
from .history import AssistantHistoryEntry, AssistantHistoryStore
from .schemas import (
    CandlePayload,
    AISelfCheckResponse,
    AISelfCheckIssue,
    MarketAIResponse,
    LiveBalancesResponse,
    MarketCandlesResponse,
    MarketInsightsResponse,
    MarketInfoPayload,
    MarketListResponse,
    NewsItem,
    NewsResponse,
    CopilotRequest,
    CopilotResponse,
    AssistantRequest,
    AssistantResponse,
    ChatAssistantRequest,
    ChatAssistantResponse,
    AutoPilotPlanPayload,
    OrderMode,
    OrderRequest,
    OrderResponse,
    PortfolioAssetInput,
    PortfolioBlueprintRequest,
    PortfolioBlueprintResponse,
    PortfolioOptimizationRequest,
    PortfolioOptimizationResponse,
    MarketAIMetricsPayload,
    TechnicalConfluencePayload,
    RiskControlAdvicePayload,
    TimeframeConsensusPayload,
    PaperBalancePayload,
    PaperMarkRequest,
    PaperOrderPayload,
    PaperPositionPayload,
    PaperResetRequest,
    PaperStatusResponse,
    RebalanceRequest,
    RebalanceResponse,
    SimulationRequest,
    SimulationResponse,
    TradePayload,
    AutoPilotConfigRequest,
    AutoPilotConfigPayload,
    AutoPilotExecutionPayload,
    AutoPilotLogEntryPayload,
    AutoPilotStatusResponse,
    AdaptiveProfilePayload,
    MetaVersionResponse,
    TradeHistoryResponse,
    TradeHistoryItemPayload,
    ConnectivitySuggestion,
    DiagnosticsResponse,
    DiagnosticCheckPayload,
    ChatNotificationRequest,
    ChatNotificationStatus,
    ChatDigestFlushRequest,
    ChatDigestFlushResponse,
    ChatDigestReport,
    LoginRequest,
    LoginResponse,
    AuthStatusResponse,
    AuthProfileResponse,
    UpdatePasswordRequest,
    MessageResponse,
    VerifyEmailResponse,
    TotpResetRequest,
    TotpResetResponse,
    RegistrationRequest,
    RegistrationResponse,
    VerifyEmailRequest,
    AssistantHistoryEntryPayload,
    AssistantHistoryListResponse,
    MarketGroupPayload,
    MarketRecommendationsResponse,
    MarketRecommendationPayload,
    LossRecoveryPlaybookResponse,
    LossRecoveryStepPayload,
    UpbitRecoveryResponse,
    UpbitRecoveryLogResponse,
    LogEntryPayload,
    LogTailResponse,
)
from .execution import (
    ExecutionError,
    PaperOrder,
    PaperPosition,
    PaperBroker,
    create_upbit_client_from_env,
    paper_broker,
)
from .market import (
    MarketDataError,
    MarketInfo,
    TopMarketSelection,
    attempt_upbit_self_heal,
    build_market_insights,
    fetch_authoritative_news,
    fetch_upbit_candles,
    fetch_upbit_markets,
    fetch_upbit_top_markets,
    get_fallback_market_infos,
    get_upbit_recovery_log,
    get_upbit_network_state,
)
from .log_utils import (
    get_log_file_path,
    log_event,
    log_exception,
    read_log_tail,
)
from .version import APP_VERSION
from .security import (
    auth_manager,
    ensure_authenticated,
    verify_login,
    begin_registration,
    resend_verification,
    verify_email_code,
)


app = FastAPI(
    title="Sado Trade Bot API",
    description="Professional Upbit and ETF trading assistant with live analytics",
    version=APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


_PUBLIC_PATH_PREFIXES = (
    "/auth/login",
    "/auth/status",
    "/auth/register",
    "/auth/register/verify",
    "/auth/register/resend",
    "/docs",
    "/openapi",
    "/redoc",
)
_PUBLIC_PATHS = {"/meta/version"}


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


@app.middleware("http")
async def enforce_authentication(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if path in _PUBLIC_PATHS or any(path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES):
        return await call_next(request)

    auth_header = request.headers.get("authorization") or ""
    token = ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()

    ensure_authenticated(token)
    return await call_next(request)


@app.middleware("http")
async def audit_requests(request: Request, call_next):  # pragma: no cover - exercised via API tests
    """Record a lightweight audit entry for every API interaction."""

    start = perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:  # pragma: no cover - ensures failures are logged
        duration_ms = round((perf_counter() - start) * 1000, 2)
        log_exception(
            "http.request.error",
            exc,
            method=request.method,
            path=request.url.path,
            duration_ms=duration_ms,
        )
        raise
    else:
        duration_ms = round((perf_counter() - start) * 1000, 2)
        if not request.url.path.startswith("/docs") and not request.url.path.startswith("/openapi"):
            log_event(
                "http.request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
            )
        return response


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    token = verify_login(payload.email, payload.password, payload.totp_code)
    return LoginResponse(access_token=token, expires_in=auth_manager.settings.token_ttl)


@app.get("/auth/status", response_model=AuthStatusResponse)
def auth_status() -> AuthStatusResponse:
    snapshot = auth_manager.snapshot()
    pending = auth_manager._store.verification_status()
    return AuthStatusResponse(
        email=snapshot.email,
        email_verified=bool(snapshot.email_verified),
        totp_required=bool(snapshot.totp_enabled),
        verification_pending=bool(pending.get("pending")),
    )


@app.post("/auth/register", response_model=RegistrationResponse)
def register(payload: RegistrationRequest) -> RegistrationResponse:
    code, expires_at = begin_registration(payload.email, payload.password)
    email_sent = send_verification_email(payload.email, code, expires_at)
    expose = _env_flag("AUTH_DEBUG_EXPOSE_CODES", True)
    message = (
        "등록을 위한 인증 코드가 이메일로 발송되었습니다."
        if email_sent
        else "등록 코드를 생성했지만 이메일 발송에 실패했습니다. 관리자에게 문의하거나 로그를 확인하세요."
    )
    response = RegistrationResponse(
        ok=True,
        message=message,
        expires_in=max(0, int(expires_at - time.time())),
        email_sent=email_sent,
    )
    if expose:
        response.verification_code = code
    return response


@app.post("/auth/register/resend", response_model=RegistrationResponse)
def resend_registration_code() -> RegistrationResponse:
    code, expires_at = resend_verification()
    snapshot = auth_manager.snapshot()
    email_sent = False
    if snapshot.email:
        email_sent = send_verification_email(snapshot.email, code, expires_at)
    expose = _env_flag("AUTH_DEBUG_EXPOSE_CODES", True)
    message = (
        "새 인증 코드가 이메일로 발송되었습니다."
        if email_sent
        else "새 인증 코드를 생성했지만 이메일 발송에 실패했습니다."
    )
    response = RegistrationResponse(
        ok=True,
        message=message,
        expires_in=max(0, int(expires_at - time.time())),
        email_sent=email_sent,
    )
    if expose:
        response.verification_code = code
    return response


@app.post("/auth/register/verify", response_model=VerifyEmailResponse)
def confirm_registration(payload: VerifyEmailRequest) -> VerifyEmailResponse:
    snapshot = auth_manager.snapshot()
    if payload.email.lower() != snapshot.email.lower():
        raise HTTPException(status_code=400, detail="등록된 이메일과 일치하지 않습니다.")
    secret, uri, qr = verify_email_code(payload.code)
    totp_required = auth_manager.requires_totp()
    return VerifyEmailResponse(
        ok=True,
        message="이메일 인증이 완료되었습니다. Google Authenticator에 보안 키를 등록하세요.",
        totp_required=totp_required,
        totp_secret=secret,
        totp_uri=uri,
        totp_qr=qr,
    )


@app.get("/auth/profile", response_model=AuthProfileResponse)
def auth_profile() -> AuthProfileResponse:
    snapshot = auth_manager.snapshot()
    rotated_at = snapshot.totp_rotated_at
    rotated_dt = (
        datetime.fromtimestamp(rotated_at, tz=timezone.utc) if rotated_at else None
    )
    enabled = bool(snapshot.totp_enabled)
    secret = snapshot.totp_secret if enabled else ""
    hint = (
        f"{secret[:4]}····{secret[-4:]}" if enabled and len(secret) >= 8 else (secret or "비활성화")
    )
    totp_uri = auth_manager.provisioning_uri() if enabled else ""
    totp_qr = auth_manager.provisioning_qr() if enabled else ""
    return AuthProfileResponse(
        email=snapshot.email,
        email_verified=bool(snapshot.email_verified),
        totp_enabled=enabled,
        totp_secret=secret,
        totp_uri=totp_uri,
        totp_qr=totp_qr,
        totp_hint=hint,
        totp_rotated_at=rotated_dt,
    )


@app.post("/auth/settings/password", response_model=MessageResponse)
def update_password(payload: UpdatePasswordRequest) -> MessageResponse:
    snapshot = auth_manager.snapshot()
    if not auth_manager.authenticate_primary(snapshot.email, payload.current_password):
        raise HTTPException(status_code=401, detail="현재 비밀번호가 올바르지 않습니다.")

    if len(payload.new_password.strip()) < 8:
        raise HTTPException(status_code=400, detail="새 비밀번호는 8자 이상이어야 합니다.")

    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="새 비밀번호가 기존 비밀번호와 동일합니다.")

    if auth_manager.requires_totp() and not auth_manager.verify_totp_code(payload.totp_code):
        raise HTTPException(status_code=401, detail="보안 코드가 올바르지 않습니다.")

    auth_manager.update_password(payload.new_password)
    return MessageResponse(message="비밀번호가 변경되었습니다.")


@app.post("/auth/settings/totp/reset", response_model=TotpResetResponse)
def reset_totp(payload: TotpResetRequest) -> TotpResetResponse:
    snapshot = auth_manager.snapshot()
    if not auth_manager.authenticate_primary(snapshot.email, payload.password):
        raise HTTPException(status_code=401, detail="현재 비밀번호가 올바르지 않습니다.")

    if auth_manager.requires_totp() and not auth_manager.verify_totp_code(payload.totp_code):
        raise HTTPException(status_code=401, detail="보안 코드가 올바르지 않습니다.")

    secret, uri, qr = auth_manager.rotate_totp()
    return TotpResetResponse(secret=secret, otpauth_uri=uri, qr_data_uri=qr)


_paper_broker: PaperBroker = paper_broker()
_paper_last_price_source: dict[str, str] = {}
_auto_trader = AutoTrader(broker=_paper_broker)
_paper_market_preference = "KRW-BTC"
_paper_interval_preference = "minute1"
_RECENT_HISTORY_CACHE_FINGERPRINT = 6
_RECENT_HISTORY_LIMIT = 12
_RECENT_HISTORY_PENALTY = 0.45
_RECENT_HISTORY_FRESH_BONUS = 0.12
_RECENT_HISTORY_CONFIDENCE_WEIGHT = 0.01
_SYNTHETIC_SOURCE_PENALTY = 0.35
_REAL_SOURCE_BONUS = 0.05

_RECOMMENDATION_CACHE: dict[
    Tuple[str, str, int, int, bool, Tuple[str, ...]],
    Tuple[float, MarketRecommendationsResponse],
] = {}
_RECOMMENDATION_CACHE_TTL = 90.0
_assistant_history_store = AssistantHistoryStore(
    Path(os.environ.get("ASSISTANT_HISTORY_PATH", "./data/assistant_history.json"))
)


def _utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime | None) -> datetime:
    """Normalise datetimes to timezone-aware UTC values."""

    if value is None:
        return _utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _record_assistant_history(
    response: "AssistantResponse", *, pushed_to_chat: Optional[bool] = None
) -> None:
    """Persist the latest assistant exchange to the shared history store."""

    entry = AssistantHistoryEntry(
        generated_at=_ensure_utc(response.generated_at),
        question=response.question,
        answer=response.answer,
        insights=list(response.insights or []),
        next_steps=list(response.next_steps or []),
        risk_notices=list(response.risk_notices or []),
        pushed_to_chat=bool(response.pushed_to_chat if pushed_to_chat is None else pushed_to_chat),
    )
    _assistant_history_store.append(entry)


def _safe_number(
    value: Optional[float],
    *,
    default: float = 0.0,
    lower: float | None = None,
    upper: float | None = None,
) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        numeric = default
    if not math.isfinite(numeric):
        numeric = default
    if lower is not None:
        numeric = max(lower, numeric)
    if upper is not None:
        numeric = min(upper, numeric)
    return numeric


def _compute_recommendation_features(
    insight: ai.MarketAIInsight,
) -> Dict[str, float | str]:
    """Derive enriched scoring features for AI TOP5 recommendations."""

    metrics = insight.metrics
    risk = insight.risk
    technical = insight.technical_confluence

    action_raw = insight.recommended_action or ""
    action_lower = action_raw.lower()
    action_bias = 0.0
    if "매수" in action_raw or "buy" in action_lower:
        action_bias = 8.0
    elif "매도" in action_raw or "sell" in action_lower:
        action_bias = -10.0
    elif "대기" in action_raw:
        action_bias = -4.0

    trend_strength = _safe_number(metrics.trend_strength)
    confidence_pct = _safe_number(insight.confidence_pct, lower=0.0, upper=100.0)
    price_change_pct = _safe_number(metrics.price_change_pct)
    breakout_probability = _safe_number(metrics.breakout_probability, lower=0.0, upper=1.0)
    sentiment = _safe_number(metrics.institutional_sentiment, lower=0.0, upper=1.0)
    liquidity = _safe_number(metrics.liquidity_score, lower=0.0)
    volatility_pct = _safe_number(metrics.volatility_pct, lower=0.0)
    surge_pct = _safe_number(metrics.surge_probability_pct, lower=0.0, upper=100.0)
    crash_pct = _safe_number(metrics.crash_probability_pct, lower=0.0, upper=100.0)
    shock_pct = _safe_number(metrics.shock_risk_pct, lower=0.0, upper=100.0)
    momentum_accel_pct = _safe_number(metrics.momentum_acceleration_pct)
    volatility_spike_score = _safe_number(metrics.volatility_spike_score, lower=0.0)
    impulse_strength_pct = _safe_number(metrics.impulse_strength_pct, lower=0.0)
    impulse_direction = (metrics.impulse_direction or "중립").lower()

    stop_loss_pct = _safe_number(risk.stop_loss_pct, lower=0.0005)
    take_profit_pct = _safe_number(risk.take_profit_pct or 0.0, lower=0.0)
    trailing_stop_pct = _safe_number(risk.trailing_stop_pct or 0.0, lower=0.0)
    position_size_pct = _safe_number(risk.position_size_pct, lower=0.0)

    reward_component = take_profit_pct if take_profit_pct > 0 else max(price_change_pct, 0.0)
    raw_risk_reward_ratio = reward_component / stop_loss_pct if stop_loss_pct > 0 else 1.0
    risk_reward_ratio = max(0.1, min(raw_risk_reward_ratio, 10.0))

    trend_score = max(0.0, trend_strength * 100) * 0.9
    confidence_score = max(0.0, confidence_pct) * 0.45
    momentum_score = max(0.0, price_change_pct)
    breakout_score = breakout_probability * 25
    sentiment_score = sentiment * 25
    liquidity_score = liquidity * 20
    regime_bonus = 6.0 if any(keyword in insight.regime for keyword in ("강세", "반등")) else 0.0

    impulse_bonus = 0.0
    if "상승" in impulse_direction:
        impulse_bonus = impulse_strength_pct * 0.45
    elif "하락" in impulse_direction:
        impulse_bonus = -impulse_strength_pct * 0.55

    confluence_score = _safe_number(technical.score, lower=0.0, upper=100.0)
    confluence_bonus = confluence_score * 0.55
    momentum_accel_bonus = max(0.0, momentum_accel_pct) * 0.5
    volatility_spike_bonus = max(0.0, volatility_spike_score - 1.0) * 6.0
    trailing_bonus = 4.0 if trailing_stop_pct > 0 else 0.0
    position_bonus = min(position_size_pct, 5.0) * 1.2
    surge_bonus = surge_pct * 0.35
    crash_penalty = crash_pct * 0.6
    shock_penalty = shock_pct * 0.4
    risk_reward_bonus = min(risk_reward_ratio, 6.0) * 9.0

    base_score = (
        trend_score
        + confidence_score
        + momentum_score
        + breakout_score
        + sentiment_score
        + liquidity_score
        + regime_bonus
        + action_bias
    )

    raw_score = (
        base_score
        + confluence_bonus
        + surge_bonus
        + risk_reward_bonus
        + momentum_accel_bonus
        + volatility_spike_bonus
        + trailing_bonus
        + position_bonus
        + impulse_bonus
        - crash_penalty
        - shock_penalty
    )

    risk_guard = max(0.4, 1.15 - min(volatility_pct, 220.0) / 220.0)
    risk_guard *= max(0.45, 1.1 - (crash_pct / 160.0))
    risk_guard *= min(1.35, 0.85 + (surge_pct / 140.0))

    score = max(0.0, raw_score * risk_guard)

    return {
        "score": score,
        "risk_reward_ratio": risk_reward_ratio,
        "surge_probability_pct": surge_pct,
        "crash_probability_pct": crash_pct,
        "shock_risk_pct": shock_pct,
        "technical_confluence_score": confluence_score,
        "technical_confluence_label": technical.label or "중립",
        "trailing_stop_pct": trailing_stop_pct,
        "volatility_pct": volatility_pct,
        "confidence_pct": confidence_pct,
        "trend_strength_pct": trend_strength * 100.0,
    }

def _derive_synthetic_seed(
    *,
    market: str,
    interval: str,
    count: int,
    seed: int | None = None,
) -> int:
    """Derive a deterministic seed for synthetic price generation."""

    if seed is not None:
        return seed

    token = f"{market.upper()}::{interval}::{count}"
    return zlib.crc32(token.encode("utf-8")) & 0xFFFFFFFF


def _sanitize_metrics_payload(metrics: ai.MarketAIMetrics) -> MarketAIMetricsPayload:
    return MarketAIMetricsPayload(
        fast_ema=_safe_number(metrics.fast_ema),
        slow_ema=_safe_number(metrics.slow_ema),
        rsi=_safe_number(metrics.rsi, lower=0.0, upper=100.0),
        macd=_safe_number(metrics.macd),
        macd_signal=_safe_number(metrics.macd_signal),
        macd_histogram=_safe_number(metrics.macd_histogram),
        volatility_pct=_safe_number(metrics.volatility_pct, lower=0.0),
        trend_strength=_safe_number(metrics.trend_strength),
        regime_score=_safe_number(metrics.regime_score),
        probability_of_trend=_safe_number(metrics.probability_of_trend, lower=0.0, upper=1.0),
        price_change_pct=_safe_number(metrics.price_change_pct),
        support_level=_safe_number(metrics.support_level, lower=0.0),
        resistance_level=_safe_number(metrics.resistance_level, lower=0.0),
        atr=_safe_number(metrics.atr, lower=0.0),
        hurst_exponent=_safe_number(metrics.hurst_exponent, lower=0.0, upper=1.0),
        bollinger_bandwidth_pct=_safe_number(metrics.bollinger_bandwidth_pct, lower=0.0),
        institutional_sentiment=_safe_number(metrics.institutional_sentiment, lower=0.0, upper=1.0),
        liquidity_score=_safe_number(metrics.liquidity_score, lower=0.0),
        breakout_probability=_safe_number(metrics.breakout_probability, lower=0.0, upper=1.0),
        volatility_regime=metrics.volatility_regime or "중립",
        volatility_spike_score=_safe_number(metrics.volatility_spike_score, lower=0.0),
        momentum_acceleration_pct=_safe_number(metrics.momentum_acceleration_pct),
        shock_risk_pct=_safe_number(metrics.shock_risk_pct, lower=0.0),
        impulse_direction=metrics.impulse_direction,
        impulse_strength_pct=_safe_number(metrics.impulse_strength_pct),
        regime_shift_probability=_safe_number(metrics.regime_shift_probability, lower=0.0, upper=1.0),
        surge_probability_pct=_safe_number(metrics.surge_probability_pct, lower=0.0, upper=100.0),
        crash_probability_pct=_safe_number(metrics.crash_probability_pct, lower=0.0, upper=100.0),
        institutional_alert_level=_safe_number(
            getattr(metrics, "institutional_alert_level", 0.0), lower=0.0, upper=100.0
        ),
        institutional_alerts=list(getattr(metrics, "institutional_alerts", []) or []),
        credible_sources=list(getattr(metrics, "credible_sources", []) or []),
    )


def _sanitize_confluence_payload(
    confluence: ai.TechnicalConfluence,
) -> TechnicalConfluencePayload:
    return TechnicalConfluencePayload(
        score=_safe_number(confluence.score, lower=0.0, upper=100.0),
        label=confluence.label,
        drivers=list(confluence.drivers),
        volume_confirmation=_safe_number(confluence.volume_confirmation, lower=0.0),
        pattern=confluence.pattern,
        squeeze_signal=confluence.squeeze_signal,
    )


def _sanitize_risk_payload(risk: ai.RiskControlAdvice) -> RiskControlAdvicePayload:
    return RiskControlAdvicePayload(
        stop_loss_pct=_safe_number(risk.stop_loss_pct, lower=0.0),
        take_profit_pct=_safe_number(risk.take_profit_pct, lower=0.0),
        trailing_stop_pct=
        None if risk.trailing_stop_pct is None else _safe_number(risk.trailing_stop_pct, lower=0.0),
        position_size_pct=_safe_number(risk.position_size_pct, lower=0.0),
        confidence_note=risk.confidence_note,
        notes=list(risk.notes),
    )


def _sanitize_loss_recovery_step(step: ai.LossRecoveryStep) -> LossRecoveryStepPayload:
    return LossRecoveryStepPayload(
        title=step.title,
        objective=step.objective,
        threshold_pct=_safe_number(step.threshold_pct, lower=0.0),
        actions=list(step.actions),
        guardrails=list(step.guardrails),
        metrics={key: _safe_number(value) for key, value in step.metrics.items()},
    )


def _serialize_loss_playbook(plan: ai.LossRecoveryPlaybook) -> LossRecoveryPlaybookResponse:
    return LossRecoveryPlaybookResponse(
        generated_at=_ensure_utc(plan.generated_at),
        realized_loss_krw=_safe_number(plan.realized_loss_krw),
        unrealized_loss_krw=_safe_number(plan.unrealized_loss_krw),
        loss_markets=list(plan.loss_markets),
        recovery_horizon=plan.recovery_horizon,
        steps=[_sanitize_loss_recovery_step(step) for step in plan.steps],
        risk_commandments=list(plan.risk_commandments),
        chart_playbook=list(plan.chart_playbook),
        institutional_briefs=list(plan.institutional_briefs),
        proprietary_edge=plan.proprietary_edge,
    )


def _sanitize_autopilot_plan(plan: ai.AutoPilotOrderPlan) -> AutoPilotPlanPayload:
    return AutoPilotPlanPayload(
        market=plan.market,
        side=plan.side,
        bias=plan.bias,
        order_type=plan.order_type,
        suggested_price=None if plan.suggested_price is None else _safe_number(plan.suggested_price),
        position_size_pct=_safe_number(plan.position_size_pct, lower=0.0),
        stop_loss_pct=_safe_number(plan.stop_loss_pct, lower=0.0),
        take_profit_pct=_safe_number(plan.take_profit_pct, lower=0.0),
        trailing_stop_pct=
        None if plan.trailing_stop_pct is None else _safe_number(plan.trailing_stop_pct, lower=0.0),
        confidence_pct=_safe_number(plan.confidence_pct, lower=0.0),
        reasoning=list(plan.reasoning),
        monitoring=list(plan.monitoring),
    )


def _sanitize_autopilot_execution(execution: AutoTraderExecution) -> AutoPilotExecutionPayload:
    return AutoPilotExecutionPayload(
        mode=execution.mode,
        market=execution.market,
        side=execution.side,
        price=_safe_number(execution.price),
        volume=_safe_number(execution.volume, lower=0.0),
        value=_safe_number(execution.value, lower=0.0),
        executed_at=execution.executed_at,
        detail=execution.detail,
    )


def _load_candles_with_fallback(
    market: str, interval: str, count: int
) -> Tuple[List[trading.Candle], str]:
    try:
        data = fetch_upbit_candles(market=market, interval=interval, count=count)
        candles = data.candles
        source = data.source
    except MarketDataError:
        days = max(count, 120)
        seed = _derive_synthetic_seed(market=market, interval=interval, count=days)
        candles = trading.generate_synthetic_prices(days=days, seed=seed)
        source = "synthetic"

    if len(candles) < 30:
        days = max(count, 120)
        seed = _derive_synthetic_seed(market=market, interval=interval, count=days)
        candles = trading.generate_synthetic_prices(days=days, seed=seed)
        source = "synthetic"

    return candles, source


def _neutral_autopilot_plan(market: str, reason: str) -> ai.AutoPilotOrderPlan:
    detail = reason.strip() if reason else "내부 오류로 관망 상태를 유지합니다."
    return ai.AutoPilotOrderPlan(
        market=market,
        side="flat",
        bias="neutral",
        order_type="monitor",
        suggested_price=None,
        position_size_pct=0.0,
        stop_loss_pct=0.0,
        take_profit_pct=None,
        trailing_stop_pct=None,
        confidence_pct=0.0,
        reasoning=["내부 오류로 관망 모드로 전환했습니다.", detail],
        monitoring=["시장 데이터를 재수집하는 중입니다."],
    )


def _paper_order_history(order: PaperOrder) -> TradeHistoryItemPayload:
    return TradeHistoryItemPayload(
        executed_at=_ensure_utc(order.executed_at),
        market=order.market,
        side=order.side,
        mode=OrderMode.PAPER,
        price=_safe_number(order.price, lower=0.0),
        volume=_safe_number(order.volume, lower=0.0),
        value=_safe_number(order.price * order.volume, lower=0.0),
        fee=_safe_number(order.fee, lower=0.0),
        realized_pnl=_safe_number(order.realized_pnl),
        source="paper",
        note="페이퍼 주문",
    )


def _execution_history(execution: AutoTraderExecution) -> TradeHistoryItemPayload:
    return TradeHistoryItemPayload(
        executed_at=_ensure_utc(execution.executed_at),
        market=execution.market,
        side=execution.side,
        mode=execution.mode,
        price=_safe_number(execution.price, lower=0.0),
        volume=_safe_number(execution.volume, lower=0.0),
        value=_safe_number(execution.value, lower=0.0),
        fee=0.0,
        realized_pnl=0.0,
        source="autopilot",
        note=execution.detail,
    )


def _adaptive_profile_payload(
    profile: Optional[AdaptiveProfile],
) -> Optional[AdaptiveProfilePayload]:
    if profile is None:
        return None
    return AdaptiveProfilePayload(
        min_confidence_pct=_safe_number(profile.min_confidence_pct, lower=0.0, upper=100.0),
        max_position_pct=_safe_number(profile.max_position_pct, lower=0.0, upper=1.0),
        risk_appetite=_safe_number(profile.risk_appetite, lower=0.0, upper=1.0),
        rolling_return_pct=_safe_number(profile.rolling_return_pct),
        rolling_volatility_pct=_safe_number(profile.rolling_volatility_pct, lower=0.0),
        sharpe_like=_safe_number(profile.sharpe_like),
        trades_tracked=int(profile.trades_tracked),
        last_updated_at=profile.last_updated_at,
    )


def _sanitize_allocation(allocation: ai.PortfolioAllocation) -> dict:
    return {
        "symbol": allocation.symbol,
        "name": allocation.name,
        "asset_type": allocation.asset_type,
        "weight": _safe_number(allocation.weight, lower=0.0, upper=1.0),
        "allocation_krw": _safe_number(allocation.allocation_krw, lower=0.0),
        "expected_return_pct": _safe_number(allocation.expected_return_pct),
        "expected_volatility_pct": _safe_number(allocation.expected_volatility_pct, lower=0.0),
        "rationale": allocation.rationale,
    }


def _get_value(obj: object, key: str, default: float | str | None = None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _summarise_recommendations_for_chat(
    payload: MarketRecommendationsResponse,
) -> Optional[str]:
    base = payload.base_currency.upper()
    interval = payload.interval
    head = list(payload.recommendations[:3])
    label_map = {"upbit": "실거래", "synthetic": "합성", "mixed": "혼합"}
    source_label = label_map.get(payload.analysis_source, payload.analysis_source)

    if not head:
        if payload.errors:
            return f"[AI 추천] {base}/{interval} 오류: {payload.errors[0]}"
        return f"[AI 추천] {base}/{interval} 추천 결과 없음"

    entries = []
    for item in head:
        entries.append(
            f"{item.market} {item.recommended_action} (신뢰 {item.confidence_pct:.1f}%, 점수 {item.score:.1f})"
        )

    summary = ", ".join(entries)
    if payload.errors:
        summary += f" · 경고 {payload.errors[0]}"
    return f"[AI 추천] {base}/{interval} {source_label}: {summary}"


def _summarise_market_intelligence_for_chat(payload: MarketAIResponse) -> Optional[str]:
    confluence = payload.technical_confluence
    return (
        f"[시장 인텔리전스] {payload.market} {payload.interval}: {payload.recommended_action} "
        f"(신뢰 {payload.confidence_pct:.1f}%, 레짐 {payload.regime}, 컨플루언스 {confluence.label} {confluence.score:.1f})"
    )


def _summarise_portfolio_plan_for_chat(
    payload: PortfolioOptimizationResponse,
) -> Optional[str]:
    if not payload.allocations:
        return None

    head = payload.allocations[:3]
    parts = []
    for allocation in head:
        symbol = _get_value(allocation, "symbol", "-")
        weight = _safe_number(_get_value(allocation, "weight", 0.0), lower=0.0, upper=1.0) * 100
        parts.append(f"{symbol} {weight:.1f}%")

    return (
        f"[AI 포트폴리오] {payload.risk_profile_label}: 기대수익 {payload.expected_return_pct:.2f}% · "
        f"변동성 {payload.expected_volatility_pct:.2f}% | "
        + ", ".join(parts)
    )


def _summarise_blueprint_for_chat(payload: PortfolioBlueprintResponse) -> Optional[str]:
    if not payload.allocations:
        return None

    stable_pct = float(payload.summary.bucket_weights_pct.get("stable", 0.0))
    aggressive_pct = float(payload.summary.bucket_weights_pct.get("aggressive", 0.0))
    entries = [
        f"{allocation.symbol} {allocation.weight_pct:.1f}%"
        for allocation in payload.allocations[:3]
    ]

    return (
        f"[포트폴리오 블루프린트] 안정 {stable_pct:.1f}% · 공격 {aggressive_pct:.1f}% | "
        + ", ".join(entries)
    )


def _summarise_rebalance_for_chat(payload: RebalanceResponse) -> Optional[str]:
    if not payload.orders:
        return "[리밸런싱] 조정 필요 없음"

    ranked = sorted(payload.orders.items(), key=lambda item: abs(item[1]), reverse=True)
    top = []
    for symbol, amount in ranked[:3]:
        rounded = round(float(amount))
        if rounded == 0:
            continue
        prefix = "+" if rounded > 0 else ""
        top.append(f"{symbol} {prefix}{rounded:,} KRW")

    if not top:
        return "[리밸런싱] 조정 필요 없음"
    return "[리밸런싱] " + ", ".join(top)


def _summarise_simulation_for_chat(payload: SimulationResponse) -> Optional[str]:
    source = payload.price_source
    base = "[전략 시뮬레이션]"
    if source != "upbit":
        return f"{base} 실시간 시세 없음 - 합성 데이터 기반 결과"

    headline = f"{base} {payload.market or '시장 미지정'} 총손익 {payload.profit_krw:,.0f} KRW"
    return headline


def _summarise_copilot_for_chat(payload: CopilotResponse) -> Optional[str]:
    plan = payload.autopilot
    highlight = (payload.summary_points[0] if payload.summary_points else payload.answer).strip()
    if len(highlight) > 80:
        highlight = highlight[:77] + "..."
    return (
        f"[AI 코파일럿] {plan.market} {plan.side.upper()} ({plan.bias}) · 신뢰 {plan.confidence_pct:.1f}% · "
        f"포지션 {plan.position_size_pct:.1f}% - {highlight}"
    )


def _summarise_assistant_for_chat(payload: AssistantResponse) -> Optional[str]:
    highlight = payload.answer.strip()
    if len(highlight) > 80:
        highlight = highlight[:77] + "..."
    parts = [highlight]
    if payload.autopilot:
        parts.append(
            f"플랜 {payload.autopilot.market} {payload.autopilot.side.upper()} · 신뢰 {payload.autopilot.confidence_pct:.1f}%"
        )
    if payload.insights:
        parts.append(payload.insights[0])
    return "[AI 어시스턴트] " + " | ".join(parts)


def _summarise_autopilot_status_for_chat(payload: AutoPilotStatusResponse) -> Optional[str]:
    status = "ON" if payload.running else "OFF"
    parts = [status]
    if payload.config:
        parts.append(f"시장 {payload.config.market} · 모드 {payload.config.mode.upper()}")
    if payload.last_plan:
        parts.append(
            f"플랜 {payload.last_plan.market} {payload.last_plan.side.upper()} ({payload.last_plan.bias}) "
            f"신뢰 {payload.last_plan.confidence_pct:.1f}%"
        )
    if payload.last_execution:
        parts.append(
            f"최근 체결 {payload.last_execution.market} {payload.last_execution.side.upper()} "
            f"{payload.last_execution.volume:.4f} @ {payload.last_execution.price:.0f}"
        )
    if payload.last_skip_reason:
        parts.append(f"관망 사유 {payload.last_skip_reason}")
    if payload.last_error:
        parts.append(f"경고 {payload.last_error}")

    if not parts:
        return None
    return "[오토파일럿] " + " | ".join(parts)


_CRITICAL_CHAT_SUMMARY_RULES: dict[str, tuple[str, ...]] = {
    "autopilot-status": ("체결", "경고", "실패", "오류", "중지", "시작"),
    "market-recommendations": ("경고", "오류", "없음", "합성", "혼합"),
}


def _is_critical_chat_summary(key: str, message: Optional[str]) -> bool:
    if not message:
        return False
    keywords = _CRITICAL_CHAT_SUMMARY_RULES.get(key)
    if not keywords:
        return False
    return any(keyword in message for keyword in keywords)


def _push_chat_summary(key: str, message: Optional[str]) -> None:
    if _is_critical_chat_summary(key, message):
        notifications.notify_synology_chat_on_change(key, message)


def _autopilot_status_payload(state: AutoTraderState) -> AutoPilotStatusResponse:
    config_payload = None
    if state.config:
        config_payload = AutoPilotConfigPayload(
            mode=state.config.mode,
            market=state.config.market,
            interval=state.config.interval,
            risk_appetite=_safe_number(state.config.risk_appetite, lower=0.0, upper=1.0),
            capital=_safe_number(state.config.capital, lower=0.0),
            poll_interval=_safe_number(state.config.poll_interval, lower=15.0),
            include_portfolio=state.config.include_portfolio,
            max_position_pct=_safe_number(state.config.max_position_pct, lower=0.0, upper=1.0),
            min_confidence_pct=_safe_number(state.config.min_confidence_pct, lower=0.0, upper=100.0),
            auto_select_market=state.config.auto_select_market,
            recommendation_base=state.config.recommendation_base,
            recommendation_interval=state.config.recommendation_interval,
            recommendation_max_markets=int(state.config.recommendation_max_markets),
            recommendation_include_warnings=state.config.recommendation_include_warnings,
            max_trades_per_cycle=int(state.config.max_trades_per_cycle),
        )

    execution_payload = None
    if state.last_execution:
        execution_payload = _sanitize_autopilot_execution(state.last_execution)

    plan_payload = None
    if state.last_plan:
        plan_payload = _sanitize_autopilot_plan(state.last_plan)

    logs = [
        AutoPilotLogEntryPayload(
            timestamp=entry.timestamp,
            level=entry.level,
            message=entry.message,
        )
        for entry in state.logs
    ]

    executions = []
    for execution in state.executions:
        try:
            executions.append(_sanitize_autopilot_execution(execution))
        except Exception:
            continue

    return AutoPilotStatusResponse(
        running=state.running,
        config=config_payload,
        last_plan=plan_payload,
        last_execution=execution_payload,
        last_error=state.last_error,
        last_cycle_started_at=state.last_cycle_started_at,
        last_cycle_completed_at=state.last_cycle_completed_at,
        next_cycle_due_at=state.next_cycle_due_at,
        logs=logs,
        recent_recommendations=list(state.last_recommendations),
        recommendation_source=state.last_recommendation_source,
        recent_executions=executions,
        last_skip_reason=state.last_skip_reason,
        candidate_markets=list(state.recommendation_markets),
        candidate_market_count=state.recommendation_market_count,
        analysis_markets=list(state.analysis_markets),
        analysis_market_count=state.analysis_market_count,
        candidate_rotation_cursor=state.candidate_rotation_cursor,
        repeat_market_count=state.repeat_market_count,
        recent_markets=list(state.recent_markets),
        market_rotation_queue=list(state.market_rotation_queue),
        network_status=state.last_network_status,
        network_message=state.last_network_message,
        network_detail=state.last_network_detail,
        network_checked_at=state.last_network_checked_at,
        network_backoff_seconds=state.last_network_backoff or 0.0,
        effective_min_confidence=_safe_number(state.effective_min_confidence, lower=0.0),
        app_version=APP_VERSION,
        adaptive_profile=_adaptive_profile_payload(state.adaptive_profile),
    )


def _build_autopilot_config(payload: AutoPilotConfigRequest) -> AutoTraderConfig:
    return AutoTraderConfig(
        mode=payload.mode,
        market=payload.market.upper(),
        interval=payload.interval,
        risk_appetite=payload.risk_appetite,
        capital=payload.capital,
        poll_interval=payload.poll_interval,
        include_portfolio=payload.include_portfolio,
        max_position_pct=payload.max_position_pct,
        min_confidence_pct=payload.min_confidence_pct,
        auto_select_market=payload.auto_select_market,
        recommendation_base=payload.recommendation_base.upper(),
        recommendation_interval=payload.recommendation_interval,
        recommendation_max_markets=payload.recommendation_max_markets,
        recommendation_include_warnings=payload.recommendation_include_warnings,
        max_trades_per_cycle=payload.max_trades_per_cycle,
    )


@app.get("/meta/version", response_model=MetaVersionResponse)
def get_meta_version() -> MetaVersionResponse:
    """Expose the current application version for dashboard displays."""

    return MetaVersionResponse(version=APP_VERSION)


def _news_items(entries: Iterable[dict]) -> list[NewsItem]:
    items: list[NewsItem] = []
    for entry in entries:
        try:
            items.append(
                NewsItem(
                    title=entry["title"],
                    url=entry["url"],
                    source=entry.get("source", ""),
                    published_at=entry.get("published_at", ""),
                )
            )
        except KeyError:
            continue
    return items


def _convert_assets(assets: Optional[list[PortfolioAssetInput]]):
    if not assets:
        return None
    converted = []
    for asset in assets:
        converted.append(
            ai.PortfolioAsset(
                symbol=asset.symbol.upper(),
                name=asset.name or asset.symbol.upper(),
                asset_type=asset.asset_type,
                expected_return_pct=asset.expected_return_pct,
                expected_volatility_pct=asset.expected_volatility_pct,
                risk_score=asset.risk_score,
                narrative=asset.narrative or "사용자 정의 자산",
                market=asset.market.upper() if asset.market else None,
            )
        )
    return converted


def _serialize_position(position: PaperPosition) -> PaperPositionPayload:
    return PaperPositionPayload(
        market=position.market,
        volume=position.volume,
        average_price=position.average_price,
        market_price=position.market_price,
        market_value=position.market_value,
        unrealized_pnl=position.unrealized_pnl,
    )


def _serialize_order(order: PaperOrder) -> PaperOrderPayload:
    return PaperOrderPayload(
        order_id=order.order_id,
        market=order.market,
        side=order.side,
        price=order.price,
        volume=order.volume,
        fee=order.fee,
        realized_pnl=order.realized_pnl,
        executed_at=_ensure_utc(order.executed_at),
    )


def _serialize_balance(snapshot) -> PaperBalancePayload:
    return PaperBalancePayload(
        cash=snapshot.cash,
        portfolio_value=snapshot.portfolio_value,
        last_updated=_ensure_utc(snapshot.last_update),
        initial_cash=getattr(snapshot, "initial_cash", _paper_broker.initial_cash),
        positions=[_serialize_position(pos) for pos in snapshot.positions],
        orders=[_serialize_order(order) for order in snapshot.orders],
    )


@app.get("/trading/autopilot/status", response_model=AutoPilotStatusResponse)
def get_autopilot_status() -> AutoPilotStatusResponse:
    state = _auto_trader.status()
    response = _autopilot_status_payload(state)
    _push_chat_summary("autopilot-status", _summarise_autopilot_status_for_chat(response))
    return response


@app.post("/trading/autopilot/start", response_model=AutoPilotStatusResponse)
def start_autopilot(payload: AutoPilotConfigRequest) -> AutoPilotStatusResponse:
    state = _auto_trader.start(_build_autopilot_config(payload))
    response = _autopilot_status_payload(state)
    _push_chat_summary("autopilot-status", _summarise_autopilot_status_for_chat(response))
    log_event(
        "autopilot.start",
        running=bool(state.running),
        market=response.last_plan.market if response.last_plan else None,
        config=payload.dict(),
    )
    return response


@app.post("/trading/autopilot/stop", response_model=AutoPilotStatusResponse)
def stop_autopilot() -> AutoPilotStatusResponse:
    state = _auto_trader.stop()
    response = _autopilot_status_payload(state)
    _push_chat_summary("autopilot-status", _summarise_autopilot_status_for_chat(response))
    log_event(
        "autopilot.stop",
        running=bool(state.running),
        last_error=state.last_error,
    )
    return response


@app.post("/notifications/chat")
def post_chat_notification(payload: ChatNotificationRequest) -> dict:
    sent = notifications.notify_synology_chat(payload.message)
    status = notifications.get_synology_chat_status()
    if not sent:
        detail = status.get("last_error") or "Synology Chat 웹훅 전송이 실패했습니다."
        raise HTTPException(status_code=502, detail=detail)
    return {
        "status": "sent",
        "last_attempt_at": status.get("last_attempt_at"),
        "last_success_at": status.get("last_success_at"),
    }


@app.get("/notifications/chat/status", response_model=ChatNotificationStatus)
def get_chat_notification_status() -> ChatNotificationStatus:
    status = notifications.get_synology_chat_status()
    return ChatNotificationStatus(**status)


@app.post("/notifications/chat/assistant", response_model=ChatAssistantResponse)
def post_chat_assistant(payload: ChatAssistantRequest) -> ChatAssistantResponse:
    assistant_payload = AssistantRequest(
        question=payload.question,
        market=payload.market,
        interval=payload.interval,
        risk_appetite=payload.risk_appetite,
        capital=payload.capital,
        include_autopilot=True,
        include_chat_push=False,
    )
    assistant_response = run_ai_assistant(assistant_payload)

    lines = [
        "[AI 어시스턴트] Synology Chat 보고",  # consistent header
        f"질문: {assistant_response.question}",
        f"답변: {assistant_response.answer}",
    ]
    if assistant_response.next_steps:
        lines.append("다음 단계:")
        lines.extend(f"- {item}" for item in assistant_response.next_steps[:3])
    if assistant_response.risk_notices:
        lines.append("주의 사항:")
        lines.extend(f"- {notice}" for notice in assistant_response.risk_notices[:3])

    message = "\n".join(lines)
    sent = notifications.notify_synology_chat(message)
    assistant_response.pushed_to_chat = bool(sent)

    if sent:
        history = _assistant_history_store.list()
        if history:
            latest = history[0]
            updated = AssistantHistoryEntry(
                generated_at=latest.generated_at,
                question=latest.question,
                answer=latest.answer,
                insights=list(latest.insights),
                next_steps=list(latest.next_steps),
                risk_notices=list(latest.risk_notices),
                pushed_to_chat=True,
            )
            _assistant_history_store.replace([updated, *history[1:]])

    return ChatAssistantResponse(
        question=assistant_response.question,
        answer=assistant_response.answer,
        next_steps=assistant_response.next_steps,
        risk_notices=assistant_response.risk_notices,
        sent=bool(sent),
        message=message,
    )


@app.post("/notifications/chat/digest/flush", response_model=ChatDigestFlushResponse)
def flush_chat_digest(payload: ChatDigestFlushRequest) -> ChatDigestFlushResponse:
    reports = notifications.flush_due_digests(
        now=_utcnow(),
        category=payload.category,
        force=payload.force,
    )
    response_items: List[ChatDigestReport] = []
    for report in reports:
        raw_date = report.get("date", "")
        try:
            parsed_date = datetime.strptime(str(raw_date), "%Y-%m-%d").date()
        except ValueError:
            parsed_date = _utcnow().date()
        message = str(report.get("message", ""))
        preview = message.split("\n", 1)[0] if message else ""
        response_items.append(
            ChatDigestReport(
                category=str(report.get("category", "general")),
                date=parsed_date,
                sent=bool(report.get("sent", False)),
                message_preview=preview,
            )
        )
    return ChatDigestFlushResponse(reports=response_items)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/strategies/simulate", response_model=SimulationResponse)
def simulate_strategy(payload: SimulationRequest) -> SimulationResponse:
    candles: list[trading.Candle]
    strategy_market: Optional[str] = payload.market.upper() if payload.market else None
    interval = payload.interval or "minute60"
    fetch_count = 200
    fallback_market = (payload.market or "KRW-BTC").upper()
    price_source: str = "manual"
    price_message: Optional[str] = None
    price_detail: Optional[str] = None

    if payload.prices:
        candles = [
            trading.Candle(
                timestamp=item.timestamp,
                open=item.open,
                high=item.high,
                low=item.low,
                close=item.close,
                volume=item.volume,
            )
            for item in payload.prices
        ]
        price_source = "manual"
        price_message = "사용자 제공 시세를 사용했습니다."
    elif payload.use_live_data or payload.market:
        strategy_market = fallback_market
        try:
            market_data = fetch_upbit_candles(
                market=fallback_market,
                interval=interval,  # type: ignore[arg-type]
                count=fetch_count,
            )
        except MarketDataError as exc:
            market_data = None
            price_detail = str(exc)
        if market_data:
            candles = market_data.candles
            price_source = market_data.source
            price_message = market_data.message or None
            if market_data.detail:
                price_detail = market_data.detail
        else:
            candles = []
            price_source = "synthetic"

        if not candles:
            days = max(fetch_count, 120)
            seed = _derive_synthetic_seed(
                market=fallback_market,
                interval=interval,
                count=days,
                seed=payload.seed,
            )
            candles = trading.generate_synthetic_prices(days=days, seed=seed)
            price_source = "synthetic"
            price_message = price_message or "업비트 시세를 가져오지 못해 합성 데이터를 사용했습니다."
    else:
        days = max(fetch_count, 120)
        seed = _derive_synthetic_seed(
            market=fallback_market,
            interval=interval,
            count=days,
            seed=payload.seed,
        )
        candles = trading.generate_synthetic_prices(days=days, seed=seed)
        price_source = "synthetic"
        price_message = "시뮬레이션 시세를 사용했습니다."

    try:
        report = trading.run_ema_strategy(
            candles,
            market=strategy_market,
            fast_period=payload.fast_period,
            slow_period=payload.slow_period,
            initial_capital=payload.initial_capital,
            fee_rate=payload.fee_rate,
            risk_per_trade_pct=payload.risk_per_trade_pct,
            stop_loss_pct=payload.stop_loss_pct,
            take_profit_pct=payload.take_profit_pct,
            trailing_stop_pct=payload.trailing_stop_pct,
        )
    except ValueError as exc:  # pragma: no cover - validated by Pydantic
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    trades = [
        TradePayload(
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            quantity=trade.quantity,
            pnl=trade.pnl,
            return_pct=trade.return_pct,
            duration_bars=trade.duration_bars,
            exit_reason=trade.exit_reason,
            market=trade.market,
        )
        for trade in report.trades
    ]

    trade_summary = trading.summarize_trades(report.trades)

    response = SimulationResponse(
        market=report.market,
        initial_capital=report.initial_capital,
        ending_equity=report.ending_equity,
        profit_krw=report.ending_equity - report.initial_capital,
        total_return_pct=report.total_return_pct,
        annualized_return_pct=report.annualized_return_pct,
        max_drawdown_pct=report.max_drawdown_pct,
        volatility_pct=report.volatility_pct,
        sharpe_ratio=report.sharpe_ratio,
        sortino_ratio=report.sortino_ratio,
        exposure_time_pct=report.exposure_time_pct,
        calmar_ratio=report.calmar_ratio,
        value_at_risk_pct=report.value_at_risk_pct,
        profit_factor=report.profit_factor,
        expectancy_pct=report.expectancy_pct,
        avg_trade_duration_bars=report.avg_trade_duration_bars,
        ulcer_index=report.ulcer_index,
        downside_deviation_pct=report.downside_deviation_pct,
        recovery_factor=report.recovery_factor,
        average_win_pct=report.average_win_pct,
        average_loss_pct=report.average_loss_pct,
        win_loss_ratio=report.win_loss_ratio,
        tail_ratio=report.tail_ratio,
        omega_ratio=report.omega_ratio,
        kelly_fraction_pct=report.kelly_fraction_pct,
        max_consecutive_wins=report.max_consecutive_wins,
        max_consecutive_losses=report.max_consecutive_losses,
        skewness=report.skewness,
        kurtosis=report.kurtosis,
        average_drawdown_pct=report.average_drawdown_pct,
        pain_index=report.pain_index,
        max_runup_pct=report.max_runup_pct,
        trades=trades,
        equity_curve=report.equity_curve,
        trade_summary=trade_summary,
        monte_carlo_summary=report.monte_carlo_summary,
        price_source=price_source,
        price_message=price_message,
        price_detail=price_detail,
        integrity_score=report.integrity_score,
        integrity_flags=report.integrity_flags,
    )

    if response.price_source not in {"upbit", "upbit_alt", "upbit_stale"}:
        caution = "실시간 업비트 시세 부재로 합성 데이터를 사용했습니다. 수익률은 참고용입니다."
        if response.price_message:
            response.price_message = f"{response.price_message} {caution}"
        else:
            response.price_message = caution

    if response.integrity_score < 60:
        summary = ", ".join(response.integrity_flags) if response.integrity_flags else "세부 사유 없음"
        warning = f"데이터 무결성 점수 {response.integrity_score:.0f}점: {summary}"
        if response.price_message:
            response.price_message = f"{response.price_message} {warning}"
        else:
            response.price_message = warning

    _push_chat_summary("strategy-sim", _summarise_simulation_for_chat(response))
    return response


@app.get("/market/list", response_model=MarketListResponse)
def list_markets(only_krw: bool = True) -> MarketListResponse:
    listing = fetch_upbit_markets(only_krw=only_krw)
    markets = [
        MarketInfoPayload(
            market=item.market,
            korean_name=item.korean_name,
            english_name=item.english_name,
            base_currency=item.base_currency,
            quote_currency=item.quote_currency,
            market_warning=item.market_warning,
            trading_suspended=item.trading_suspended,
        )
        for item in listing.markets
    ]

    return MarketListResponse(
        generated_at=_utcnow(),
        source=listing.source,
        markets=markets,
        groups=_build_market_groups(markets),
        status=listing.status,
        message=listing.message,
        detail=listing.detail,
        checked_at=listing.checked_at,
        backoff_seconds_remaining=listing.backoff_seconds_remaining,
    )


def _score_market_recommendation(insight: ai.MarketAIInsight) -> float:
    return float(_compute_recommendation_features(insight)["score"])


def _format_recommendation_reason(
    insight: ai.MarketAIInsight,
    features: Dict[str, float | str],
) -> str:
    metrics = insight.metrics
    confidence = _safe_number(insight.confidence_pct, lower=0.0, upper=100.0)
    trend_pct = _safe_number(metrics.trend_strength * 100, default=0.0)
    volatility_pct = _safe_number(metrics.volatility_pct, lower=0.0)
    institutional_pct = _safe_number(metrics.institutional_sentiment * 100, lower=0.0, upper=100.0)
    breakout_pct = _safe_number(metrics.breakout_probability * 100, lower=0.0, upper=100.0)
    surge_pct = _safe_number(features.get("surge_probability_pct"), lower=0.0, upper=100.0)
    crash_pct = _safe_number(features.get("crash_probability_pct"), lower=0.0, upper=100.0)
    risk_reward = _safe_number(features.get("risk_reward_ratio"), lower=0.0)
    confluence_label = str(features.get("technical_confluence_label") or "중립")
    confluence_score = _safe_number(features.get("technical_confluence_score"), lower=0.0, upper=100.0)
    return (
        f"{insight.regime} · 컨플루언스 {confluence_label}({confluence_score:.0f}) · "
        f"R/R {risk_reward:.2f} · 상승 {surge_pct:.1f}% vs 하락 {crash_pct:.1f}% · "
        f"신뢰도 {confidence:.1f}% · 추세 {trend_pct:.2f}% · 변동성 {volatility_pct:.2f}% · "
        f"기관 {institutional_pct:.1f}% · 돌파 {breakout_pct:.1f}%"
    )


_INTERVAL_LABELS: Dict[str, str] = {
    "minute1": "1분",
    "minute3": "3분",
    "minute5": "5분",
    "minute15": "15분",
    "minute30": "30분",
    "minute60": "60분",
    "minute240": "4시간",
    "day": "일간",
    "week": "주간",
    "month": "월간",
}


def _evaluate_market_candidate(
    info: MarketInfo,
    interval: str,
) -> tuple[str, Optional[MarketRecommendationPayload], Optional[str]]:
    """Analyse a single market with multi-timeframe confirmation."""

    interval = interval or "minute60"
    primary_interval = interval

    # Primary timeframe receives full weight; supporting timeframes receive
    # progressively smaller weights so that consensus strengthens conviction
    # without letting a single noisy candle dominate the recommendation.
    interval_plan: list[tuple[str, float]] = [(primary_interval, 1.0)]
    if primary_interval != "minute15":
        interval_plan.append(("minute15", 0.6))
    if primary_interval != "minute60":
        interval_plan.append(("minute60", 0.85))
    if primary_interval not in {"minute240", "day", "week", "month"}:
        interval_plan.append(("minute240", 0.45))

    analyses: list[tuple[float, str, MarketData, ai.MarketAIInsight, Dict[str, float | str]]] = []
    sources: set[str] = set()
    failures: list[str] = []

    for frame, weight in interval_plan:
        if any(frame == entry[1] for entry in analyses):
            continue
        try:
            candle_data = fetch_upbit_candles(
                market=info.market,
                interval=frame,
                count=200,
            )
        except MarketDataError as exc:
            failures.append(f"{info.market} {frame}: {exc}")
            continue
        except Exception as exc:  # pragma: no cover - defensive guard
            failures.append(f"{info.market} {frame}: {exc}")
            continue

        if len(candle_data.candles) < 30:
            failures.append(f"{info.market} {frame}: 캔들이 부족합니다")
            continue

        try:
            insight = ai.analyse_market(
                candle_data.candles,
                market=info.market,
                interval=frame,
            )
        except Exception as exc:
            failures.append(f"{info.market} {frame}: {exc}")
            continue

        features = _compute_recommendation_features(insight)
        analyses.append((weight, frame, candle_data, insight, features))
        sources.add(candle_data.source)

    if not analyses:
        error_message = failures[0] if failures else f"{info.market}: 분석 실패"
        return (error_message, None, None)

    total_weight = sum(weight for weight, *_ in analyses)
    if total_weight <= 0:
        return (f"{info.market}: 가중치 계산 오류", None, None)

    # Choose the primary analysis (matching the requested interval) to drive
    # user-facing messaging while still blending the metrics across timeframes.
    try:
        primary_entry = next(entry for entry in analyses if entry[1] == primary_interval)
    except StopIteration:
        primary_entry = analyses[0]

    primary_weight, primary_frame, primary_data, primary_insight, primary_features = primary_entry

    def _weighted_average(extractor: Callable[[ai.MarketAIInsight, Dict[str, float | str]], float]) -> float:
        value = 0.0
        for weight, _frame, _data, insight, features in analyses:
            value += weight * extractor(insight, features)
        return value / total_weight

    score = _weighted_average(lambda _insight, feats: _safe_number(feats.get("score"), lower=0.0))
    confidence = _weighted_average(lambda insight, _feats: _safe_number(insight.confidence_pct, lower=0.0, upper=100.0))
    def _metric_average(getter: Callable[[ai.MarketAIInsight], float], *, scale: float = 1.0, lower: float | None = None, upper: float | None = None) -> float:
        raw = 0.0
        for weight, _frame, _data, insight, _features in analyses:
            raw += weight * getter(insight)
        averaged = raw / total_weight
        scaled = averaged * scale
        return _safe_number(scaled, lower=lower, upper=upper)

    price_change_pct = _metric_average(lambda insight: insight.metrics.price_change_pct)
    trend_strength_pct = _metric_average(lambda insight: insight.metrics.trend_strength, scale=100.0)
    volatility_pct = _metric_average(lambda insight: insight.metrics.volatility_pct, lower=0.0)
    institutional_pct = _metric_average(
        lambda insight: insight.metrics.institutional_sentiment,
        scale=100.0,
        lower=0.0,
        upper=100.0,
    )
    breakout_pct = _metric_average(
        lambda insight: insight.metrics.breakout_probability,
        scale=100.0,
        lower=0.0,
        upper=100.0,
    )

    surge_pct = _weighted_average(
        lambda _insight, feats: _safe_number(feats.get("surge_probability_pct"), lower=0.0, upper=100.0)
    )
    crash_pct = _weighted_average(
        lambda _insight, feats: _safe_number(feats.get("crash_probability_pct"), lower=0.0, upper=100.0)
    )
    risk_reward_ratio = _weighted_average(
        lambda _insight, feats: _safe_number(feats.get("risk_reward_ratio"), lower=0.0)
    )
    technical_score = _weighted_average(
        lambda _insight, feats: _safe_number(feats.get("technical_confluence_score"), lower=0.0, upper=100.0)
    )
    shock_risk_pct = _weighted_average(
        lambda _insight, feats: _safe_number(feats.get("shock_risk_pct"), lower=0.0, upper=100.0)
    )

    last_price = _safe_number(primary_data.candles[-1].close, lower=0.0)

    primary_reason = _format_recommendation_reason(primary_insight, primary_features)
    interval_reason_parts = [primary_reason]
    for weight, frame, _data, insight, features in analyses:
        if frame == primary_frame:
            continue
        label = _INTERVAL_LABELS.get(frame, frame)
        frame_score = _safe_number(features.get("score"), lower=0.0)
        interval_reason_parts.append(
            f"{label} 점수 {frame_score:.1f} · {insight.regime}"
        )

    if failures:
        interval_reason_parts.append(f"보조 신호 경고 {len(failures)}건")

    consensus_note = "다중 타임프레임 합의" if len(analyses) > 1 else "단일 타임프레임"
    summary = f"{primary_insight.summary} · {consensus_note} 점수 {score:.1f}"

    if not sources:
        sources.add("unknown")

    payload = MarketRecommendationPayload(
        market=info.market,
        korean_name=info.korean_name,
        english_name=info.english_name,
        base_currency=info.base_currency,
        quote_currency=info.quote_currency,
        score=round(score, 2),
        confidence_pct=_safe_number(confidence, lower=0.0, upper=100.0),
        regime=primary_insight.regime,
        recommended_action=primary_insight.recommended_action,
        last_price=last_price,
        price_change_pct=price_change_pct,
        trend_strength_pct=trend_strength_pct,
        volatility_pct=volatility_pct,
        institutional_sentiment_pct=institutional_pct,
        breakout_probability_pct=breakout_pct,
        surge_probability_pct=surge_pct,
        crash_probability_pct=crash_pct,
        risk_reward_ratio=risk_reward_ratio,
        technical_confluence_score=technical_score,
        technical_confluence_label=str(primary_features.get("technical_confluence_label") or "중립"),
        shock_risk_pct=shock_risk_pct,
        summary=summary,
        reason=" / ".join(interval_reason_parts),
        source="mixed" if len(sources) > 1 else next(iter(sources)),
    )

    error_message = ""
    if failures:
        payload.summary += " · 보조 신호 일부 경고"
        error_message = f"{info.market}: 보조 신호 {len(failures)}건 경고"

    return (error_message, payload, payload.source)


def _empty_market_recommendations(
    *,
    base: str,
    interval: str,
    limit: int,
    error: str,
) -> MarketRecommendationsResponse:
    """Return a deterministic empty payload when analysis fails.

    FastAPI previously surfaced ``500 Internal Server Error`` responses when the
    background scoring routine raised an unexpected exception.  Returning a
    structured fallback keeps the dashboard responsive and surfaces the error
    message to the UI so operators immediately understand what went wrong.
    """

    safe_limit = max(1, min(limit, 10))
    return MarketRecommendationsResponse(
        generated_at=_utcnow(),
        interval=interval,
        base_currency=base,
        limit=safe_limit,
        analysed_markets=0,
        analysis_duration_ms=0.0,
        analysis_source="synthetic",
        recommendations=[],
        errors=[error],
    )


def _fallback_recommendation_payloads(
    *,
    base_currency: str,
    interval: str,
    limit: int,
    exclude: Iterable[str],
) -> List[MarketRecommendationPayload]:
    """Return AI-backed synthetic recommendations when live analysis fails."""

    base_currency = (base_currency or "KRW").upper()
    interval = interval or "minute60"
    safe_limit = max(1, limit)
    exclude_set = {code.upper() for code in exclude}
    only_krw = True if base_currency == "KRW" else base_currency != "ALL"

    fallback_infos = get_fallback_market_infos(only_krw=only_krw)
    if not fallback_infos:
        return []

    rotation_seed = int(_utcnow().timestamp() // 900)
    sorted_infos = sorted(
        fallback_infos,
        key=lambda info: (
            zlib.crc32(f"{info.market}:{interval}:{rotation_seed}".encode("utf-8"))
            & 0xFFFFFFFF,
            info.market,
        ),
    )

    payloads: List[MarketRecommendationPayload] = []

    for info in sorted_infos:
        market_code = info.market.upper()
        if market_code in exclude_set:
            continue
        if base_currency != "ALL" and info.base_currency.upper() != base_currency:
            continue

        seed = zlib.crc32(f"{market_code}:{interval}".encode("utf-8")) & 0xFFFFFFFF
        synthetic_candles = trading.generate_synthetic_prices(days=200, seed=seed)

        try:
            insight = ai.analyse_market(
                synthetic_candles,
                market=market_code,
                interval=interval,
            )
            features = _compute_recommendation_features(insight)
            score_value = _safe_number(features.get("score"), lower=0.0)
            confidence = _safe_number(
                insight.confidence_pct, lower=0.0, upper=100.0
            )
            metrics = insight.metrics
            reason = _format_recommendation_reason(insight, features)
            surge_pct = _safe_number(
                features.get("surge_probability_pct"), lower=0.0, upper=100.0
            )
            crash_pct = _safe_number(
                features.get("crash_probability_pct"), lower=0.0, upper=100.0
            )
            risk_reward_ratio = _safe_number(
                features.get("risk_reward_ratio"), lower=0.0
            )
            technical_score = _safe_number(
                features.get("technical_confluence_score"), lower=0.0, upper=100.0
            )
            technical_label = str(features.get("technical_confluence_label") or "중립")
            shock_risk_pct = _safe_number(
                features.get("shock_risk_pct"), lower=0.0, upper=100.0
            )
            payloads.append(
                MarketRecommendationPayload(
                    market=market_code,
                    korean_name=info.korean_name,
                    english_name=info.english_name,
                    base_currency=info.base_currency,
                    quote_currency=info.quote_currency,
                    score=round(score_value, 2),
                    confidence_pct=confidence,
                    regime=insight.regime,
                    recommended_action=insight.recommended_action,
                    last_price=_safe_number(synthetic_candles[-1].close, lower=0.0),
                    price_change_pct=_safe_number(metrics.price_change_pct),
                    trend_strength_pct=_safe_number(metrics.trend_strength * 100),
                    volatility_pct=_safe_number(metrics.volatility_pct, lower=0.0),
                    institutional_sentiment_pct=_safe_number(
                        metrics.institutional_sentiment * 100,
                        lower=0.0,
                        upper=100.0,
                    ),
                    breakout_probability_pct=_safe_number(
                        metrics.breakout_probability * 100,
                        lower=0.0,
                        upper=100.0,
                    ),
                    surge_probability_pct=surge_pct,
                    crash_probability_pct=crash_pct,
                    risk_reward_ratio=risk_reward_ratio,
                    technical_confluence_score=technical_score,
                    technical_confluence_label=technical_label,
                    shock_risk_pct=shock_risk_pct,
                    summary=f"AI 폴백 분석 · {insight.summary}",
                    reason=reason,
                    source="synthetic",
                )
            )
        except Exception:
            payloads.append(
                MarketRecommendationPayload(
                    market=market_code,
                    korean_name=info.korean_name,
                    english_name=info.english_name,
                    base_currency=info.base_currency,
                    quote_currency=info.quote_currency,
                    score=0.0,
                    confidence_pct=0.0,
                    regime="데이터 대기",
                    recommended_action="관망",
                    last_price=0.0,
                    price_change_pct=0.0,
                    trend_strength_pct=0.0,
                    volatility_pct=0.0,
                    institutional_sentiment_pct=0.0,
                    breakout_probability_pct=0.0,
                    surge_probability_pct=0.0,
                    crash_probability_pct=0.0,
                    risk_reward_ratio=0.0,
                    technical_confluence_score=0.0,
                    technical_confluence_label="중립",
                    shock_risk_pct=0.0,
                    summary="AI 폴백 분석을 준비하는 중입니다.",
                    reason="실시간 분석 결과가 부족하여 내장 후보로 보강했습니다.",
                    source="synthetic",
                )
            )

        if len(payloads) >= safe_limit:
            break

    return payloads


def _normalise_recent_markets(recent_markets: Sequence[str] | None) -> Tuple[str, ...]:
    if not recent_markets:
        return tuple()

    normalised: list[str] = []
    seen: set[str] = set()
    for market in reversed(list(recent_markets)):
        if not market:
            continue
        upper = str(market).upper()
        if upper in seen:
            continue
        normalised.append(upper)
        seen.add(upper)
        if len(normalised) >= _RECENT_HISTORY_LIMIT:
            break
    return tuple(normalised)


def _rank_recommendations(
    payloads: Iterable[MarketRecommendationPayload],
    recent_markets: Sequence[str] | None,
) -> List[MarketRecommendationPayload]:
    ordered_history = _normalise_recent_markets(recent_markets)
    history_map = {code: index for index, code in enumerate(ordered_history)}

    ranked: list[Tuple[int, float, int, MarketRecommendationPayload]] = []
    seen: set[str] = set()
    for index, payload in enumerate(payloads):
        market_code = payload.market.upper()
        if market_code in seen:
            continue
        seen.add(market_code)

        adjusted_score = float(payload.score)
        adjusted_score += float(payload.confidence_pct) * _RECENT_HISTORY_CONFIDENCE_WEIGHT

        if market_code in history_map:
            depth = history_map[market_code]
            adjusted_score -= _RECENT_HISTORY_PENALTY / (depth + 1.0)
        else:
            adjusted_score += _RECENT_HISTORY_FRESH_BONUS

        source_value = getattr(payload, "source", "upbit")
        if source_value == "synthetic":
            adjusted_score -= _SYNTHETIC_SOURCE_PENALTY
        elif source_value in {"upbit", "upbit_alt"}:
            adjusted_score += _REAL_SOURCE_BONUS

        priority = 0 if source_value == "synthetic" else 1
        ranked.append((priority, adjusted_score, -index, payload))

    ranked.sort(reverse=True)
    return [item[3] for item in ranked]


def _compute_market_recommendations(
    *,
    base: str,
    interval: str,
    limit: int,
    max_markets: int,
    include_warnings: bool,
    recent_markets: Sequence[str] | None = None,
) -> MarketRecommendationsResponse:
    start = perf_counter()
    base_currency = base.upper() or "KRW"
    interval = interval or "minute60"
    limit = max(1, min(limit, 10))
    max_markets = max(limit, min(max_markets, 120))

    fingerprint = _normalise_recent_markets(recent_markets)
    cache_key = (
        base_currency,
        interval,
        limit,
        max_markets,
        bool(include_warnings),
        fingerprint[:_RECENT_HISTORY_CACHE_FINGERPRINT],
    )
    cached = _RECOMMENDATION_CACHE.get(cache_key)
    now_ts = time.time()
    if cached and now_ts - cached[0] < _RECOMMENDATION_CACHE_TTL:
        return cached[1]

    errors: list[str] = []
    try:
        listing = fetch_upbit_markets(only_krw=base_currency != "ALL")
        base_markets = [
            item
            for item in listing.markets
            if not item.trading_suspended
            and (include_warnings or item.market_warning in {"", "NONE"})
            and (base_currency == "ALL" or item.base_currency.upper() == base_currency)
        ]
        listing_source = listing.source
    except Exception as exc:  # pragma: no cover - defensive guard
        base_markets = get_fallback_market_infos(only_krw=base_currency != "ALL")
        listing_source = "fallback"
        errors.append(f"실시간 마켓 목록 조회 실패: {exc}")

    selection: TopMarketSelection
    if len(base_markets) <= max_markets:
        selection = TopMarketSelection(markets=[], source=listing_source, errors=[])
        targets_source = listing_source
        targets = base_markets[:max_markets]
    else:
        try:
            selection = fetch_upbit_top_markets(
                base_currency=base_currency,
                limit=max_markets,
                include_warnings=include_warnings,
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            selection = TopMarketSelection(
                markets=[],
                source="fallback-volume",
                errors=[f"거래대금 순위 조회 실패: {exc}"]
            )

        targets_source = selection.source if selection.markets else listing_source
        targets = selection.markets[:max_markets] if selection.markets else base_markets[:max_markets]
    analysed = 0
    errors.extend(selection.errors)
    scores: list[MarketRecommendationPayload] = []
    data_sources: set[str] = set()
    if targets_source != "upbit":
        data_sources.add("synthetic" if targets_source == "fallback-volume" else targets_source)

    if targets:
        max_workers = min(8, len(targets)) or 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_evaluate_market_candidate, info, interval): info.market
                for info in targets
            }
            for future in as_completed(future_map):
                try:
                    error, payload, source = future.result()
                except Exception as exc:  # pragma: no cover - unexpected failure path
                    errors.append(f"{future_map[future]}: {exc}")
                    continue

                if error:
                    errors.append(error)
                    if source:
                        data_sources.add(source)
                    continue

                if payload is None:
                    continue

                analysed += 1
                scores.append(payload)
                if source:
                    data_sources.add(source)

    scores.sort(key=lambda item: item.score, reverse=True)

    seen_markets: set[str] = set()
    deduped_scores: List[MarketRecommendationPayload] = []
    duplicate_trimmed = False
    for payload in scores:
        market_code = payload.market.upper()
        if market_code in seen_markets:
            duplicate_trimmed = True
            continue
        seen_markets.add(market_code)
        deduped_scores.append(payload)

    if duplicate_trimmed:
        errors.append("동일 종목 추천이 중복되어 상위 결과를 정리했습니다.")

    ranked_candidates = _rank_recommendations(deduped_scores, fingerprint)
    top_recommendations: List[MarketRecommendationPayload] = ranked_candidates[:limit]

    combined_scores = list(deduped_scores)
    if len(top_recommendations) < limit:
        fallback_needed = limit - len(top_recommendations)
        fallback_candidates = _fallback_recommendation_payloads(
            base_currency=base_currency,
            interval=interval,
            limit=max(fallback_needed * 2, fallback_needed),
            exclude=seen_markets,
        )
        if fallback_candidates:
            appended = False
            for candidate in fallback_candidates:
                market_code = candidate.market.upper()
                if market_code in seen_markets:
                    continue
                combined_scores.append(candidate)
                seen_markets.add(market_code)
                appended = True
            if appended:
                data_sources.add("synthetic")
                errors.append("실시간 추천이 부족해 내장 후보를 보강했습니다.")

    if len(combined_scores) < limit:
        top_recommendations = combined_scores[:]
    else:
        ranked_candidates = _rank_recommendations(combined_scores, fingerprint)
        top_recommendations = ranked_candidates[:limit]

    if len(top_recommendations) < limit:
        errors.append("추천 후보가 충분하지 않아 일부 슬롯이 비어 있습니다.")

    source_alias = {"fallback-volume": "synthetic"}

    if not data_sources:
        analysis_source = "upbit"
    elif len(data_sources) == 1:
        source_value = next(iter(data_sources))
        analysis_source = source_alias.get(source_value, source_value)
    else:
        analysis_source = "mixed"

    raw_duration = (perf_counter() - start) * 1000
    duration_ms = round(_safe_number(raw_duration, lower=0.0), 2)

    response = MarketRecommendationsResponse(
        generated_at=_utcnow(),
        interval=interval,
        base_currency=base_currency,
        limit=limit,
        analysed_markets=analysed,
        analysis_duration_ms=duration_ms,
        analysis_source=analysis_source,
        recommendations=top_recommendations,
        errors=errors,
    )
    _RECOMMENDATION_CACHE[cache_key] = (now_ts, response)
    return response


def _notify_recommendation_health(payload: MarketRecommendationsResponse) -> None:
    """Send Synology Chat alerts when recommendation quality degrades."""

    reasons: list[str] = []
    if not payload.recommendations:
        reasons.append("추천 결과 없음")
    elif len(payload.recommendations) < payload.limit:
        reasons.append(
            f"추천 부족 {len(payload.recommendations)}/{payload.limit}"
        )

    if payload.errors:
        reasons.extend(payload.errors[:2])

    degraded_sources = {
        "synthetic": "합성 데이터 사용",
        "mixed": "혼합 데이터 사용",
        "upbit_alt": "대체 업비트 소스",
        "upbit_stale": "업비트 지연 데이터",
    }
    if payload.analysis_source in degraded_sources:
        reasons.append(degraded_sources[payload.analysis_source])

    if payload.analysed_markets <= 0:
        reasons.append("분석 대상 확보 실패")

    if reasons:
        message = (
            f"[AI 추천 경고] {payload.base_currency.upper()}/{payload.interval} "
            + " · ".join(reasons)
        )
    else:
        message = (
            f"[AI 추천] {payload.base_currency.upper()}/{payload.interval} 상태 정상화"
        )

    notifications.notify_synology_chat_on_change(
        "market-recommendations-alert", message
    )


def _autopilot_recommendations(
    base: str,
    interval: str,
    limit: int,
    max_markets: int,
    include_warnings: bool,
) -> MarketRecommendationsResponse:
    try:
        recent_markets = _auto_trader.status().recent_markets
    except Exception:  # pragma: no cover - defensive guard
        recent_markets = []
    try:
        return _compute_market_recommendations(
            base=base,
            interval=interval,
            limit=limit,
            max_markets=max_markets,
            include_warnings=include_warnings,
            recent_markets=recent_markets,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        error_message = f"내부 추천 엔진 오류: {exc}"
        return _empty_market_recommendations(
            base=base,
            interval=interval,
            limit=limit,
            error=error_message,
        )


_auto_trader.set_recommendation_scanner(_autopilot_recommendations)


@app.get("/market/recommendations", response_model=MarketRecommendationsResponse)
def get_market_recommendations(
    base: str = "KRW",
    interval: str = "minute60",
    limit: int = 5,
    max_markets: int = 30,
    include_warnings: bool = False,
) -> MarketRecommendationsResponse:
    try:
        response = _compute_market_recommendations(
            base=base,
            interval=interval,
            limit=limit,
            max_markets=max_markets,
            include_warnings=include_warnings,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        error_message = f"내부 추천 엔진 오류: {exc}"
        response = _empty_market_recommendations(
            base=base,
            interval=interval,
            limit=limit,
            error=error_message,
        )

    _push_chat_summary("market-recommendations", _summarise_recommendations_for_chat(response))
    _notify_recommendation_health(response)
    return response


@app.get("/market/upbit/candles", response_model=MarketCandlesResponse)
def get_upbit_candles(
    market: str = "KRW-BTC", interval: str = "minute1", count: int = 120
) -> MarketCandlesResponse:
    try:
        data = fetch_upbit_candles(market=market, interval=interval, count=count)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    candles = [
        CandlePayload(
            timestamp=item.timestamp,
            open=item.open,
            high=item.high,
            low=item.low,
            close=item.close,
            volume=item.volume,
        )
        for item in data.candles
    ]

    network_state = get_upbit_network_state()
    return MarketCandlesResponse(
        market=market.upper(),
        interval=interval,
        source=data.source,
        candles=candles,
        status=data.status or network_state["status"],
        message=data.message or network_state["message"],
        detail=data.detail or network_state.get("detail"),
        checked_at=network_state.get("checked_at"),
        stale=data.stale,
        fetched_at=data.fetched_at,
    )


@app.get("/market/upbit/insights", response_model=MarketInsightsResponse)
def get_market_insights(
    market: str = "KRW-BTC", interval: str = "minute1", count: int = 160
) -> MarketInsightsResponse:
    try:
        data = fetch_upbit_candles(market=market, interval=interval, count=count)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    insights = build_market_insights(
        data.candles,
        market=market.upper(),
        interval=interval,
    )

    latest_close = _safe_number(insights.get("latest_close"), lower=0.0)
    ema_fast = _safe_number(insights.get("ema_fast"))
    ema_slow = _safe_number(insights.get("ema_slow"))
    rsi = _safe_number(insights.get("rsi"), lower=0.0, upper=100.0)
    macd = _safe_number(insights.get("macd"))
    macd_signal = _safe_number(insights.get("macd_signal"))
    macd_histogram = _safe_number(insights.get("macd_histogram"))
    volatility_pct = _safe_number(insights.get("volatility_pct"), lower=0.0)
    trend_strength = _safe_number(insights.get("trend_strength"))
    confidence_pct = _safe_number(insights.get("confidence_pct"), lower=0.0, upper=100.0)

    return MarketInsightsResponse(
        market=str(insights.get("market", market.upper())),
        interval=str(insights.get("interval", interval)),
        source=data.source,
        latest_close=latest_close,
        latest_timestamp=insights.get("latest_timestamp"),
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ema_signal=str(insights.get("ema_signal", "중립")),
        rsi=rsi,
        macd=macd,
        macd_signal=macd_signal,
        macd_histogram=macd_histogram,
        volatility_pct=volatility_pct,
        trend_strength=trend_strength,
        regime=str(insights.get("regime", "중립")),
        recommended_action=str(insights.get("recommended_action", "")),
        confidence_pct=confidence_pct,
        insight_summary=str(insights.get("insight_summary", "")),
        stale=data.stale,
        fetched_at=data.fetched_at,
    )


@app.get("/ai/market/intelligence", response_model=MarketAIResponse)
def get_market_intelligence(
    market: str = "KRW-BTC", interval: str = "minute60", count: int = 180
) -> MarketAIResponse:
    market = market.upper()
    try:
        data = fetch_upbit_candles(market=market, interval=interval, count=count)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    headlines = fetch_authoritative_news(limit=5)
    try:
        insight = ai.analyse_market(
            data.candles,
            market=market,
            interval=interval,
            news=headlines,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    response = MarketAIResponse(
        market=insight.market,
        interval=insight.interval,
        regime=insight.regime,
        recommended_action=insight.recommended_action,
        confidence_pct=_safe_number(insight.confidence_pct, lower=0.0, upper=100.0),
        summary=insight.summary,
        signals=insight.signals,
        metrics=_sanitize_metrics_payload(insight.metrics),
        risk=_sanitize_risk_payload(insight.risk),
        technical_confluence=_sanitize_confluence_payload(insight.technical_confluence),
        generated_at=insight.generated_at,
        news=_news_items(insight.news),
        timeframe_consensus=TimeframeConsensusPayload(**insight.timeframe_consensus.__dict__),
        institutional_confidence_pct=_safe_number(
            insight.institutional_confidence_pct, lower=0.0, upper=100.0
        ),
        institutional_commentary=insight.institutional_commentary,
        institutional_alert_level=_safe_number(
            getattr(insight, "institutional_alert_level", 0.0), lower=0.0, upper=100.0
        ),
        institutional_alerts=list(getattr(insight, "institutional_alerts", []) or []),
        credible_sources=list(getattr(insight, "credible_sources", []) or []),
    )

    _push_chat_summary(
        "market-intelligence",
        _summarise_market_intelligence_for_chat(response),
    )
    return response


@app.get("/ai/risk/playbook", response_model=LossRecoveryPlaybookResponse)
def get_loss_recovery_playbook() -> LossRecoveryPlaybookResponse:
    state = _auto_trader.status()
    try:
        headlines = fetch_authoritative_news(limit=5)
    except MarketDataError:
        headlines = []

    plan = ai.build_loss_recovery_playbook(
        orders=list(_paper_broker.orders),
        positions=list(_paper_broker.positions.values()),
        executions=state.executions,
        last_insight=state.last_insight,
        news_items=headlines,
    )

    response = _serialize_loss_playbook(plan)
    summary = (
        f"손실 복구 플레이북 · 실현 {response.realized_loss_krw:,.0f} KRW / 미실현 {response.unrealized_loss_krw:,.0f} KRW"
        if (response.realized_loss_krw or response.unrealized_loss_krw)
        else "손실 복구 플레이북 · 현재 손실 없음"
    )
    _push_chat_summary("loss-recovery", summary)
    return response


@app.get("/market/news", response_model=NewsResponse)
def get_news(limit: int = 8) -> NewsResponse:
    items = [
        NewsItem(**entry)
        for entry in fetch_authoritative_news(limit=limit)
    ]
    return NewsResponse(generated_at=_utcnow(), items=items)


@app.post("/diagnostics/upbit/self-heal", response_model=UpbitRecoveryResponse)
def trigger_upbit_self_heal(reason: str = "manual") -> UpbitRecoveryResponse:
    """Manually trigger the Upbit self-heal workflow."""

    result = attempt_upbit_self_heal(reason=reason)
    return UpbitRecoveryResponse(**result)


@app.get("/diagnostics/upbit/recovery-log", response_model=UpbitRecoveryLogResponse)
def get_upbit_recovery_log_entries(limit: int = 20) -> UpbitRecoveryLogResponse:
    """Return recent Upbit recovery attempts for dashboard display."""

    entries = get_upbit_recovery_log(limit=limit)
    return UpbitRecoveryLogResponse(entries=entries)


@app.get("/diagnostics/full", response_model=DiagnosticsResponse)
def diagnostics_full() -> DiagnosticsResponse:
    return _diagnostics_summary()


@app.get("/diagnostics/logs", response_model=LogTailResponse)
def diagnostics_logs(limit: int = 200) -> LogTailResponse:
    """Expose recent structured log entries for troubleshooting."""

    try:
        limit = int(limit)
    except (TypeError, ValueError):  # pragma: no cover - defensive guard
        limit = 200
    limit = max(1, min(limit, 1000))

    entries: List[LogEntryPayload] = []
    for item in read_log_tail(limit):
        timestamp_value = item.get("timestamp")
        timestamp_dt: Optional[datetime] = None
        if isinstance(timestamp_value, str):
            try:
                timestamp_dt = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
            except ValueError:
                timestamp_dt = None
        data = {k: v for k, v in item.items() if k not in {"timestamp", "level", "event"}}
        entries.append(
            LogEntryPayload(
                timestamp=timestamp_dt,
                level=str(item.get("level", "INFO")),
                event=str(item.get("event", "")),
                data=data,
                raw=json.dumps(item, ensure_ascii=False, default=str),
            )
        )

    log_path = get_log_file_path()
    return LogTailResponse(
        generated_at=_utcnow(),
        entries=entries,
        log_path=str(log_path) if log_path else None,
    )


def _refresh_paper_market(market: str, *, interval: str = "minute1") -> str:
    market = market.upper()
    source = "manual"
    candles = []

    try:
        market_data = fetch_upbit_candles(market=market, interval=interval, count=1)
        candles = market_data.candles
        source = market_data.source
    except MarketDataError:
        candles = trading.generate_synthetic_prices(days=60)
        source = "synthetic"

    if not candles:
        return "manual"

    resolved = "manual"
    if source in {"upbit", "upbit_alt", "upbit_stale"}:
        last_price = candles[-1].close
        try:
            _paper_broker.mark_price(market=market, price=last_price)
        except ExecutionError:
            return "manual"
        resolved = source
    else:
        resolved = "synthetic"

    _paper_last_price_source[market] = resolved
    return resolved


def _paper_heartbeat(price_source: str, last_updated: datetime) -> tuple[str, str]:
    """Return dashboard-friendly heartbeat state and reason."""

    now = _utcnow()
    delta_seconds = max(0.0, (now - last_updated).total_seconds())

    if price_source in {"upbit", "upbit_alt"}:
        if delta_seconds <= 90:
            return ("online", "live")
        if delta_seconds <= 300:
            return ("warning", "delayed")
        return ("offline", "stale")

    if price_source == "upbit_stale":
        if delta_seconds <= 300:
            return ("warning", "stale")
        return ("offline", "stale")

    if price_source == "synthetic":
        if delta_seconds <= 600:
            return ("warning", "synthetic")
        return ("offline", "synthetic")

    if delta_seconds <= 600:
        return ("warning", "manual")
    return ("offline", "manual")


def _paper_status_response(*, market: Optional[str] = None, interval: str = "minute1") -> PaperStatusResponse:
    global _paper_market_preference, _paper_interval_preference

    explicit_refresh = market is not None
    target_market = (market or _paper_market_preference or "KRW-BTC").upper()
    target_interval = interval or _paper_interval_preference or "minute1"
    _paper_market_preference = target_market
    _paper_interval_preference = target_interval

    price_source = _paper_last_price_source.get(target_market, "manual") if target_market else "manual"
    if explicit_refresh and target_market:
        source = _refresh_paper_market(market=target_market, interval=target_interval)
        if source in {"upbit", "upbit_alt", "upbit_stale", "synthetic"}:
            price_source = source

    snapshot = _paper_broker.snapshot()
    balance = _serialize_balance(snapshot)

    if target_market:
        has_price = _paper_broker.get_last_price(target_market) is not None
        if not has_price:
            initial_source = _refresh_paper_market(market=target_market, interval=target_interval)
            if initial_source in {"upbit", "upbit_alt", "upbit_stale", "synthetic"}:
                price_source = initial_source
            snapshot = _paper_broker.snapshot()
            balance = _serialize_balance(snapshot)

        age_seconds = max(0.0, (_utcnow() - balance.last_updated).total_seconds())
        if age_seconds > 120:
            refreshed_source = _refresh_paper_market(market=target_market, interval=target_interval)
            if refreshed_source in {"upbit", "upbit_alt", "upbit_stale", "synthetic"}:
                price_source = refreshed_source
            snapshot = _paper_broker.snapshot()
            balance = _serialize_balance(snapshot)

    payload = balance.dict()
    heartbeat_state, heartbeat_reason = _paper_heartbeat(price_source, balance.last_updated)
    payload.update(
        {
            "price_source": price_source,
            "market": target_market,
            "interval": target_interval,
            "heartbeat_state": heartbeat_state,
            "heartbeat_reason": heartbeat_reason,
            "initial_cash": balance.initial_cash,
        }
    )
    if target_market:
        _paper_last_price_source[target_market] = price_source
    return PaperStatusResponse(**payload)


def _build_market_groups(markets: list[MarketInfoPayload]) -> list[MarketGroupPayload]:
    if not markets:
        return []

    groups: list[MarketGroupPayload] = []
    base_map: dict[str, list[str]] = {}
    for item in markets:
        base = item.base_currency.upper()
        base_map.setdefault(base, []).append(item.market)

    def add_group(key: str, label: str, description: str, codes: Iterable[str]) -> None:
        unique_codes = sorted({code.upper() for code in codes if code})
        if unique_codes:
            groups.append(
                MarketGroupPayload(
                    key=key,
                    label=label,
                    description=description,
                    markets=unique_codes,
                )
            )

    majors_reference = [
        "KRW-BTC",
        "KRW-ETH",
        "KRW-XRP",
        "KRW-SOL",
        "KRW-ADA",
        "KRW-MATIC",
        "KRW-DOGE",
        "KRW-LINK",
        "KRW-BCH",
    ]
    available_markets = {item.market for item in markets}
    add_group(
        "majors",
        "대표 코인",
        "업비트에서 가장 많이 거래되는 대표 종목",
        [code for code in majors_reference if code in available_markets],
    )
    add_group("krw", "KRW 마켓", "원화 기준 전체 종목", base_map.get("KRW", []))
    add_group("usdt", "USDT 마켓", "테더 기반 글로벌 페어", base_map.get("USDT", []))
    add_group("btc", "BTC 마켓", "비트코인 기반 페어", base_map.get("BTC", []))

    warning_codes = [item.market for item in markets if item.market_warning != "NONE"]
    add_group("warning", "투자 유의", "투자 유의 종목은 리스크 확인 필요", warning_codes)

    suspended_codes = [item.market for item in markets if item.trading_suspended]
    add_group("suspended", "거래 일시 중지", "점검 또는 유동성 부족으로 제한된 종목", suspended_codes)

    return groups


def _run_check(name: str, func) -> DiagnosticCheckPayload:
    start = perf_counter()
    status = "ok"
    detail = "정상"
    try:
        result = func()
        if isinstance(result, tuple):
            detail = str(result[0])
            status = result[1] or status
        elif isinstance(result, str):
            detail = result
        elif result is not None:
            detail = str(result)
    except Exception as exc:  # pragma: no cover - diagnostics should surface errors
        status = "error"
        detail = str(exc)
    latency = round((perf_counter() - start) * 1000, 2)
    return DiagnosticCheckPayload(name=name, status=status, detail=detail, latency_ms=latency)


def _build_upbit_guidance(check: DiagnosticCheckPayload) -> List[ConnectivitySuggestion]:
    """Provide actionable suggestions when the Upbit link is degraded."""

    if check.status == "ok":
        return []

    return [
        ConnectivitySuggestion(
            title="네트워크 연결 안정성 점검",
            detail="유선 LAN 등 안정적인 인터넷 환경을 사용하고 공유기·모뎀 재부팅을 통해 연결을 새로 고침하세요.",
        ),
        ConnectivitySuggestion(
            title="PC 절전 모드 비활성화",
            detail="윈도우 전원 관리에서 절전 모드나 화면 보호기가 자동 실행되지 않도록 설정해 통신이 유지되도록 하세요.",
        ),
        ConnectivitySuggestion(
            title="VPN 또는 프록시 사용 중지",
            detail="업비트는 VPN·프록시를 차단할 수 있으므로 네트워크 우회 설정을 해제한 뒤 다시 연결을 시도하세요.",
        ),
        ConnectivitySuggestion(
            title="API 키 권한 확인",
            detail="업비트 개발자 센터에서 주문·조회 권한이 모두 활성화된 최신 API 키를 사용 중인지 재확인하세요.",
        ),
        ConnectivitySuggestion(
            title="요청 제한 준수",
            detail="초당 30회·분당 900회 제한을 초과하지 않도록 요청 빈도를 조정하고 AUTOPILOT_MAX_MARKETS_PER_CYCLE 값을 필요 시 낮춰보세요.",
        ),
        ConnectivitySuggestion(
            title="업비트 공지 확인",
            detail="업비트 공지사항에서 서버 점검·장애 안내가 있는지 확인한 뒤 정상화 시 재연결하세요.",
        ),
        ConnectivitySuggestion(
            title="프로그램 최신 버전 유지",
            detail="대시보드와 백엔드를 최신 커밋으로 배포하고 `pytest`로 무결성을 확인한 뒤 운용하세요.",
        ),
    ]


def _diagnostics_summary() -> DiagnosticsResponse:
    checks: list[DiagnosticCheckPayload] = []

    def _check_upbit():
        data = fetch_upbit_candles(market="KRW-BTC", interval="minute60", count=80)
        if data.source == "synthetic":
            return "업비트 응답 없음 - 시뮬레이션 데이터 사용", "warning"
        return f"{len(data.candles)} 캔들 확보", "ok"

    def _check_paper():
        snapshot = _paper_broker.snapshot()
        return f"현금 {snapshot.cash:,.0f} KRW · 포지션 {len(snapshot.positions)}건", "ok"

    def _check_autopilot():
        state = _auto_trader.status()
        if state.last_error:
            level = "warning" if not state.running else "error"
            return f"최근 오류: {state.last_error}", level
        if state.running:
            return "자동매매 루프 실행 중", "ok"
        return "오토파일럿 대기 상태", "ok"

    def _check_strategy():
        candles = trading.generate_synthetic_prices(days=120, seed=42)
        report = trading.run_ema_strategy(candles)
        return f"시뮬레이션 수익률 {report.total_return_pct:.2f}%", "ok"

    def _check_keys():
        has_access = bool(os.getenv("UPBIT_ACCESS_KEY"))
        has_secret = bool(os.getenv("UPBIT_SECRET_KEY"))
        if has_access and has_secret:
            return "실거래 키 감지", "ok"
        return "실거래 키 미설정 - 페이퍼 모드", "warning"

    upbit_check = _run_check("업비트 연결", _check_upbit)
    checks.append(upbit_check)
    checks.append(_run_check("페이퍼 브로커", _check_paper))
    checks.append(_run_check("오토파일럿", _check_autopilot))
    checks.append(_run_check("전략 엔진", _check_strategy))
    checks.append(_run_check("실거래 키", _check_keys))

    return DiagnosticsResponse(
        generated_at=_utcnow(),
        checks=checks,
        upbit_guidance=_build_upbit_guidance(upbit_check),
    )


_SELF_CHECK_REFRESH_SECONDS = 180


def _seconds_since(value: Optional[datetime]) -> float:
    if value is None:
        return float("inf")
    return max(0.0, (_utcnow() - _ensure_utc(value)).total_seconds())


def _seconds_until(value: Optional[datetime]) -> float:
    if value is None:
        return 0.0
    return max(0.0, (_ensure_utc(value) - _utcnow()).total_seconds())


def _format_duration(seconds: float) -> str:
    seconds = int(round(max(0.0, seconds)))
    if seconds < 60:
        return f"{seconds}초"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}분" + (f" {remainder}초" if remainder else "")
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}시간" + (f" {minutes}분" if minutes else "")
    days, hours = divmod(hours, 24)
    return f"{days}일" + (f" {hours}시간" if hours else "")


def _compose_self_check_summary(
    overall: str,
    diagnostics: DiagnosticsResponse,
    autopilot_state: AutoTraderState,
    paper_status: PaperStatusResponse,
) -> str:
    autopilot_fragment = "오토파일럿 대기 중"
    if autopilot_state.running:
        target = autopilot_state.last_plan.market if autopilot_state.last_plan else "자동 선택"
        autopilot_fragment = f"오토파일럿 가동 중 ({target})"
    elif autopilot_state.last_error:
        autopilot_fragment = "오토파일럿 오류 대기"

    heartbeat_fragment = "페이퍼 계좌 비활성"
    if paper_status.heartbeat_state == "online":
        heartbeat_fragment = "페이퍼 하트비트 정상"
    elif paper_status.heartbeat_state == "warning":
        heartbeat_fragment = "페이퍼 하트비트 경고"
    else:
        heartbeat_fragment = "페이퍼 하트비트 중단"

    prefix = {
        "critical": "⚠️ 심각: 핵심 시스템에서 오류가 감지되었습니다.",
        "warning": "⚠️ 주의: 일부 구성 요소에서 경고가 감지되었습니다.",
        "nominal": "✅ 정상: 주요 구성 요소가 안정적으로 동작 중입니다.",
    }[overall]

    degraded_checks = [check.name for check in diagnostics.checks if check.status != "ok"]
    if degraded_checks:
        degraded_fragment = " · ".join(degraded_checks[:3])
        if len(degraded_checks) > 3:
            degraded_fragment += " 외"
        detail_fragment = f"진단 경고: {degraded_fragment}"
    else:
        detail_fragment = "진단 경고 없음"

    return f"{prefix} ({autopilot_fragment} · {heartbeat_fragment} · {detail_fragment})"


def _compile_self_check_issues(
    *,
    diagnostics: DiagnosticsResponse,
    autopilot_state: AutoTraderState,
    paper_status: PaperStatusResponse,
    chat_status: dict,
) -> List[AISelfCheckIssue]:
    issues: List[AISelfCheckIssue] = []

    severity_map = {"warning": "warning", "error": "critical"}
    for check in diagnostics.checks:
        if check.status == "ok":
            continue
        severity = severity_map.get(check.status, "warning")
        issues.append(
            AISelfCheckIssue(
                component=f"시스템·{check.name}",
                severity=severity,
                summary=f"{check.name} 상태 {check.status.upper()}",
                detail=check.detail,
                suggested_action="대시보드 진단 패널을 확인하고 조치하세요.",
            )
        )

    autopilot_summary = "오토파일럿 대기 중"
    autopilot_detail: Optional[str] = None
    autopilot_action: Optional[str] = None
    autopilot_severity = "info"

    if autopilot_state.running:
        target = autopilot_state.last_plan.market if autopilot_state.last_plan else "자동 선택"
        autopilot_summary = f"오토파일럿 가동 중 ({target})"
        if autopilot_state.last_error:
            autopilot_severity = "critical"
            autopilot_detail = autopilot_state.last_error
            autopilot_action = "오토파일럿 로그를 확인하고 필요 시 재시작하세요."
        else:
            last_exec_at = (
                autopilot_state.last_execution.executed_at
                if autopilot_state.last_execution is not None
                else None
            )
            if last_exec_at is not None:
                age = _seconds_since(last_exec_at)
                poll_interval = autopilot_state.config.poll_interval if autopilot_state.config else 0.0
                allowed = max(180.0, poll_interval * 2 if poll_interval else 0.0)
                if allowed and age > allowed:
                    autopilot_severity = "warning"
                    autopilot_detail = f"최근 체결 {_format_duration(age)} 전"
                    next_due = _seconds_until(autopilot_state.next_cycle_due_at)
                    if next_due:
                        autopilot_detail += f" · 다음 주기 {_format_duration(next_due)} 후"
                    autopilot_action = "업비트 연결과 추천 결과를 확인하세요."
    else:
        autopilot_summary = "오토파일럿 대기 중"
        if autopilot_state.last_error:
            autopilot_severity = "warning"
            autopilot_detail = autopilot_state.last_error
            autopilot_action = "구성이 맞는지 확인하고 다시 시작하세요."

    issues.append(
        AISelfCheckIssue(
            component="오토파일럿",
            severity=autopilot_severity,
            summary=autopilot_summary,
            detail=autopilot_detail,
            suggested_action=autopilot_action,
        )
    )

    heartbeat_state = paper_status.heartbeat_state
    heartbeat_reason = paper_status.heartbeat_reason
    heartbeat_age = _seconds_since(paper_status.last_updated)
    heartbeat_summary = {
        "online": "페이퍼 계좌 실시간 연동", 
        "warning": "페이퍼 계좌 지연", 
        "offline": "페이퍼 계좌 중단",
    }.get(heartbeat_state, "페이퍼 계좌 상태 알 수 없음")
    heartbeat_severity = {
        "online": "info",
        "warning": "warning",
        "offline": "critical",
    }.get(heartbeat_state, "warning")
    heartbeat_detail = f"마지막 갱신 {_format_duration(heartbeat_age)} 전 · 소스 {heartbeat_reason}"
    heartbeat_action = None
    if heartbeat_severity != "info":
        heartbeat_action = "업비트 연결 또는 수동 시세 반영을 확인하세요."

    issues.append(
        AISelfCheckIssue(
            component="페이퍼 트레이딩",
            severity=heartbeat_severity,
            summary=heartbeat_summary,
            detail=heartbeat_detail,
            suggested_action=heartbeat_action,
        )
    )

    net_state = get_upbit_network_state()
    net_status = net_state.get("status", "unknown")
    net_message = str(net_state.get("message", ""))
    net_backoff = float(net_state.get("backoff_seconds_remaining", 0.0) or 0.0)
    if net_status in {"down", "degraded"}:
        severity = "critical" if net_status == "down" else "warning"
        if net_status == "down" and net_backoff <= 60:
            severity = "warning"
        issues.append(
            AISelfCheckIssue(
                component="업비트 네트워크",
                severity=severity,
                summary="업비트 연결 불안정",
                detail=net_message,
                suggested_action="네트워크 상태를 점검하거나 자가 복구를 실행하세요.",
            )
        )

    chat_configured = bool(chat_status.get("configured"))
    last_error = chat_status.get("last_error")
    last_success = chat_status.get("last_success_at")
    if chat_configured:
        if last_error:
            severity = "critical" if not last_success else "warning"
            detail = str(last_error)
            action = "웹훅 URL과 Synology Chat 로그를 확인하세요."
        else:
            severity = "info"
            last_attempt = chat_status.get("last_attempt_at")
            if isinstance(last_attempt, datetime):
                detail = f"최근 전송 {_format_duration(_seconds_since(last_attempt))} 전"
            else:
                detail = "최근 전송 이력 확보됨"
            action = None
        issues.append(
            AISelfCheckIssue(
                component="Synology Chat",
                severity=severity,
                summary="Synology Chat 알림 구성",  # noqa: E501
                detail=detail,
                suggested_action=action,
            )
        )
    else:
        issues.append(
            AISelfCheckIssue(
                component="Synology Chat",
                severity="info",
                summary="Synology Chat 웹훅 미설정",
                detail=".env에 SADO_CHAT_WEBHOOK을 설정하면 자동 알림이 활성화됩니다.",
                suggested_action=".env 파일을 갱신한 뒤 서버를 재시작하세요.",
            )
        )

    if not issues:
        issues.append(
            AISelfCheckIssue(
                component="시스템",
                severity="info",
                summary="모든 진단이 정상입니다.",
                detail="현재 감지된 경고가 없습니다.",
            )
        )

    issues.sort(key=lambda item: {"critical": 0, "warning": 1, "info": 2}[item.severity])
    return issues


def _derive_overall_severity(issues: List[AISelfCheckIssue]) -> str:
    highest = "nominal"
    for issue in issues:
        if issue.severity == "critical":
            return "critical"
        if issue.severity == "warning" and highest != "critical":
            highest = "warning"
    return highest


class _SelfCheckManager:
    def __init__(self, refresh_interval: int = _SELF_CHECK_REFRESH_SECONDS) -> None:
        self._refresh_interval = refresh_interval
        self._lock = threading.RLock()
        self._cached: Optional[AISelfCheckResponse] = None

    def current(self, *, force: bool = False) -> AISelfCheckResponse:
        now = _utcnow()
        with self._lock:
            if (
                force
                or self._cached is None
                or (now - self._cached.generated_at).total_seconds() > self._refresh_interval
            ):
                self._cached = self._evaluate(now)
            return self._cached

    def _evaluate(self, timestamp: datetime) -> AISelfCheckResponse:
        diagnostics = _diagnostics_summary()
        autopilot_state = _auto_trader.status()
        paper_status = _paper_status_response()
        chat_status = notifications.get_synology_chat_status()

        issues = _compile_self_check_issues(
            diagnostics=diagnostics,
            autopilot_state=autopilot_state,
            paper_status=paper_status,
            chat_status=chat_status,
        )
        overall = _derive_overall_severity(issues)
        summary = _compose_self_check_summary(
            overall,
            diagnostics,
            autopilot_state,
            paper_status,
        )
        return AISelfCheckResponse(
            generated_at=timestamp,
            overall_severity=overall,
            summary=summary,
            issues=issues,
        )


_self_check_manager = _SelfCheckManager()


@app.get("/diagnostics/ai/self-check", response_model=AISelfCheckResponse)
def get_ai_self_check(force: bool = False) -> AISelfCheckResponse:
    return _self_check_manager.current(force=force)


@app.post("/ai/portfolio/optimize", response_model=PortfolioOptimizationResponse)
def optimize_portfolio(payload: PortfolioOptimizationRequest) -> PortfolioOptimizationResponse:
    custom_assets = _convert_assets(payload.custom_assets)

    def _fetch_candles(market: str, interval: str, count: int):
        result = fetch_upbit_candles(market=market, interval=interval, count=count)
        return result.candles

    try:
        plan = ai.optimise_portfolio(
            risk_appetite=payload.risk_appetite,
            capital=payload.capital,
            include_cash=payload.include_cash,
            custom_assets=custom_assets,
            preferred_markets=payload.preferred_markets,
            candle_fetcher=_fetch_candles,
        )
    except (MarketDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    response = PortfolioOptimizationResponse(
        generated_at=plan.generated_at,
        risk_profile_label=plan.risk_profile_label,
        risk_appetite=plan.risk_appetite,
        expected_return_pct=plan.expected_return_pct,
        expected_volatility_pct=plan.expected_volatility_pct,
        sharpe_estimate=plan.sharpe_estimate,
        diversification_score_pct=plan.diversification_score_pct,
        tail_risk_guard_pct=plan.tail_risk_guard_pct,
        allocations=[_sanitize_allocation(allocation) for allocation in plan.allocations],
        hedging_notes=plan.hedging_notes,
        methodology=plan.methodology,
        market_briefings=plan.market_briefings,
    )

    _push_chat_summary("ai-portfolio", _summarise_portfolio_plan_for_chat(response))
    return response


@app.post("/ai/copilot", response_model=CopilotResponse)
def run_ai_copilot(payload: CopilotRequest) -> CopilotResponse:
    market = payload.market.upper()
    interval = payload.interval

    candles, _source = _load_candles_with_fallback(market, interval, 220)

    headlines = fetch_authoritative_news(limit=6)

    try:
        insight = ai.analyse_market(candles, market=market, interval=interval, news=headlines)
    except ValueError:
        fallback_candles, _ = _load_candles_with_fallback(market, interval, 260)
        insight = ai.analyse_market(fallback_candles, market=market, interval=interval, news=headlines)
    except Exception as exc:
        fallback_candles, _ = _load_candles_with_fallback(market, interval, 260)
        try:
            insight = ai.analyse_market(
                fallback_candles,
                market=market,
                interval=interval,
                news=headlines,
            )
        except Exception as final_exc:
            raise HTTPException(status_code=500, detail=f"AI 시장 분석 실패: {exc}") from final_exc

    portfolio_plan: Optional[ai.PortfolioAIPlan] = None
    if payload.include_portfolio:

        def _fetch(market_code: str, fetch_interval: str, count: int):
            candles, _ = _load_candles_with_fallback(market_code, fetch_interval, count)
            return candles

        try:
            portfolio_plan = ai.optimise_portfolio(
                risk_appetite=payload.risk_appetite,
                capital=payload.capital,
                include_cash=True,
                preferred_markets=[market],
                candle_fetcher=_fetch,
            )
        except (ValueError, MarketDataError):
            portfolio_plan = None

    try:
        autopilot_plan = ai.craft_autopilot_plan(
            insight=insight,
            risk_appetite=payload.risk_appetite,
            capital=payload.capital,
            mode=payload.mode.value,
            portfolio_plan=portfolio_plan,
        )
    except Exception as exc:
        autopilot_plan = _neutral_autopilot_plan(market, f"자동매매 계획 생성 실패: {exc}")

    autopilot_response_plan = autopilot_plan
    try:
        synthesis = ai.generate_copilot_synthesis(
            question=payload.question,
            insight=insight,
            autopilot=autopilot_plan,
            portfolio_plan=portfolio_plan,
            mode=payload.mode.value,
        )
    except Exception as exc:
        generated_at = _utcnow()
        answer = (
            "AI 코파일럿 기본 보고서를 생성했지만 상세 해설 중 오류가 발생했습니다. "
            f"오류 상세: {exc}"
        )
        summary_points = [
            f"{market} {interval} · 자동 생성 요약",
            "상세 분석을 생성하는 동안 문제가 발생했습니다.",
        ]
        risk_notices = [
            "내부 오류로 상세 코멘트를 생성하지 못했습니다. 잠시 후 다시 실행해주세요.",
        ]
        action_items: List[str] = []
        highlights: List[str] = []
    else:
        autopilot_response_plan = synthesis.autopilot
        generated_at = synthesis.generated_at
        answer = synthesis.answer
        summary_points = synthesis.summary_points
        risk_notices = synthesis.risk_notices
        action_items = synthesis.action_items
        highlights = synthesis.highlights

    autopilot_payload = _sanitize_autopilot_plan(autopilot_response_plan)

    insight_payload = MarketAIResponse(
        market=insight.market,
        interval=insight.interval,
        regime=insight.regime,
        recommended_action=insight.recommended_action,
        confidence_pct=_safe_number(insight.confidence_pct, lower=0.0, upper=100.0),
        summary=insight.summary,
        signals=insight.signals,
        metrics=_sanitize_metrics_payload(insight.metrics),
        risk=_sanitize_risk_payload(insight.risk),
        technical_confluence=_sanitize_confluence_payload(insight.technical_confluence),
        generated_at=insight.generated_at,
        news=_news_items(insight.news),
        timeframe_consensus=TimeframeConsensusPayload(**insight.timeframe_consensus.__dict__),
        institutional_confidence_pct=_safe_number(
            insight.institutional_confidence_pct, lower=0.0, upper=100.0
        ),
        institutional_commentary=insight.institutional_commentary,
        institutional_alert_level=_safe_number(
            getattr(insight, "institutional_alert_level", 0.0), lower=0.0, upper=100.0
        ),
        institutional_alerts=list(getattr(insight, "institutional_alerts", []) or []),
        credible_sources=list(getattr(insight, "credible_sources", []) or []),
    )

    response = CopilotResponse(
        generated_at=generated_at,
        answer=answer,
        summary_points=summary_points,
        risk_notices=risk_notices,
        action_items=action_items,
        highlights=highlights,
        autopilot=autopilot_payload,
        insight=insight_payload,
        news=_news_items(insight.news),
    )

    _push_chat_summary("ai-copilot", _summarise_copilot_for_chat(response))
    return response


def _build_assistant_response(payload: AssistantRequest) -> AssistantResponse:
    market = payload.market.upper()
    interval = payload.interval

    candles, _source = _load_candles_with_fallback(market, interval, 220)

    headlines = fetch_authoritative_news(limit=6)

    try:
        insight = ai.analyse_market(candles, market=market, interval=interval, news=headlines)
    except ValueError:
        fallback_candles, _ = _load_candles_with_fallback(market, interval, 260)
        insight = ai.analyse_market(fallback_candles, market=market, interval=interval, news=headlines)
    except Exception as exc:
        fallback_candles, _ = _load_candles_with_fallback(market, interval, 260)
        try:
            insight = ai.analyse_market(
                fallback_candles,
                market=market,
                interval=interval,
                news=headlines,
            )
        except Exception as final_exc:
            raise HTTPException(status_code=500, detail=f"AI 분석 실패: {exc}") from final_exc

    autopilot_plan: Optional[ai.AutoPilotOrderPlan] = None
    if payload.include_autopilot:
        try:
            autopilot_plan = ai.craft_autopilot_plan(
                insight=insight,
                risk_appetite=payload.risk_appetite,
                capital=payload.capital,
                mode=OrderMode.PAPER.value,
                portfolio_plan=None,
            )
        except Exception as exc:
            autopilot_plan = _neutral_autopilot_plan(market, f"어시스턴트 자동매매 계획 실패: {exc}")

    try:
        synthesis = ai.generate_assistant_synthesis(
            question=payload.question,
            insight=insight,
            autopilot=autopilot_plan,
            include_autopilot=payload.include_autopilot,
        )
    except Exception as exc:
        synthesis = ai.AssistantSynthesis(
            answer=(
                "AI 어시스턴트가 기본 분석을 제공했으나 상세 설명을 생성하지 못했습니다. "
                f"오류 상세: {exc}"
            ),
            insights=["내부 오류로 상세 인사이트를 생성하지 못했습니다."],
            next_steps=["잠시 후 다시 요청해주세요."],
            risk_notices=["내부 오류로 관망 상태를 유지합니다."],
            autopilot=autopilot_plan,
            generated_at=_utcnow(),
        )

    insight_payload = MarketAIResponse(
        market=insight.market,
        interval=insight.interval,
        regime=insight.regime,
        recommended_action=insight.recommended_action,
        confidence_pct=_safe_number(insight.confidence_pct, lower=0.0, upper=100.0),
        summary=insight.summary,
        signals=insight.signals,
        metrics=_sanitize_metrics_payload(insight.metrics),
        risk=_sanitize_risk_payload(insight.risk),
        technical_confluence=_sanitize_confluence_payload(insight.technical_confluence),
        generated_at=insight.generated_at,
        news=_news_items(insight.news),
        timeframe_consensus=TimeframeConsensusPayload(**insight.timeframe_consensus.__dict__),
        institutional_confidence_pct=_safe_number(
            insight.institutional_confidence_pct, lower=0.0, upper=100.0
        ),
        institutional_commentary=insight.institutional_commentary,
        institutional_alert_level=_safe_number(
            getattr(insight, "institutional_alert_level", 0.0), lower=0.0, upper=100.0
        ),
        institutional_alerts=list(getattr(insight, "institutional_alerts", []) or []),
        credible_sources=list(getattr(insight, "credible_sources", []) or []),
    )

    autopilot_payload = None
    if autopilot_plan and payload.include_autopilot:
        autopilot_payload = _sanitize_autopilot_plan(autopilot_plan)

    response = AssistantResponse(
        generated_at=synthesis.generated_at,
        question=payload.question,
        answer=synthesis.answer,
        insights=synthesis.insights,
        next_steps=synthesis.next_steps,
        risk_notices=synthesis.risk_notices,
        autopilot=autopilot_payload,
        insight=insight_payload,
        pushed_to_chat=False,
    )

    return response


@app.post("/ai/assistant", response_model=AssistantResponse)
def run_ai_assistant(payload: AssistantRequest) -> AssistantResponse:
    response = _build_assistant_response(payload)

    if payload.include_chat_push:
        summary = _summarise_assistant_for_chat(response)
        _push_chat_summary("ai-assistant", summary)
        response.pushed_to_chat = bool(summary)

    _record_assistant_history(response)
    return response


@app.get("/ai/assistant/history", response_model=AssistantHistoryListResponse)
def get_assistant_history() -> AssistantHistoryListResponse:
    entries = [
        AssistantHistoryEntryPayload(
            generated_at=_ensure_utc(item.generated_at),
            question=item.question,
            answer=item.answer,
            insights=list(item.insights),
            next_steps=list(item.next_steps),
            risk_notices=list(item.risk_notices),
            pushed_to_chat=bool(item.pushed_to_chat),
        )
        for item in _assistant_history_store.list()
    ]
    return AssistantHistoryListResponse(entries=entries, count=len(entries))


@app.delete("/ai/assistant/history", response_model=AssistantHistoryListResponse)
def clear_assistant_history() -> AssistantHistoryListResponse:
    _assistant_history_store.clear()
    return AssistantHistoryListResponse(entries=[], count=0)


@app.post("/trading/order", response_model=OrderResponse)
def submit_order(payload: OrderRequest) -> OrderResponse:
    if payload.mode is OrderMode.PAPER:
        market_code = payload.market.upper()
        needs_price_refresh = payload.price is None or payload.price <= 0
        if needs_price_refresh:
            last_price = _paper_broker.get_last_price(market_code)
            age_seconds = max(0.0, (_utcnow() - _ensure_utc(_paper_broker.last_update)).total_seconds())
            if last_price is None or age_seconds > 90.0:
                try:
                    _refresh_paper_market(
                        market=market_code,
                        interval=_paper_interval_preference or "minute1",
                    )
                except MarketDataError:
                    pass

        attempts = 0
        max_attempts = 2 if needs_price_refresh or payload.ord_type == "market" else 1
        last_error: ExecutionError | None = None

        while attempts < max_attempts:
            try:
                snapshot = _paper_broker.submit_order(
                    market=market_code,
                    side=payload.side,
                    price=payload.price,
                    volume=payload.volume,
                    ord_type=payload.ord_type,
                )
                break
            except ExecutionError as exc:
                last_error = exc
                attempts += 1
                if attempts >= max_attempts:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc

                try:
                    _refresh_paper_market(
                        market=market_code,
                        interval=_paper_interval_preference or "minute1",
                    )
                except MarketDataError:
                    pass
        else:  # pragma: no cover - defensive guard
            assert last_error is not None
            raise HTTPException(status_code=400, detail=str(last_error)) from last_error
        balance = _serialize_balance(snapshot)
        latest_order = balance.orders[0] if balance.orders else None
        response = OrderResponse(
            mode=OrderMode.PAPER,
            order_id=latest_order.order_id if latest_order else "paper-order",
            market=payload.market,
            side=payload.side,
            price=payload.price,
            volume=payload.volume,
            fee=latest_order.fee if latest_order else None,
            realized_pnl=latest_order.realized_pnl if latest_order else None,
            status="filled",
            balance=balance,
        )
        log_event(
            "order.submitted",
            mode="paper",
            market=response.market,
            side=response.side,
            ord_type=payload.ord_type,
            price=latest_order.price if latest_order else payload.price,
            volume=payload.volume,
            cash=balance.cash,
            portfolio_value=balance.portfolio_value,
        )
        return response

    try:
        client = create_upbit_client_from_env()
    except ExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        response = client.create_order(
            market=payload.market,
            side=payload.side,
            ord_type=payload.ord_type,
            volume=payload.volume,
            price=payload.price,
        )
    except ExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    price_value = response.get("price") or response.get("avg_price")
    volume_value = response.get("volume") or response.get("executed_volume")
    fee_value = response.get("paid_fee")

    order_response = OrderResponse(
        mode=OrderMode.LIVE,
        order_id=response.get("uuid", "live-order"),
        market=response.get("market", payload.market),
        side=response.get("side", payload.side),
        price=float(price_value) if price_value is not None else payload.price,
        volume=float(volume_value) if volume_value is not None else payload.volume,
        fee=float(fee_value) if fee_value is not None else None,
        status=response.get("state", "requested"),
        raw_response=response,
    )
    log_event(
        "order.submitted",
        mode="live",
        market=order_response.market,
        side=order_response.side,
        ord_type=payload.ord_type,
        price=order_response.price,
        volume=order_response.volume,
        status=order_response.status,
    )
    return order_response


@app.get("/trading/paper/status", response_model=PaperStatusResponse)
def get_paper_status(market: str = "KRW-BTC", interval: str = "minute1") -> PaperStatusResponse:
    return _paper_status_response(market=market.upper(), interval=interval)


@app.post("/trading/paper/reset", response_model=PaperStatusResponse)
def reset_paper(payload: PaperResetRequest) -> PaperStatusResponse:
    _paper_broker.reset(initial_cash=payload.initial_cash)
    _paper_last_price_source.clear()
    log_event("paper.reset", initial_cash=payload.initial_cash)
    return _paper_status_response()


@app.post("/trading/paper/mark", response_model=PaperStatusResponse)
def mark_paper(payload: PaperMarkRequest) -> PaperStatusResponse:
    try:
        _paper_broker.mark_price(market=payload.market, price=payload.price)
    except ExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _paper_last_price_source[payload.market.upper()] = "manual"
    log_event(
        "paper.mark",
        market=payload.market.upper(),
        price=payload.price,
    )
    return _paper_status_response()


@app.get("/trading/history", response_model=TradeHistoryResponse)
def get_trade_history(limit: int = 50) -> TradeHistoryResponse:
    limit = max(1, min(int(limit), 200))

    paper_orders = list(_paper_broker.orders)
    paper_items = [_paper_order_history(order) for order in paper_orders[:limit]]

    autopilot_state = _auto_trader.status()
    autopilot_items: list[TradeHistoryItemPayload] = []
    for execution in getattr(autopilot_state, "executions", []):
        try:
            autopilot_items.append(_execution_history(execution))
        except Exception:
            continue

    combined = paper_items + autopilot_items
    combined.sort(key=lambda item: item.executed_at, reverse=True)

    if len(combined) > limit:
        combined = combined[:limit]

    return TradeHistoryResponse(generated_at=_utcnow(), items=combined)


@app.get("/trading/live/balances", response_model=LiveBalancesResponse)
def get_live_balances() -> LiveBalancesResponse:
    try:
        client = create_upbit_client_from_env()
        balances = client.get_balances()
    except ExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LiveBalancesResponse(balances=balances)


@app.post("/portfolio/rebalance", response_model=RebalanceResponse)
def rebalance_portfolio(payload: RebalanceRequest) -> RebalanceResponse:
    try:
        orders = trading.rebalance_portfolio(
            current_positions=payload.current_positions,
            target_allocations=payload.target_allocations,
            portfolio_value=payload.portfolio_value,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    response = RebalanceResponse(orders=orders)
    _push_chat_summary("portfolio-rebalance", _summarise_rebalance_for_chat(response))
    return response


@app.post("/portfolio/blueprint", response_model=PortfolioBlueprintResponse)
def build_portfolio_blueprint(
    payload: PortfolioBlueprintRequest,
) -> PortfolioBlueprintResponse:
    try:
        plan = trading.design_risk_budgeted_portfolio(
            capital=payload.capital,
            risk_profile=payload.risk_profile,
            stable_assets=[asset.dict() for asset in payload.stable_assets],
            aggressive_assets=[asset.dict() for asset in payload.aggressive_assets],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    response = PortfolioBlueprintResponse(**plan)
    _push_chat_summary("portfolio-blueprint", _summarise_blueprint_for_chat(response))
    return response


@app.get("/prices/synthetic", response_model=list[CandlePayload])
def get_synthetic_prices(days: int = 120, seed: int | None = None) -> list[CandlePayload]:
    candles = trading.generate_synthetic_prices(days=days, seed=seed)
    return [
        CandlePayload(
            timestamp=item.timestamp,
            open=item.open,
            high=item.high,
            low=item.low,
            close=item.close,
            volume=item.volume,
        )
        for item in candles
    ]

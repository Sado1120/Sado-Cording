"""Background auto-trading orchestration for Sado Trade Bot."""
from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List, Optional, Tuple

from . import ai
from .adaptive import AdaptiveLearner, AdaptiveProfile
from .execution import BalanceSnapshot, ExecutionError, PaperBroker, create_upbit_client_from_env
from .market import (
    MarketData,
    MarketDataError,
    MarketInfo,
    TopMarketSelection,
    attempt_upbit_self_heal,
    fetch_authoritative_news,
    fetch_upbit_candles,
    fetch_upbit_markets as _market_directory_fetcher,
    fetch_upbit_top_markets,
    get_fallback_market_infos,
    get_upbit_network_state,
    is_upbit_network_operational,
)

# Backward compatibility for tests and external scripts monkeypatching the
# legacy ``fetch_upbit_markets`` symbol.
fetch_upbit_markets = _market_directory_fetcher
from .notifications import enqueue_digest, flush_due_digests, notify_synology_chat
from .log_utils import log_exception
from .schemas import (
    MarketRecommendationPayload,
    MarketRecommendationsResponse,
    OrderMode,
)
from .trading import Candle, evaluate_candles_integrity

HOLD_SIGNAL_REASON = "관망 신호: 명확한 매매 조건이 확인되지 않았습니다."

try:
    _configured_max_markets = int(os.getenv("AUTOPILOT_MAX_MARKETS_PER_CYCLE", "60"))
except ValueError:
    _configured_max_markets = 60

_AUTOPILOT_MAX_MARKETS_PER_CYCLE = max(5, min(_configured_max_markets, 200))
_REPEAT_MARKET_ROTATION_THRESHOLD = 3
_ALTERNATE_CONFIDENCE_MARGIN = 7.5
_REPEAT_MARKET_CONFIDENCE_FLOOR = 35.0
_REPEAT_MARKET_LOW_CONFIDENCE_FLOOR = 30.0
_RECENT_MARKET_HISTORY_LIMIT = 12


@dataclass
class AutoTraderConfig:
    """User-provided configuration for the auto-trading loop."""

    mode: OrderMode
    market: str
    interval: str
    risk_appetite: float
    capital: float
    poll_interval: float
    include_portfolio: bool = True
    max_position_pct: float = 0.25
    min_confidence_pct: float = 50.0
    auto_select_market: bool = True
    recommendation_base: str = "KRW"
    recommendation_interval: str = "minute60"
    recommendation_max_markets: int = 180
    recommendation_include_warnings: bool = False
    max_trades_per_cycle: int = 1


@dataclass
class AutoTraderLogEntry:
    timestamp: datetime
    level: str
    message: str


@dataclass
class AutoTraderExecution:
    mode: OrderMode
    market: str
    side: str
    price: float
    volume: float
    value: float
    executed_at: datetime
    detail: str


@dataclass
class AutoTraderState:
    running: bool = False
    config: Optional[AutoTraderConfig] = None
    last_plan: Optional[ai.AutoPilotOrderPlan] = None
    last_insight: Optional[ai.MarketAIInsight] = None
    last_execution: Optional[AutoTraderExecution] = None
    last_error: Optional[str] = None
    last_cycle_started_at: Optional[datetime] = None
    last_cycle_completed_at: Optional[datetime] = None
    logs: List[AutoTraderLogEntry] = field(default_factory=list)
    next_cycle_due_at: Optional[datetime] = None
    last_recommendations: List[str] = field(default_factory=list)
    last_recommendation_source: Optional[str] = None
    executions: List[AutoTraderExecution] = field(default_factory=list)
    last_skip_reason: Optional[str] = None
    recommendation_markets: List[str] = field(default_factory=list)
    recommendation_market_count: int = 0
    last_price_source: Optional[str] = None
    analysis_markets: List[str] = field(default_factory=list)
    analysis_market_count: int = 0
    candidate_rotation_cursor: int = 0
    repeat_market_count: int = 0
    rotation_anchor: Optional[str] = None
    last_network_status: Optional[str] = None
    last_network_message: Optional[str] = None
    last_network_detail: Optional[str] = None
    last_network_checked_at: Optional[datetime] = None
    last_network_backoff: Optional[float] = None
    effective_min_confidence: float = 0.0
    recent_markets: List[str] = field(default_factory=list)
    market_rotation_queue: List[str] = field(default_factory=list)
    adaptive_profile: Optional[AdaptiveProfile] = None


@dataclass
class _ActionableCandidate:
    """Container for actionable plans considered during a cycle."""

    plan: ai.AutoPilotOrderPlan
    insight: ai.MarketAIInsight
    portfolio_plan: Optional[ai.PortfolioAIPlan]
    candles: List[Candle]
    last_close: float
    price_source: str
    used_fallback: bool = False


class AutoTrader:
    """Manage an autonomous trading loop for paper and live execution."""

    def __init__(
        self,
        *,
        broker: PaperBroker,
        candle_fetcher: Callable[..., MarketData] = fetch_upbit_candles,
        news_fetcher: Callable[[int], List[dict]] = fetch_authoritative_news,
        analyse_market: Callable[..., ai.MarketAIInsight] = ai.analyse_market,
        autopilot_builder: Callable[..., ai.AutoPilotOrderPlan] = ai.craft_autopilot_plan,
        portfolio_builder: Callable[..., ai.PortfolioAIPlan] = ai.optimise_portfolio,
        time_provider: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        notifier: Optional[Callable[[str], bool]] = notify_synology_chat,
        recommendation_scanner: Optional[
            Callable[[str, str, int, int, bool], MarketRecommendationsResponse]
        ] = None,
        learner: Optional[AdaptiveLearner] = None,
    ) -> None:
        self._broker = broker
        self._candle_fetcher = candle_fetcher
        self._news_fetcher = news_fetcher
        self._analyse_market = analyse_market
        self._autopilot_builder = autopilot_builder
        self._portfolio_builder = portfolio_builder
        self._time_provider = time_provider
        self._notifier = notifier
        self._recommendation_scanner: Optional[
            Callable[[str, str, int, int, bool], MarketRecommendationsResponse]
        ] = None

        self._config: Optional[AutoTraderConfig] = None
        self._state = AutoTraderState()
        # Use an RLock so lifecycle hooks can append logs while holding the lock.
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._learner = learner or AdaptiveLearner()

        # Ensure a recommendation scanner is always available so the
        # auto-pilot never collapses to a single-market workflow when the
        # FastAPI bootstrap helper is not used (예: CLI 나 테스트 환경).
        self.set_recommendation_scanner(recommendation_scanner)

    # ------------------------------------------------------------------
    # Lifecycle control
    # ------------------------------------------------------------------
    def start(self, config: AutoTraderConfig) -> AutoTraderState:
        """Apply configuration, execute an immediate cycle, and spawn the loop."""

        with self._lock:
            config.market = config.market.upper()
            config.recommendation_base = (config.recommendation_base or "ALL").upper()
            self._config = config
            self._state.config = config
            self._state.running = True
            self._state.last_skip_reason = None
            self._state.repeat_market_count = 0
            self._state.rotation_anchor = None
            self._state.recent_markets = []
            self._state.market_rotation_queue = []
            try:
                self._state.effective_min_confidence = float(config.min_confidence_pct)
            except (TypeError, ValueError):  # pragma: no cover - defensive guard
                self._state.effective_min_confidence = 0.0
            self._stop_event.clear()
        self._append_log("info", "자동매매 오토파일럿을 시작합니다.")
        self._safe_notify("자동매매 오토파일럿을 시작했습니다.")

        if self._learner:
            snapshot = self._broker.snapshot()
            profile = self._learner.reset(initial_equity=snapshot.portfolio_value, config=config)
            profile = self._learner.apply(config=config)
            with self._lock:
                self._state.config = config
                self._state.adaptive_profile = profile
                self._state.effective_min_confidence = profile.min_confidence_pct

        try:
            self.run_cycle()
        except Exception as exc:  # pragma: no cover - defensive guard
            self._handle_cycle_exception(exc, config=config, context="initial")
        self._ensure_thread()
        return self.status()

    def stop(self) -> AutoTraderState:
        with self._lock:
            self._stop_event.set()
            thread = self._thread
            self._thread = None
            self._state.running = False
            self._append_log("info", "자동매매 오토파일럿을 중지했습니다.")
            self._safe_notify("자동매매 오토파일럿을 중지했습니다.")
            self._state.next_cycle_due_at = None
            self._state.repeat_market_count = 0
            self._state.rotation_anchor = None

        if thread and thread.is_alive():
            thread.join(timeout=2)
        return self.status()

    def status(self) -> AutoTraderState:
        with self._lock:
            config_snapshot = replace(self._state.config) if self._state.config is not None else None
            snapshot = AutoTraderState(
                running=self._state.running,
                config=config_snapshot,
                last_plan=self._state.last_plan,
                last_insight=self._state.last_insight,
                last_execution=self._state.last_execution,
                last_error=self._state.last_error,
                last_cycle_started_at=self._state.last_cycle_started_at,
                last_cycle_completed_at=self._state.last_cycle_completed_at,
                logs=list(self._state.logs),
                next_cycle_due_at=self._state.next_cycle_due_at,
                last_recommendations=list(self._state.last_recommendations),
                last_recommendation_source=self._state.last_recommendation_source,
                executions=list(self._state.executions),
                last_skip_reason=self._state.last_skip_reason,
                recommendation_markets=list(self._state.recommendation_markets),
                recommendation_market_count=self._state.recommendation_market_count,
                last_price_source=self._state.last_price_source,
                analysis_markets=list(self._state.analysis_markets),
                analysis_market_count=self._state.analysis_market_count,
                candidate_rotation_cursor=self._state.candidate_rotation_cursor,
                repeat_market_count=self._state.repeat_market_count,
                last_network_status=self._state.last_network_status,
                last_network_message=self._state.last_network_message,
                last_network_detail=self._state.last_network_detail,
                last_network_checked_at=self._state.last_network_checked_at,
                last_network_backoff=self._state.last_network_backoff,
                effective_min_confidence=self._state.effective_min_confidence,
                recent_markets=list(self._state.recent_markets),
                market_rotation_queue=list(self._state.market_rotation_queue),
                adaptive_profile=replace(self._state.adaptive_profile)
                if self._state.adaptive_profile is not None
                else None,
            )
            if (
                snapshot.last_skip_reason is None
                and snapshot.last_plan is not None
                and snapshot.last_plan.side == "flat"
            ):
                snapshot.last_skip_reason = HOLD_SIGNAL_REASON
        return snapshot

    # ------------------------------------------------------------------
    # Internal mechanics
    # ------------------------------------------------------------------
    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return

            thread = threading.Thread(target=self._run_loop, name="auto-trader", daemon=True)
            self._thread = thread

        thread.start()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                config_snapshot = self._config
            try:
                self.run_cycle()
            except Exception as exc:  # pragma: no cover - defensive guard
                self._handle_cycle_exception(exc, config=config_snapshot, context="background")
            interval = self._current_interval()
            if interval <= 0:
                interval = 60.0
            if self._stop_event.wait(interval):
                break

    def _current_interval(self) -> float:
        with self._lock:
            if self._config is None:
                return 60.0
            return max(10.0, float(self._config.poll_interval))

    def _apply_adaptive_strategy(self, config: AutoTraderConfig) -> None:
        if not self._learner:
            return
        profile = self._learner.apply(config=config)
        with self._lock:
            self._state.adaptive_profile = profile
            self._state.effective_min_confidence = profile.min_confidence_pct
            self._state.config = config

    def _append_log(self, level: str, message: str) -> None:
        entry = AutoTraderLogEntry(timestamp=self._time_provider(), level=level, message=message)
        with self._lock:
            self._state.logs.append(entry)
            self._state.logs = self._state.logs[-20:]
        if level in {"trade", "error"}:
            self._safe_notify(message)

    def _set_skip_reason(self, reason: Optional[str]) -> None:
        with self._lock:
            self._state.last_skip_reason = reason

    def _handle_cycle_exception(
        self,
        exc: Exception,
        *,
        config: Optional[AutoTraderConfig],
        context: str,
    ) -> None:
        """Gracefully recover from unexpected cycle failures.

        When upstream dependencies raise unforeseen exceptions we do not want
        to surface a 500 response or crash the background thread. Instead we
        capture the failure, mark the cycle as skipped, and schedule an
        automatic retry while keeping the auto-pilot running.
        """

        log_exception(
            "autopilot.cycle.failure",
            exc,
            context=context,
            market=getattr(config, "market", None),
            mode=getattr(config, "mode", None),
        )

        message = f"자동매매 사이클 오류({context}): {exc}"
        self._append_log("error", message)
        self._set_skip_reason("사이클 오류로 관망합니다. 자동으로 재시도합니다.")

        now = self._time_provider()
        with self._lock:
            if config is not None:
                self._state.config = config
            self._state.last_error = message
            self._state.last_plan = None
            self._state.last_insight = None
            self._state.last_execution = None
            self._state.last_cycle_completed_at = now
            try:
                interval_seconds = max(10.0, float((config or self._config).poll_interval))
            except Exception:
                interval_seconds = 60.0
            self._state.next_cycle_due_at = now + timedelta(seconds=interval_seconds)
            if not self._state.last_network_status:
                self._state.last_network_status = "warning"
            if not self._state.last_network_message:
                self._state.last_network_message = "사이클 오류로 재시도 대기 중입니다."

        flush_due_digests(now=now, notifier=self._notifier)

    def _relax_confidence_floor(self, threshold: float, observed: float) -> float:
        """Dynamically relax the working confidence floor when signals fall short."""

        with self._lock:
            baseline = float(self._config.min_confidence_pct) if self._config else threshold
            current = self._state.effective_min_confidence or baseline
            effective_threshold = min(current, threshold)

            reduced_target = max(
                _REPEAT_MARKET_LOW_CONFIDENCE_FLOOR,
                min(
                    baseline,
                    max(_REPEAT_MARKET_LOW_CONFIDENCE_FLOOR, threshold - 2.0),
                    observed + 5.0,
                ),
            )

            new_value = min(effective_threshold, reduced_target)

            if new_value < current:
                self._state.effective_min_confidence = new_value
                self._append_log(
                    "info",
                    f"자동매매 신뢰도 기준을 {current:.1f}%→{new_value:.1f}%로 완화합니다.",
                )

            return new_value

    def _safe_notify(self, message: str) -> None:
        if not self._notifier:
            return
        try:
            self._notifier(f"[Sado Trade Bot] {message}")
        except Exception:  # pragma: no cover - notifications are best-effort
            pass

    # ------------------------------------------------------------------
    # Trading logic
    # ------------------------------------------------------------------
    def run_cycle(self) -> None:
        with self._lock:
            config = self._config
            if config is None:
                return
            self._state.last_cycle_started_at = self._time_provider()

        network_state = get_upbit_network_state()
        with self._lock:
            self._state.last_network_status = str(network_state.get("status") or "unknown")
            self._state.last_network_message = network_state.get("message")
            self._state.last_network_detail = network_state.get("detail")
            checked_at = network_state.get("checked_at")
            self._state.last_network_checked_at = (
                checked_at if isinstance(checked_at, datetime) else None
            )
            try:
                backoff = float(network_state.get("backoff_seconds_remaining", 0.0) or 0.0)
            except (TypeError, ValueError):
                backoff = 0.0
            self._state.last_network_backoff = max(0.0, backoff)

        if config.mode == OrderMode.LIVE and not is_upbit_network_operational():
            network_state = get_upbit_network_state()
            guard_reason = "업비트 실시간 연결이 복구될 때까지 관망합니다."
            message = network_state.get("message") or guard_reason
            self._append_log(
                "warning",
                f"{message} · 실거래 보호 모드로 관망합니다.",
            )
            self._set_skip_reason(guard_reason)
            recovery = attempt_upbit_self_heal("autopilot-network-guard")
            status = recovery.get("status") if isinstance(recovery, dict) else None
            detail = recovery.get("steps") if isinstance(recovery, dict) else None
            if status:
                if status == "cooldown":
                    self._append_log(
                        "info",
                        "업비트 자가 복구 대기 중입니다. 잠시 후 다시 시도합니다.",
                    )
                elif status == "success":
                    self._append_log(
                        "info",
                        "업비트 연결 복구 절차를 완료했습니다.",
                    )
                elif status == "skipped":
                    self._append_log(
                        "info",
                        "업비트 네트워크가 비활성화되어 자가 복구를 건너뜁니다.",
                    )
                else:
                    self._append_log(
                        "info",
                        f"업비트 자가 복구 상태: {status}",
                    )
            if isinstance(detail, list) and detail:
                last_step = detail[-1]
                description = last_step.get("detail") if isinstance(last_step, dict) else None
                if description:
                    self._append_log("debug", f"자가 복구 단계: {description}")
            refreshed_state = get_upbit_network_state()
            with self._lock:
                self._state.last_network_status = str(refreshed_state.get("status") or "unknown")
                self._state.last_network_message = refreshed_state.get("message")
                self._state.last_network_detail = refreshed_state.get("detail")
                checked_at = refreshed_state.get("checked_at")
                self._state.last_network_checked_at = (
                    checked_at if isinstance(checked_at, datetime) else None
                )
                try:
                    backoff = float(
                        refreshed_state.get("backoff_seconds_remaining", 0.0) or 0.0
                    )
                except (TypeError, ValueError):
                    backoff = 0.0
                self._state.last_network_backoff = max(0.0, backoff)
            with self._lock:
                self._state.last_plan = None
                self._state.last_insight = None
                self._state.last_price_source = "network-down"
                self._state.analysis_markets = []
                self._state.analysis_market_count = 0
                self._state.last_error = None
                self._state.last_cycle_completed_at = self._time_provider()
                interval_seconds = max(10.0, float(config.poll_interval))
                self._state.next_cycle_due_at = self._state.last_cycle_completed_at + timedelta(
                    seconds=interval_seconds
                )
            flush_due_digests(now=self._time_provider(), notifier=self._notifier)
            return

        self._apply_adaptive_strategy(config)

        try:
            market_code, candidate_markets = self._resolve_market(config)
        except Exception as exc:  # pragma: no cover - defensive guard
            friendly_error = f"추천 엔진 오류: {exc}"
            self._append_log("error", friendly_error)
            self._set_skip_reason("추천 엔진 오류로 관망 모드로 전환합니다.")
            with self._lock:
                self._state.last_error = friendly_error
                self._state.last_cycle_completed_at = self._time_provider()
                interval_seconds = max(10.0, float(config.poll_interval))
                self._state.next_cycle_due_at = self._state.last_cycle_completed_at + timedelta(
                    seconds=interval_seconds
                )
            return

        with self._lock:
            recommendation_source = (self._state.last_recommendation_source or "").strip()
            last_recommendations = list(self._state.last_recommendations)

        trusted_sources = {"upbit", "upbit_alt", "mixed"}
        synthetic_low_confidence = bool(
            recommendation_source == "synthetic"
            and last_recommendations
            and all("신뢰도 0.0%" in entry for entry in last_recommendations)
        )
        fallback_source = recommendation_source in {"", "fallback"}

        fallback_context = False
        if fallback_source or synthetic_low_confidence:
            preview = ", ".join(last_recommendations[:3]) if last_recommendations else "없음"
            if config.mode == OrderMode.LIVE:
                guard_reason = "실시간 추천 데이터를 확보하지 못해 관망합니다."
                self._append_log(
                    "warning",
                    (
                        "실시간 추천이 아닌 데이터 소스로 분석되어 자동매매를 일시 중단합니다. "
                        f"(source={recommendation_source}, preview={preview})"
                    ),
                )
                self._set_skip_reason(guard_reason)
                with self._lock:
                    self._state.last_plan = None
                    self._state.last_insight = None
                    self._state.last_execution = None
                    self._state.last_cycle_completed_at = self._time_provider()
                    interval_seconds = max(10.0, float(config.poll_interval))
                    self._state.next_cycle_due_at = self._state.last_cycle_completed_at + timedelta(
                        seconds=interval_seconds
                    )
                flush_due_digests(now=self._time_provider(), notifier=self._notifier)
                return

            fallback_context = True
            self._append_log(
                "warning",
                (
                    "실시간 추천이 부족하지만 페이퍼 모드에서 안전하게 분석을 계속합니다. "
                    f"(source={recommendation_source}, preview={preview})"
                ),
            )
            self._set_skip_reason(None)
            with self._lock:
                current_status = (self._state.last_network_status or "").strip().lower()
                if current_status in {"", "unknown", "down"}:
                    self._state.last_network_status = "warning"
                    self._state.last_network_message = (
                        "실시간 업비트 시세가 부족해 합성 데이터를 활용하고 있습니다."
                    )
                    if not self._state.last_network_detail:
                        self._state.last_network_detail = "페이퍼 모드 · 대체 시세 사용"
                    self._state.last_network_backoff = 0.0
        else:
            self._set_skip_reason(None)

        if recommendation_source and recommendation_source not in trusted_sources:
            self._append_log(
                "warning",
                (
                    "지원되지 않는 추천 소스로 분석되었습니다. "
                    f"(source={recommendation_source})"
                ),
            )

        try:
            snapshot = self._broker.snapshot()
            news_feed = self._safe_news()
            candidate_markets = list(dict.fromkeys(candidate_markets or [market_code]))

            with self._lock:
                repeat_count = self._state.repeat_market_count

            with self._lock:
                effective_floor = self._state.effective_min_confidence or config.min_confidence_pct

            base_min_confidence = float(min(config.min_confidence_pct, effective_floor))
            if fallback_context:
                adjusted_floor = max(
                    _REPEAT_MARKET_LOW_CONFIDENCE_FLOOR,
                    base_min_confidence - 5.0,
                )
                if adjusted_floor < base_min_confidence:
                    self._append_log(
                        "info",
                        (
                            "실시간 추천 부족으로 신뢰도 기준을 "
                            f"{base_min_confidence:.1f}%→{adjusted_floor:.1f}%로 임시 완화합니다."
                        ),
                    )
                base_min_confidence = adjusted_floor
                relaxation_active = True
            relaxed_threshold = base_min_confidence
            relaxation_active = False
            if repeat_count >= _REPEAT_MARKET_ROTATION_THRESHOLD:
                relaxed_threshold = max(
                    _REPEAT_MARKET_CONFIDENCE_FLOOR,
                    base_min_confidence - (2 * _ALTERNATE_CONFIDENCE_MARGIN),
                )
                if relaxed_threshold < base_min_confidence:
                    relaxation_active = True

            if candidate_markets:
                preview = ", ".join(candidate_markets[:3])
                if len(candidate_markets) > 1:
                    message = f"추천 후보 {len(candidate_markets)}종목 탐색 시작: {preview}"
                else:
                    message = f"추천 후보 {preview} 분석을 시작합니다."
                self._append_log("info", message)

            analysis_candidates, truncated = self._select_analysis_batch(candidate_markets)
            if truncated:
                self._append_log(
                    "info",
                    (
                        f"이번 사이클에서는 추천 {len(candidate_markets)}종목 중 "
                        f"{len(analysis_candidates)}종목을 우선 분석합니다."
                    ),
                )

            selected_plan: Optional[ai.AutoPilotOrderPlan] = None
            selected_insight: Optional[ai.MarketAIInsight] = None
            selected_portfolio = None
            selected_candles: List[Candle] = []
            selected_last_close = 0.0
            selected_market = market_code
            selected_price_source = "manual"
            last_candidate_error: Optional[Exception] = None
            integrity_failures: List[str] = []
            reported_plan: Optional[ai.AutoPilotOrderPlan] = None
            reported_insight: Optional[ai.MarketAIInsight] = None
            reported_portfolio = None
            reported_candles: List[Candle] = []
            reported_last_close = 0.0
            reported_market = market_code
            reported_price_source = "manual"
            reported_confidence = float("-inf")
            selected_plan_actionable = False
            fallback_used = bool(fallback_context)

            skip_messages: List[str] = []
            best_confidence = float("-inf")
            actionable_candidates: List[_ActionableCandidate] = []

            for index, candidate in enumerate(analysis_candidates):
                try:
                    market_data = self._candle_fetcher(
                        market=candidate,
                        interval=config.interval,
                        count=200,
                    )
                    candles = market_data.candles
                    if not candles:
                        raise MarketDataError("캔들 데이터가 비어 있습니다.")

                    integrity_score, integrity_flags = evaluate_candles_integrity(candles)
                    if integrity_score < 60.0:
                        summary = ", ".join(integrity_flags) if integrity_flags else "상세 사유 없음"
                        message = f"{candidate} 데이터 무결성 부족 ({integrity_score:.0f}점: {summary})"
                        integrity_failures.append(message)
                        self._append_log("warning", message)
                        continue

                    last_close = candles[-1].close
                    insight = self._analyse_market(
                        candles,
                        market=candidate,
                        interval=config.interval,
                        news=news_feed,
                    )

                    portfolio_plan = None
                    if config.include_portfolio:
                        try:
                            portfolio_plan = self._portfolio_builder(
                                risk_appetite=config.risk_appetite,
                                capital=config.capital,
                                include_cash=True,
                                preferred_markets=[candidate],
                                candle_fetcher=self._candle_fetcher,
                            )
                        except Exception as exc:  # pragma: no cover - optional enhancement
                            self._append_log("warning", f"포트폴리오 계산 실패: {exc}")

                    autopilot_plan = self._autopilot_builder(
                        insight=insight,
                        risk_appetite=config.risk_appetite,
                        capital=config.capital,
                        mode=config.mode,
                        portfolio_plan=portfolio_plan,
                    )
                except (MarketDataError, ValueError) as exc:
                    last_candidate_error = exc
                    self._append_log("warning", f"{candidate} 분석 실패: {exc}")
                    continue

                scalping_signal = None
                scalping_applied = False
                interval_text = (config.interval or "").lower()
                enable_scalping = False
                if interval_text.startswith("minute"):
                    try:
                        minutes = int(interval_text.replace("minute", ""))
                    except ValueError:
                        minutes = None
                    else:
                        enable_scalping = minutes is not None and minutes <= 30

                if enable_scalping and index < 5:
                    try:
                        scalping_data = self._candle_fetcher(
                            market=candidate,
                            interval="minute3",
                            count=120,
                        )
                    except MarketDataError:
                        scalping_data = None
                    except Exception as exc:  # pragma: no cover - defensive logging
                        scalping_data = None
                        self._append_log("debug", f"{candidate} 스캘핑 캔들 로드 실패: {exc}")
                    if scalping_data and getattr(scalping_data, "candles", None):
                        scalping_signal = ai.evaluate_scalping_signal(
                            scalping_data.candles,
                            interval="minute3",
                        )

                has_position = self._has_position(snapshot, candidate)
                if scalping_signal is not None:
                    autopilot_plan, scalping_applied = ai.apply_scalping_signal(
                        plan=autopilot_plan,
                        scalping=scalping_signal,
                        risk_appetite=config.risk_appetite,
                        allow_short=has_position,
                    )
                    if scalping_applied:
                        self._append_log(
                            "info",
                            (
                                f"{candidate} 스캘핑 강화 적용: {scalping_signal.label} "
                                f"(신뢰도 {scalping_signal.conviction_pct:.1f}%) · "
                                f"마이크로 {scalping_signal.micro_trend} · "
                                f"VWAP 편차 {scalping_signal.vwap_gap_pct:+.2f}%"
                            ),
                        )

                if autopilot_plan.confidence_pct > reported_confidence:
                    reported_confidence = autopilot_plan.confidence_pct
                    reported_plan = autopilot_plan
                    reported_insight = insight
                    reported_portfolio = portfolio_plan
                    reported_candles = candles
                    reported_last_close = last_close
                    reported_market = candidate
                    reported_price_source = market_data.source

                candidate_fallback_used = False
                skip_reason: Optional[str] = None
                candidate_threshold = float(base_min_confidence)
                if index > 0:
                    candidate_threshold = max(
                        _REPEAT_MARKET_CONFIDENCE_FLOOR,
                        base_min_confidence - _ALTERNATE_CONFIDENCE_MARGIN,
                    )
                if relaxation_active:
                    candidate_threshold = min(candidate_threshold, relaxed_threshold)

                if autopilot_plan.side == "ask" and not has_position:
                    skip_reason = f"{autopilot_plan.market} 매도 신호이지만 보유 수량이 없어 다음 추천을 확인합니다."
                elif autopilot_plan.side == "flat":
                    skip_reason = f"{autopilot_plan.market} 관망 신호로 다음 후보를 탐색합니다."
                elif autopilot_plan.confidence_pct < candidate_threshold:
                    adjusted_threshold = self._relax_confidence_floor(
                        candidate_threshold, autopilot_plan.confidence_pct
                    )
                    if autopilot_plan.confidence_pct >= adjusted_threshold:
                        if adjusted_threshold < candidate_threshold:
                            self._append_log(
                                "info",
                                (
                                    f"{autopilot_plan.market} 후보에 완화된 신뢰도 "
                                    f"{adjusted_threshold:.1f}%를 적용합니다."
                                ),
                            )
                        candidate_threshold = adjusted_threshold
                    elif (
                        repeat_count >= _REPEAT_MARKET_ROTATION_THRESHOLD
                        and autopilot_plan.confidence_pct > 0
                    ):
                        candidate_threshold = autopilot_plan.confidence_pct
                        self._append_log(
                            "info",
                            (
                                f"{autopilot_plan.market} 신뢰도 {autopilot_plan.confidence_pct:.1f}%이지만 "
                                "반복 종목 회피를 위해 실행 기준을 일시 완화합니다."
                            ),
                        )
                    else:
                        if relaxation_active and autopilot_plan.confidence_pct >= relaxed_threshold:
                            self._append_log(
                                "info",
                                (
                                    f"반복 종목 회피를 위해 신뢰도 기준을 "
                                    f"{base_min_confidence:.1f}%에서 {relaxed_threshold:.1f}%로 완화합니다."
                                ),
                            )
                        elif repeat_count >= _REPEAT_MARKET_ROTATION_THRESHOLD:
                            if autopilot_plan.confidence_pct >= _REPEAT_MARKET_LOW_CONFIDENCE_FLOOR:
                                self._append_log(
                                    "info",
                                    (
                                        f"신뢰도 {autopilot_plan.confidence_pct:.1f}% 후보지만 반복 종목을 "
                                        "피하기 위해 심층 분석을 계속합니다."
                                    ),
                                )
                            elif autopilot_plan.confidence_pct > 0:
                                self._append_log(
                                    "info",
                                    (
                                        f"신뢰도 {autopilot_plan.confidence_pct:.1f}% 후보지만 반복 종목 회피를 "
                                        "위해 후보를 유지합니다."
                                    ),
                                )
                            else:
                                skip_reason = (
                                    f"{autopilot_plan.market} 신뢰도가 0%로 평가되어 후보에서 제외합니다."
                                )
                        else:
                            skip_reason = (
                                f"{autopilot_plan.market} 신뢰도 {autopilot_plan.confidence_pct:.1f}%가 "
                                f"기준 {candidate_threshold:.1f}% 미만입니다. 다음 추천을 확인합니다."
                            )
                        if skip_reason is None:
                            skip_reason = (
                                f"{autopilot_plan.market} 신뢰도 {autopilot_plan.confidence_pct:.1f}%가 "
                                f"기준 {candidate_threshold:.1f}% 미만입니다. 다음 추천을 확인합니다."
                            )

                position_pct = min(autopilot_plan.position_size_pct / 100.0, config.max_position_pct)
                if position_pct <= 0 and skip_reason is None:
                    skip_reason = f"{autopilot_plan.market} 포지션 비중이 0%로 계산되어 주문을 건너뜁니다."

                fallback_eligible = (
                    skip_reason
                    and autopilot_plan.side != "ask"
                    and (
                        autopilot_plan.side == "flat"
                        or autopilot_plan.confidence_pct <= 0
                    )
                )
                if fallback_eligible:
                    if not hasattr(insight, "institutional_commentary"):
                        try:
                            setattr(
                                insight,
                                "institutional_commentary",
                                "기관 코멘터리가 제공되지 않았습니다.",
                            )
                        except Exception:  # pragma: no cover - defensive
                            pass
                    fallback_plan = ai.craft_momentum_fallback_plan(
                        insight=insight,
                        candles=candles,
                        risk_appetite=config.risk_appetite,
                        capital=config.capital,
                        allow_short=has_position,
                    )
                    if fallback_plan is not None and fallback_plan.side != "flat":
                        autopilot_plan = fallback_plan
                        skip_reason = None
                        candidate_fallback_used = True
                        fallback_used = True
                        self._append_log(
                            "info",
                            f"{candidate} 후보에 모멘텀 대체 전략을 적용합니다.",
                        )

                if skip_reason:
                    skip_messages.append(skip_reason)
                    self._append_log("info", skip_reason)
                    continue

                actionable_candidates.append(
                    _ActionableCandidate(
                        plan=autopilot_plan,
                        insight=insight,
                        portfolio_plan=portfolio_plan,
                        candles=candles,
                        last_close=last_close,
                        price_source=market_data.source,
                        used_fallback=candidate_fallback_used,
                    )
                )
                if candidate_fallback_used:
                    fallback_used = True

                if autopilot_plan.confidence_pct > best_confidence:
                    best_confidence = autopilot_plan.confidence_pct
                    selected_plan = autopilot_plan
                    selected_insight = insight
                    selected_portfolio = portfolio_plan
                    selected_candles = candles
                    selected_last_close = last_close
                    selected_market = candidate
                    selected_price_source = market_data.source
                    selected_plan_actionable = True

            chosen_candidate: Optional[_ActionableCandidate] = None
            rotated_market = False
            diversified_candidates: List[_ActionableCandidate] = []

            if actionable_candidates:
                chosen_candidate, rotated_market = self._choose_actionable_candidate(
                    actionable_candidates
                )
                if chosen_candidate is not None:
                    selected_plan = chosen_candidate.plan
                    selected_insight = chosen_candidate.insight
                    selected_portfolio = chosen_candidate.portfolio_plan
                    selected_candles = chosen_candidate.candles
                    selected_last_close = chosen_candidate.last_close
                    selected_market = chosen_candidate.plan.market
                    selected_price_source = chosen_candidate.price_source
                    selected_plan_actionable = True

                prioritised = []
                if chosen_candidate is not None:
                    prioritised.append(chosen_candidate)
                prioritised.extend(
                    candidate
                    for candidate in sorted(
                        actionable_candidates,
                        key=lambda item: item.plan.confidence_pct,
                        reverse=True,
                    )
                    if candidate is not chosen_candidate
                )
                if prioritised:
                    diversified_candidates = self._select_diversified_candidates(
                        prioritised,
                        snapshot=snapshot,
                        config=config,
                    )

            if selected_plan is None and reported_plan is not None:
                selected_plan = reported_plan
                selected_insight = reported_insight
                selected_portfolio = reported_portfolio
                selected_candles = reported_candles
                selected_last_close = reported_last_close
                selected_market = reported_market
                selected_price_source = reported_price_source

            if (
                not selected_plan_actionable
                and reported_plan is not None
                and reported_insight is not None
                and reported_candles
            ):
                fallback_plan = ai.craft_momentum_fallback_plan(
                    insight=reported_insight,
                    candles=reported_candles,
                    risk_appetite=config.risk_appetite,
                    capital=config.capital,
                    allow_short=self._has_position(snapshot, reported_plan.market),
                )
                if fallback_plan is not None:
                    selected_plan = fallback_plan
                    selected_insight = reported_insight
                    selected_portfolio = reported_portfolio
                    selected_candles = reported_candles
                    selected_last_close = reported_last_close
                    selected_market = fallback_plan.market
                    selected_price_source = reported_price_source
                    selected_plan_actionable = fallback_plan.side != "flat"
                    fallback_used = True
                    skip_messages = []
                    if selected_plan_actionable:
                        self._append_log(
                            "info",
                            f"{selected_market} 관망 신호를 모멘텀 전략으로 대체합니다.",
                        )
                    else:
                        self._append_log(
                            "info",
                            "모멘텀 대체 전략도 관망 상태로 판단했습니다.",
                        )

            if selected_plan is None:
                if integrity_failures:
                    summary = integrity_failures[0]
                    if len(integrity_failures) > 1:
                        summary = f"{summary} 외 {len(integrity_failures) - 1}개 후보"
                    reason = f"데이터 무결성 부족으로 관망합니다. {summary}"
                    self._set_skip_reason(reason)
                    raise MarketDataError(reason)

                if last_candidate_error is not None:
                    raise last_candidate_error
                raise MarketDataError("실행 가능한 추천을 찾지 못했습니다.")

            if not selected_plan_actionable:
                reason = skip_messages[0] if skip_messages else HOLD_SIGNAL_REASON
                self._set_skip_reason(reason)

            if selected_market != market_code:
                market_code = selected_market
            if len(candidate_markets) > 1:
                self._append_log(
                    "info",
                    f"총 {len(candidate_markets)}종목을 평가한 결과 {selected_market}을(를) 선택했습니다.",
                )

            if rotated_market and selected_plan_actionable:
                self._append_log(
                    "info",
                    f"최근 매매 종목과 겹침을 방지하기 위해 {selected_market} 종목으로 순환 선택을 적용했습니다.",
                )

            if selected_price_source != "upbit" and config.mode == OrderMode.LIVE:
                self._append_log(
                    "warning",
                    f"{selected_market} 시세가 실시간 업비트가 아니므로 거래에 주의가 필요합니다. (source={selected_price_source})",
                )

            executions: List[AutoTraderExecution] = []
            executed_candidates: List[_ActionableCandidate] = []
            if selected_plan_actionable:
                execution_candidates = (
                    diversified_candidates if diversified_candidates else [
                        _ActionableCandidate(
                            plan=selected_plan,
                            insight=selected_insight,
                            portfolio_plan=selected_portfolio,
                            candles=selected_candles,
                            last_close=selected_last_close,
                            price_source=selected_price_source,
                            used_fallback=fallback_used,
                        )
                    ]
                )

                for candidate in execution_candidates:
                    exec_result = self._maybe_execute(
                        config=config,
                        plan=candidate.plan,
                        candles=candidate.candles,
                        last_close=candidate.last_close,
                        price_source=candidate.price_source,
                        used_fallback=candidate.used_fallback,
                    )
                    if exec_result:
                        executions.append(exec_result)
                        executed_candidates.append(candidate)

                if executed_candidates:
                    final_candidate = executed_candidates[-1]
                    selected_plan = final_candidate.plan
                    selected_insight = final_candidate.insight
                    selected_portfolio = final_candidate.portfolio_plan
                    selected_candles = final_candidate.candles
                    selected_last_close = final_candidate.last_close
                    selected_market = final_candidate.plan.market
                    selected_price_source = final_candidate.price_source
                elif not diversified_candidates:
                    # When no executions succeeded and no diversification list existed,
                    # preserve the originally selected plan for logging/skip reasons.
                    pass

            execution: Optional[AutoTraderExecution] = executions[-1] if executions else None

            selected_market_upper = selected_plan.market.upper()

            with self._lock:
                previous_market = (
                    self._state.last_plan.market.upper()
                    if self._state.last_plan is not None
                    else None
                )
                if selected_plan is not None:
                    if previous_market == selected_market_upper:
                        self._state.repeat_market_count = max(1, self._state.repeat_market_count + 1)
                    else:
                        self._state.repeat_market_count = 1
                    self._state.rotation_anchor = selected_market_upper
                else:
                    self._state.repeat_market_count = 0
                    self._state.rotation_anchor = None
                self._state.last_plan = selected_plan
                self._state.last_insight = selected_insight
                if self._config:
                    self._config.market = selected_market
                    self._state.config = self._config
                    if executions:
                        if relaxation_active and relaxed_threshold < float(self._config.min_confidence_pct):
                            self._config.min_confidence_pct = float(relaxed_threshold)
                        self._state.effective_min_confidence = float(self._config.min_confidence_pct)
                    elif relaxation_active:
                        self._state.effective_min_confidence = min(
                            self._state.effective_min_confidence or base_min_confidence,
                            relaxed_threshold,
                        )
                if selected_plan is not None:
                    market_upper = selected_plan.market.upper()
                    recent_history = list(self._state.recent_markets)
                    recent_history.append(market_upper)
                    self._state.recent_markets = recent_history[-_RECENT_MARKET_HISTORY_LIMIT:]
                if executions:
                    self._state.last_execution = executions[-1]
                    self._state.executions.extend(executions)
                    self._state.executions = self._state.executions[-40:]
                    self._state.last_skip_reason = None
                self._state.last_price_source = selected_price_source
                if selected_price_source in {"synthetic", "upbit_alt", "upbit_stale"}:
                    current_status = (self._state.last_network_status or "").strip().lower()
                    if current_status in {"", "unknown", "down"}:
                        self._state.last_network_status = "warning"
                    if not self._state.last_network_message:
                        self._state.last_network_message = (
                            "대체 시세 소스로 자동매매를 계속합니다."
                        )
                    if not self._state.last_network_detail:
                        self._state.last_network_detail = (
                            f"가격 소스: {selected_price_source}"
                        )
                    self._state.last_network_backoff = 0.0
                self._state.last_error = None
                self._state.last_cycle_completed_at = self._time_provider()
                interval_seconds = max(10.0, float(config.poll_interval))
                self._state.next_cycle_due_at = self._state.last_cycle_completed_at + timedelta(
                    seconds=interval_seconds
                )

            if self._learner:
                post_snapshot = self._broker.snapshot()
                if not executions:
                    with self._lock:
                        manual_floor = self._state.effective_min_confidence or 0.0
                    if manual_floor > 0 and manual_floor < float(config.min_confidence_pct):
                        config.min_confidence_pct = manual_floor
                profile = self._learner.observe(
                    config=config,
                    before=snapshot,
                    after=post_snapshot,
                    executed=bool(executions),
                )
                with self._lock:
                    self._state.adaptive_profile = profile
                    self._state.effective_min_confidence = profile.min_confidence_pct
                    self._state.config = config

            if executions:
                for exec_item in executions:
                    self._append_log(
                        "trade",
                        f"{exec_item.mode.upper()} {exec_item.market} {exec_item.side} {exec_item.volume:.6f} 실행",
                    )
                if len(executions) > 1:
                    ordered_markets: List[str] = []
                    for exec_item in executions:
                        market_code = exec_item.market
                        if market_code not in ordered_markets:
                            ordered_markets.append(market_code)
                    traded_markets = ", ".join(ordered_markets)
                    self._append_log(
                        "info",
                        f"분산 매매 실행 완료: {len(executions)}건 체결 ({traded_markets})",
                    )
            else:
                bias_label = {
                    "long": "롱",
                    "short": "숏",
                    "neutral": "관망",
                }.get(selected_plan.bias, selected_plan.bias)
                suffix = " (모멘텀 대체)" if fallback_used else ""
                self._append_log(
                    "info",
                    f"{selected_plan.market} 분석 완료: {bias_label} 전략{suffix}",
                )

        except (MarketDataError, ExecutionError, ValueError) as exc:
            with self._lock:
                self._state.last_error = str(exc)
                self._state.last_cycle_completed_at = self._time_provider()
                if config is not None:
                    interval_seconds = max(10.0, float(config.poll_interval))
                    self._state.next_cycle_due_at = self._state.last_cycle_completed_at + timedelta(
                        seconds=interval_seconds
                    )
                self._state.last_skip_reason = self._state.last_skip_reason or "데이터 오류로 관망합니다."
                self._state.repeat_market_count = 0
                self._state.rotation_anchor = None
            self._append_log("error", f"자동매매 사이클 실패: {exc}")
        except Exception as exc:  # pragma: no cover - defensive fallback
            friendly_error = f"내부 오류로 사이클을 종료했습니다: {exc}"
            with self._lock:
                self._state.last_error = friendly_error
                self._state.last_cycle_completed_at = self._time_provider()
                if config is not None:
                    interval_seconds = max(10.0, float(config.poll_interval))
                    self._state.next_cycle_due_at = self._state.last_cycle_completed_at + timedelta(
                        seconds=interval_seconds
                    )
                self._state.last_skip_reason = (
                    self._state.last_skip_reason or "내부 오류가 발생해 관망 상태를 유지합니다."
                )
                self._state.repeat_market_count = 0
                self._state.rotation_anchor = None
            self._append_log("error", friendly_error)
        finally:
            flush_due_digests(now=self._time_provider(), notifier=self._notifier)

    def _safe_news(self) -> List[dict]:
        try:
            return self._news_fetcher(4)
        except Exception:  # pragma: no cover - network failures
            return []

    def _has_position(self, snapshot, market: str) -> bool:
        market_upper = market.upper()
        for position in getattr(snapshot, "positions", []):
            try:
                if position.market.upper() == market_upper and position.volume > 0:
                    return True
            except AttributeError:
                continue
        return False

    def _select_analysis_batch(self, candidates: List[str]) -> Tuple[List[str], bool]:
        if not candidates:
            with self._lock:
                self._state.analysis_markets = []
                self._state.analysis_market_count = 0
                self._state.candidate_rotation_cursor = 0
            return [], False

        limit = max(1, min(len(candidates), _AUTOPILOT_MAX_MARKETS_PER_CYCLE))
        truncated = len(candidates) > limit

        if not truncated:
            with self._lock:
                cursor = self._state.candidate_rotation_cursor % len(candidates)

            batch: List[str] = []
            for offset in range(len(candidates)):
                index = (cursor + offset) % len(candidates)
                batch.append(candidates[index])

            next_cursor = (cursor + 1) % len(candidates)

            with self._lock:
                self._state.analysis_markets = list(batch)
                self._state.analysis_market_count = len(batch)
                self._state.candidate_rotation_cursor = next_cursor
            return batch, False

        with self._lock:
            cursor = self._state.candidate_rotation_cursor % len(candidates)

        batch: List[str] = []
        for offset in range(limit):
            index = (cursor + offset) % len(candidates)
            batch.append(candidates[index])

        next_cursor = (cursor + limit) % len(candidates)

        with self._lock:
            self._state.analysis_markets = list(batch)
            self._state.analysis_market_count = len(batch)
            self._state.candidate_rotation_cursor = next_cursor

        return batch, True

    def _choose_actionable_candidate(
        self, candidates: List[_ActionableCandidate]
    ) -> Tuple[Optional[_ActionableCandidate], bool]:
        """Select an actionable plan while avoiding repeated markets."""

        if not candidates:
            return None, False

        ordered = sorted(candidates, key=lambda item: item.plan.confidence_pct, reverse=True)
        ordered = self._rotate_candidates_by_queue(ordered)

        with self._lock:
            last_market = None
            repeat_count = self._state.repeat_market_count
            min_confidence = 55.0
            if self._state.config is not None:
                try:
                    min_confidence = float(self._state.config.min_confidence_pct)
                except (TypeError, ValueError):  # pragma: no cover - defensive guard
                    min_confidence = 55.0
            if self._state.last_execution is not None:
                last_market = self._state.last_execution.market.upper()
            elif self._state.last_plan is not None:
                last_market = self._state.last_plan.market.upper()
            recent_markets = {
                code.upper() for code in self._state.recent_markets[-_RECENT_MARKET_HISTORY_LIMIT:]
            }
            prior_execution_market = (
                self._state.last_execution.market.upper()
                if self._state.last_execution is not None
                else None
            )

        top_candidate = ordered[0]
        if not last_market or top_candidate.plan.market.upper() != last_market:
            return top_candidate, False

        top_confidence = top_candidate.plan.confidence_pct
        rotation_tolerance = 5.0
        alt_candidates = [
            candidate for candidate in ordered if candidate.plan.market.upper() != last_market
        ]

        rotation_trigger = max(1, _REPEAT_MARKET_ROTATION_THRESHOLD)

        effective_repeats = repeat_count
        if (
            prior_execution_market
            and prior_execution_market == top_candidate.plan.market.upper()
            and repeat_count == 0
        ):
            effective_repeats = max(effective_repeats, rotation_trigger)

        min_rotation_confidence = max(
            _REPEAT_MARKET_CONFIDENCE_FLOOR, min_confidence - _ALTERNATE_CONFIDENCE_MARGIN
        )

        if effective_repeats < rotation_trigger:
            if repeat_count >= 1:
                preemptive_tolerance = max(rotation_tolerance, 10.0)
                for candidate in alt_candidates:
                    market_code = candidate.plan.market.upper()
                    if market_code in recent_markets:
                        continue
                    if (
                        candidate.plan.confidence_pct >= min_rotation_confidence
                        and candidate.plan.confidence_pct >= top_confidence - preemptive_tolerance
                    ):
                        self._append_log(
                            "info",
                            (
                                f"동일 종목 반복을 줄이기 위해 {market_code} 후보를 선제적으로 선택합니다."
                                f" (신뢰도 {candidate.plan.confidence_pct:.1f}%)"
                            ),
                        )
                        return candidate, True
            return top_candidate, False

        for candidate in alt_candidates:
            market_code = candidate.plan.market.upper()
            if market_code in recent_markets:
                continue
            if candidate.plan.confidence_pct >= min_rotation_confidence:
                return candidate, True

        if effective_repeats >= rotation_trigger:
            for candidate in alt_candidates:
                market_code = candidate.plan.market.upper()
                if market_code in recent_markets:
                    continue
                if candidate.plan.confidence_pct >= _REPEAT_MARKET_CONFIDENCE_FLOOR:
                    self._append_log(
                        "info",
                        (
                            f"동일 종목 반복을 방지하기 위해 {market_code} 후보를 선택했습니다."
                            f" (신뢰도 {candidate.plan.confidence_pct:.1f}%)"
                        ),
                    )
                    return candidate, True

        for candidate in alt_candidates:
            if candidate.plan.confidence_pct >= top_confidence - rotation_tolerance:
                return candidate, True

        if effective_repeats >= rotation_trigger and alt_candidates:
            for candidate in alt_candidates:
                market_code = candidate.plan.market.upper()
                if market_code in recent_markets:
                    continue
                self._append_log(
                    "info",
                    (
                        "동일 종목 반복을 피하기 위해 신뢰도에 상관없이 "
                        f"대체 후보 {market_code}을(를) 선택합니다."
                    ),
                )
                return candidate, True

        if effective_repeats >= rotation_trigger:
            low_confidence_alternatives = [
                candidate
                for candidate in alt_candidates
                if candidate.plan.confidence_pct >= _REPEAT_MARKET_LOW_CONFIDENCE_FLOOR
            ]
            if low_confidence_alternatives:
                chosen = max(
                    low_confidence_alternatives,
                    key=lambda item: item.plan.confidence_pct,
                )
                self._append_log(
                    "info",
                    (
                        "동일 종목 반복을 방지하기 위해 신뢰도 "
                        f"{chosen.plan.confidence_pct:.1f}% 대체 후보 {chosen.plan.market}을(를) 선택했습니다."
                    ),
                )
                return chosen, True

            for candidate in alt_candidates:
                if candidate.plan.confidence_pct >= _REPEAT_MARKET_CONFIDENCE_FLOOR:
                    return candidate, True

        return top_candidate, False

    def _select_diversified_candidates(
        self,
        prioritised: List[_ActionableCandidate],
        *,
        snapshot: BalanceSnapshot,
        config: AutoTraderConfig,
    ) -> List[_ActionableCandidate]:
        """Pick a subset of actionable candidates for diversified execution."""

        max_trades = getattr(config, "max_trades_per_cycle", 1) or 1
        max_trades = max(1, min(int(max_trades), 5))
        if max_trades <= 1:
            return prioritised[:1]

        selected: List[_ActionableCandidate] = []
        seen_markets: set[str] = set()

        for candidate in prioritised:
            market_code = candidate.plan.market.upper()
            if market_code in seen_markets:
                continue

            position_pct = min(candidate.plan.position_size_pct / 100.0, config.max_position_pct)
            if position_pct <= 0:
                continue

            if candidate.plan.side == "ask" and not self._has_position(snapshot, candidate.plan.market):
                continue

            if candidate.plan.side == "bid" and snapshot.cash <= 0 and not snapshot.positions:
                # Permit rebalancing to free cash, but skip when 계좌가 완전히 비어 있는 경우.
                continue

            selected.append(candidate)
            seen_markets.add(market_code)

            if len(selected) >= max_trades:
                break

        return selected if selected else prioritised[:1]

    def _should_try_next_candidate(
        self,
        *,
        plan: ai.AutoPilotOrderPlan,
        config: AutoTraderConfig,
        snapshot,
        has_more_candidates: bool,
    ) -> bool:
        if not has_more_candidates or not config.auto_select_market:
            return False

        if plan.side == "ask" and not self._has_position(snapshot, plan.market):
            self._append_log(
                "info",
                f"{plan.market} 매도 신호이지만 보유 수량이 없어 다음 추천을 확인합니다.",
            )
            return True

        if plan.side == "flat":
            self._append_log("info", f"{plan.market} 관망 신호로 다음 후보를 탐색합니다.")
            return True

        if plan.confidence_pct < config.min_confidence_pct:
            self._append_log(
                "info",
                (
                    f"{plan.market} 신뢰도 {plan.confidence_pct:.1f}%가 "
                    f"기준 {config.min_confidence_pct:.1f}% 미만입니다. 다음 추천을 확인합니다."
                ),
            )
            return True

        return False

    def _maybe_execute(
        self,
        *,
        config: AutoTraderConfig,
        plan: ai.AutoPilotOrderPlan,
        candles: Iterable[Candle],
        last_close: float,
        price_source: str,
        used_fallback: bool = False,
    ) -> Optional[AutoTraderExecution]:
        if plan.side == "flat":
            self._set_skip_reason(HOLD_SIGNAL_REASON)
            return None
        if plan.confidence_pct < config.min_confidence_pct and not used_fallback:
            self._append_log("info", "신뢰도가 낮아 주문을 건너뜁니다.")
            self._set_skip_reason(
                f"신뢰도 {plan.confidence_pct:.1f}%가 기준 {config.min_confidence_pct:.1f}%보다 낮습니다."
            )
            return None
        if used_fallback and plan.confidence_pct < config.min_confidence_pct:
            self._append_log(
                "info",
                (
                    "모멘텀 대체 전략으로 신뢰도 기준을 완화하고 주문을 진행합니다. "
                    f"(신뢰도 {plan.confidence_pct:.1f}% ≥ fallback)"
                ),
            )

        position_pct = min(plan.position_size_pct / 100.0, config.max_position_pct)
        if position_pct <= 0:
            self._append_log("info", "포지션 비중이 0%로 계산되어 주문을 생략합니다.")
            self._set_skip_reason("포지션 비중이 0%로 계산되어 관망합니다.")
            return None

        synthetic_price = price_source not in {"upbit", "upbit_alt", "upbit_stale"}
        if synthetic_price:
            if config.mode == OrderMode.LIVE:
                self._append_log(
                    "warning",
                    (
                        f"실거래 보호: {plan.market} 시세 소스({price_source})가 실시간 업비트가 "
                        "아니어서 주문을 차단했습니다."
                    ),
                )
                self._set_skip_reason("실거래는 업비트 실시간 시세에서만 실행됩니다.")
                return None

            self._append_log(
                "info",
                (
                    f"페이퍼 모드: {plan.market} 합성 시세({price_source})로 주문을 시뮬레이션합니다. "
                    "실시간 데이터 복구 후 결과를 재검증하세요."
                ),
            )

        if config.mode == OrderMode.PAPER:
            execution = self._execute_paper(plan=plan, position_pct=position_pct, last_close=last_close)
        else:
            execution = self._execute_live(plan=plan, position_pct=position_pct, last_close=last_close)

        if execution:
            self._set_skip_reason(None)
        return execution

    def _rebalance_for_purchase(
        self,
        *,
        snapshot: BalanceSnapshot,
        plan: ai.AutoPilotOrderPlan,
        shortfall: float,
    ) -> float:
        if shortfall <= 0:
            return 0.0
        positions = [
            pos
            for pos in snapshot.positions
            if pos.market != plan.market and pos.market_value > 0 and pos.volume > 0
        ]
        if not positions:
            return 0.0

        sell_ratio = min(0.5, max(0.1, plan.confidence_pct / 120.0))
        freed_total = 0.0
        self._append_log("info", "가용 현금이 부족해 기존 포지션 일부를 조정합니다.")

        for position in sorted(positions, key=lambda item: item.market_value, reverse=True):
            price = self._broker.get_last_price(position.market) or position.market_price or 0.0
            if price <= 0:
                continue
            sell_value = position.market_value * sell_ratio
            volume = min(position.volume, sell_value / price)
            if volume <= 0:
                continue
            try:
                self._broker.mark_price(market=position.market, price=price)
                self._broker.submit_order(
                    market=position.market,
                    side="ask",
                    price=None,
                    volume=volume,
                    ord_type="market",
                )
            except ExecutionError as exc:
                self._append_log("warning", f"재배분 매도 실패({position.market}): {exc}")
                continue
            freed_total += volume * price
            if freed_total >= shortfall * 0.9:
                break

        if freed_total > 0:
            self._append_log("info", f"자금 재배분으로 {freed_total:,.0f} KRW 확보했습니다.")
        else:
            self._append_log("info", "재배분 가능한 포지션이 없어 관망합니다.")
        return freed_total

    def _execute_paper(
        self,
        *,
        plan: ai.AutoPilotOrderPlan,
        position_pct: float,
        last_close: float,
    ) -> Optional[AutoTraderExecution]:
        if not self._is_valid_price(plan.market, last_close):
            return None

        snapshot = self._broker.snapshot()
        capital = snapshot.portfolio_value
        target_value = max(0.0, capital * position_pct)

        if plan.side == "bid":
            available_cash = snapshot.cash
            fee_rate = getattr(self._broker, "fee_rate", 0.0) or 0.0
            max_affordable = available_cash / (1.0 + max(fee_rate, 0.0))
            order_value = min(max_affordable, target_value)

            shortfall = max(0.0, target_value - order_value)
            if shortfall > target_value * 0.2:
                freed = self._rebalance_for_purchase(
                    snapshot=snapshot,
                    plan=plan,
                    shortfall=shortfall,
                )
                if freed > 0:
                    snapshot = self._broker.snapshot()
                    available_cash = snapshot.cash
                    max_affordable = available_cash / (1.0 + max(fee_rate, 0.0))
                    order_value = min(max_affordable, target_value)

            if max_affordable > 0:
                safety_value = max_affordable * 0.9995
                if safety_value < order_value:
                    order_value = safety_value
            else:
                order_value = 0.0

            if order_value < last_close * 0.0001:
                self._append_log(
                    "info",
                    (
                        "현금이 부족해 매수를 생략합니다. "
                        f"(보유 현금 {available_cash:,.0f} KRW · 목표 {target_value:,.0f} KRW)"
                    ),
                )
                self._set_skip_reason(
                    f"가용 현금 {available_cash:,.0f} KRW로 목표 {target_value:,.0f} KRW 매수를 실행하기 어렵습니다."
                )
                return None
            volume = order_value / last_close
            self._broker.mark_price(market=plan.market, price=last_close)
            try:
                self._broker.submit_order(
                    market=plan.market,
                    side="bid",
                    price=None,
                    volume=volume,
                    ord_type="market",
                )
            except ExecutionError as exc:
                message = str(exc)
                if "현금" not in message:
                    raise
                self._append_log("info", "가용 현금 한도로 주문 금액을 재조정합니다.")
                adjusted_value = max(0.0, available_cash * 0.995)
                if adjusted_value <= 0 or adjusted_value < last_close * 0.0001:
                    self._set_skip_reason(
                        f"가용 현금 {available_cash:,.0f} KRW로 목표 {target_value:,.0f} KRW 매수를 실행하기 어렵습니다."
                    )
                    return None
                volume = adjusted_value / last_close
                self._broker.submit_order(
                    market=plan.market,
                    side="bid",
                    price=None,
                    volume=volume,
                    ord_type="market",
                )
                order_value = adjusted_value
            return AutoTraderExecution(
                mode=OrderMode.PAPER,
                market=plan.market,
                side="bid",
                price=last_close,
                volume=volume,
                value=order_value,
                executed_at=self._time_provider(),
                detail="페이퍼 계좌 자동매수",
            )

        position = next((pos for pos in snapshot.positions if pos.market == plan.market), None)
        if not position or position.volume <= 0:
            self._append_log("info", "보유 포지션이 없어 매도를 생략합니다.")
            self._set_skip_reason("보유 중인 포지션이 없어 매도를 생략합니다.")
            return None

        order_value = min(position.market_value, capital * position_pct)
        if order_value <= 0:
            self._append_log("info", "매도 대상 가치가 없습니다.")
            self._set_skip_reason("매도 대상 자산 가치가 없어 관망합니다.")
            return None

        volume = min(position.volume, order_value / last_close)
        if volume <= 0:
            self._append_log("info", "매도 수량이 0으로 계산되어 생략합니다.")
            self._set_skip_reason("매도 수량이 0으로 계산되어 주문을 건너뜁니다.")
            return None

        self._broker.mark_price(market=plan.market, price=last_close)
        self._broker.submit_order(
            market=plan.market,
            side="ask",
            price=None,
            volume=volume,
            ord_type="market",
        )
        return AutoTraderExecution(
            mode=OrderMode.PAPER,
            market=plan.market,
            side="ask",
            price=last_close,
            volume=volume,
            value=volume * last_close,
            executed_at=self._time_provider(),
            detail="페이퍼 계좌 자동매도",
        )

    def set_recommendation_scanner(
        self,
        scanner: Optional[Callable[[str, str, int, int, bool], MarketRecommendationsResponse]],
    ) -> None:
        """Configure the callable used to fetch AI market recommendations."""

        with self._lock:
            if scanner is None:
                self._recommendation_scanner = self._default_recommendation_scan
                self._append_log(
                    "warning",
                    "추천 스캐너가 지정되지 않아 기본 내장 스캐너를 사용합니다.",
                )
            else:
                self._recommendation_scanner = scanner

    # ------------------------------------------------------------------
    # Recommendation support
    # ------------------------------------------------------------------
    def _default_recommendation_scan(
        self,
        base: str,
        interval: str,
        limit: int,
        max_markets: int,
        include_warnings: bool,
    ) -> MarketRecommendationsResponse:
        """Fallback recommendation list used when no external scanner is bound."""

        base_currency = (base or "KRW").upper()
        interval = interval or "minute60"
        safe_limit = max(1, min(int(limit or 1), 10))
        safe_max_markets = max(safe_limit, min(int(max_markets or 60), 200))
        include_warnings = bool(include_warnings)

        errors: List[str] = []
        try:
            listing = fetch_upbit_markets(only_krw=base_currency != "ALL")
            market_infos = list(listing.markets)
            listing_source = listing.source
        except Exception as exc:  # pragma: no cover - defensive guard
            market_infos = get_fallback_market_infos(only_krw=base_currency != "ALL")
            listing_source = "fallback"
            errors.append(f"실시간 마켓 목록 조회 실패: {exc}")

        filtered: List[MarketInfo] = []
        for info in market_infos:
            warning = getattr(info, "market_warning", "")
            if getattr(info, "trading_suspended", False):
                continue
            if not include_warnings and str(warning or "").upper() not in {"", "NONE"}:
                continue
            if base_currency != "ALL" and getattr(info, "base_currency", "").upper() != base_currency:
                continue
            filtered.append(info)

        filtered.sort(key=lambda info: info.market)
        selected = filtered[:safe_max_markets]

        recommendation_source = "upbit" if listing_source == "upbit" else "synthetic"
        default_reason = (
            "기본 추천 스캐너가 제공한 후보입니다. 상세 평가는 자동매매 엔진이 수행합니다."
        )

        payloads: List[MarketRecommendationPayload] = []
        for info in selected:
            payloads.append(
                MarketRecommendationPayload(
                    market=info.market,
                    korean_name=info.korean_name,
                    english_name=info.english_name,
                    base_currency=info.base_currency,
                    quote_currency=info.quote_currency,
                    score=0.0,
                    confidence_pct=0.0,
                    regime="분석 대기",
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
                    summary="기본 후보 목록",
                    reason=default_reason,
                    source=recommendation_source,
                )
            )

        recommendations = payloads[:safe_limit]
        analysed_markets = len(payloads)

        generated_at = self._time_provider()

        return MarketRecommendationsResponse(
            generated_at=generated_at,
            interval=interval,
            base_currency=base_currency,
            limit=safe_limit,
            analysed_markets=analysed_markets,
            analysis_duration_ms=0.0,
            analysis_source=recommendation_source,
            recommendations=recommendations,
            errors=errors,
        )

    def _resolve_market(self, config: AutoTraderConfig) -> Tuple[str, List[str]]:
        market_code = (config.market or "KRW-BTC").upper()
        recommendations: List[str] = []
        source: Optional[str] = None
        candidate_markets: List[str] = [market_code]

        if not config.auto_select_market or not self._recommendation_scanner:
            with self._lock:
                self._state.last_recommendations = recommendations
                self._state.last_recommendation_source = source
                self._state.recommendation_markets = candidate_markets
                self._state.recommendation_market_count = len(candidate_markets)
            return market_code, candidate_markets

        scan_limit = max(5, min(int(config.recommendation_max_markets or 180), 200))
        recommendation_limit = 5

        fallback_message: Optional[str] = None
        try:
            result = self._recommendation_scanner(
                (config.recommendation_base or "ALL").upper(),
                config.recommendation_interval or config.interval,
                recommendation_limit,
                scan_limit,
                bool(config.recommendation_include_warnings),
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            fallback_message = f"추천 종목 분석 실패: {exc}"
            self._append_log("warning", fallback_message)
            result = None

        top_market = market_code
        if result is None:
            recs = []
        else:
            try:
                recs = getattr(result, "recommendations", []) or []
            except Exception:  # pragma: no cover - unexpected payload shape
                recs = []

        formatted: List[str] = []
        for entry in recs:
            market_name = getattr(entry, "market", None)
            if not market_name:
                continue
            market_name = str(market_name).upper()
            label_parts = [market_name]
            action = getattr(entry, "recommended_action", "")
            if action:
                label_parts.append(str(action))
            confidence = getattr(entry, "confidence_pct", None)
            try:
                if confidence is not None:
                    label_parts.append(f"신뢰도 {float(confidence):.1f}%")
            except (TypeError, ValueError):
                pass
            score = getattr(entry, "score", None)
            try:
                if score is not None:
                    label_parts.append(f"점수 {float(score):.1f}")
            except (TypeError, ValueError):
                pass
            if not formatted and top_market == market_code:
                top_market = market_name
            formatted.append(" · ".join(label_parts))
            if market_name not in candidate_markets:
                candidate_markets.append(market_name)

        if result is None:
            source = None
        else:
            try:
                source = getattr(result, "analysis_source", None)
            except Exception:  # pragma: no cover - unexpected payload shape
                source = None

        if result is None:
            errors = []
        else:
            try:
                errors = getattr(result, "errors", []) or []
            except Exception:  # pragma: no cover
                errors = []

        if fallback_message:
            errors = [fallback_message, *errors]

        if errors:
            for message in errors[:3]:
                self._append_log("warning", f"추천 분석 경고: {message}")

        if top_market != market_code:
            self._append_log("info", f"AI 추천 종목으로 전환: {top_market}")
            market_code = top_market
            candidate_markets = [top_market] + [
                code for code in candidate_markets if code != top_market
            ]

        deduped_candidates = list(dict.fromkeys(candidate_markets))

        fallback_candidates: List[str] = []
        base_currency = (config.recommendation_base or "ALL").upper()
        if config.auto_select_market and not formatted:
            fallback_limit = max(0, scan_limit - len(deduped_candidates))
            if fallback_limit > 0:
                fallback_candidates = self._fallback_candidate_markets(
                    base_currency=base_currency,
                    exclude=deduped_candidates,
                    limit=fallback_limit,
                )
                if fallback_candidates:
                    deduped_candidates.extend(fallback_candidates)
                    deduped_candidates = list(dict.fromkeys(deduped_candidates))
                    preview = ", ".join(fallback_candidates[:3])
                    self._append_log(
                        "info",
                        (
                            f"추천 후보를 확장해 상위 {len(fallback_candidates)}종목을 추가합니다: {preview}"
                        ),
                    )
                    if not source:
                        source = "fallback"

        if not deduped_candidates:
            fallback_candidates = self._fallback_candidate_markets(
                base_currency=base_currency,
                exclude=[],
                limit=max(5, scan_limit),
            )
            if fallback_candidates:
                deduped_candidates = fallback_candidates
                market_code = fallback_candidates[0]
                source = source or "fallback"
                self._append_log(
                    "warning",
                    "추천 결과가 비어 있어 KRW 폴백 후보를 사용합니다.",
                )

        with self._lock:
            self._state.last_recommendations = formatted[:5]
            self._state.last_recommendation_source = source
            self._state.recommendation_markets = deduped_candidates[:15]
            self._state.recommendation_market_count = len(deduped_candidates)

            # 유지 중인 회전 큐를 새 추천 목록과 병합해 반복 초기화를 방지한다.
            new_order = [code.upper() for code in deduped_candidates]
            existing_queue = [
                code for code in self._state.market_rotation_queue if code in new_order
            ]
            for code in new_order:
                if code not in existing_queue:
                    existing_queue.append(code)
            self._state.market_rotation_queue = existing_queue

        if formatted:
            timestamp = self._time_provider()
            header_parts = [timestamp.strftime("%H:%M"), config.recommendation_interval or config.interval]
            if source:
                header_parts.append(str(source))
            header = " · ".join(part for part in header_parts if part)
            digest_lines = [f"{index + 1}. {line}" for index, line in enumerate(formatted[:5])]
            enqueue_digest(
                "recommendations",
                "\n".join([header, *digest_lines]),
                timestamp=timestamp,
            )

        return market_code, deduped_candidates

    def _fallback_candidate_markets(
        self,
        *,
        base_currency: str,
        exclude: Iterable[str],
        limit: int,
    ) -> List[str]:
        """Return deterministic market candidates when AI recommendations are empty."""

        base = (base_currency or "ALL").upper()
        only_krw = False if base == "ALL" else base == "KRW"
        safe_limit = max(5, int(limit) if limit else 5)
        seen = {code.upper() for code in exclude}
        try:
            listing = fetch_upbit_markets(only_krw=only_krw)
            base_market_infos = list(listing.markets)
        except Exception as exc:
            base_market_infos = get_fallback_market_infos(only_krw=only_krw)
            self._append_log(
                "warning",
                f"업비트 마켓 목록 조회에 실패했습니다: {exc}",
            )

        selection: TopMarketSelection
        try:
            selection = fetch_upbit_top_markets(
                base_currency="KRW" if only_krw else "ALL",
                limit=safe_limit + len(seen),
                include_warnings=True,
            )
        except MarketDataError as exc:
            self._append_log(
                "warning",
                f"업비트 거래대금 순위 조회에 실패했습니다: {exc}",
            )
            selection = TopMarketSelection(
                markets=[],
                source="fallback-volume",
                errors=[str(exc)],
            )

        for message in selection.errors:
            self._append_log("warning", f"추천 후보 조회 경고: {message}")

        market_infos = list(selection.markets) if selection.markets else base_market_infos
        if not market_infos:
            market_infos = get_fallback_market_infos(only_krw=only_krw)

        fallback: List[str] = []
        for info in market_infos:
            if info.trading_suspended:
                continue
            if base != "ALL" and info.base_currency.upper() != base:
                continue
            market_name = info.market.upper()
            if market_name in seen:
                continue
            fallback.append(market_name)
            if len(fallback) >= safe_limit:
                break
        return fallback

    def _next_rotating_market(self, markets: List[str]) -> Optional[str]:
        """Return the next preferred market in the rotation queue."""

        normalised = [code.upper() for code in markets if code]
        if len(normalised) <= 1:
            return normalised[0] if normalised else None

        with self._lock:
            queue = [code for code in self._state.market_rotation_queue if code in normalised]
            if not queue:
                queue = list(dict.fromkeys(normalised))

            rotation_trigger = max(1, _REPEAT_MARKET_ROTATION_THRESHOLD)
            target = queue[0]
            queue = queue[1:] + [target]
            self._state.market_rotation_queue = queue

            if self._state.repeat_market_count < rotation_trigger:
                anchor = self._state.rotation_anchor
                if anchor and anchor in normalised:
                    return anchor
                self._state.rotation_anchor = target
                return target

            self._state.rotation_anchor = None
            return queue[0]

    def _rotate_candidates_by_queue(
        self, candidates: List[_ActionableCandidate]
    ) -> List[_ActionableCandidate]:
        if len(candidates) <= 1:
            return candidates

        target = self._next_rotating_market([candidate.plan.market for candidate in candidates])
        if not target:
            return candidates

        target = target.upper()
        for index, candidate in enumerate(candidates):
            if candidate.plan.market.upper() == target:
                if index == 0:
                    return candidates
                return candidates[index:] + candidates[:index]
        return candidates

    def _execute_live(
        self,
        *,
        plan: ai.AutoPilotOrderPlan,
        position_pct: float,
        last_close: float,
    ) -> Optional[AutoTraderExecution]:
        if not self._is_valid_price(plan.market, last_close):
            return None

        try:
            client = create_upbit_client_from_env()
        except ExecutionError as exc:
            self._append_log("error", str(exc))
            raise

        order_type = plan.order_type if plan.order_type in {"limit", "market"} else "market"
        price = plan.suggested_price or last_close if order_type == "limit" else None

        capital = self._config.capital if self._config else 0.0
        order_value = max(0.0, capital * position_pct)
        volume = order_value / (price or last_close)
        if volume <= 0:
            self._append_log("info", "주문 수량이 0으로 계산되어 생략합니다.")
            self._set_skip_reason("주문 수량이 0으로 계산되어 주문을 건너뜁니다.")
            return None

        client.create_order(
            market=plan.market,
            side=plan.side,
            ord_type=order_type,
            volume=volume if plan.side == "ask" or order_type == "market" else None,
            price=price if order_type == "limit" else None,
        )
        return AutoTraderExecution(
            mode=OrderMode.LIVE,
            market=plan.market,
            side=plan.side,
            price=price or last_close,
            volume=volume,
            value=volume * (price or last_close),
            executed_at=self._time_provider(),
            detail="실거래 주문 전송",
        )

    def _is_valid_price(self, market: str, price: float) -> bool:
        """Return ``True`` when ``price`` can be used for position sizing."""

        if not math.isfinite(price) or price <= 0:
            self._append_log(
                "error",
                f"{market} 시세({price})가 유효하지 않아 주문을 건너뜁니다.",
            )
            self._set_skip_reason("시세 데이터가 유효하지 않아 관망합니다.")
            return False
        if price < 1e-8:
            self._append_log(
                "warning",
                f"{market} 시세가 매우 낮아 주문 금액 계산에 주의가 필요합니다.",
            )
        return True


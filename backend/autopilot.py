"""Background auto-trading orchestration for Sado Trade Bot."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List, Optional, Tuple

from . import ai
from .execution import ExecutionError, PaperBroker, create_upbit_client_from_env
from .market import (
    MarketData,
    MarketDataError,
    fetch_authoritative_news,
    fetch_upbit_candles,
    fetch_upbit_markets,
    get_fallback_market_infos,
)
from .notifications import enqueue_digest, flush_due_digests, notify_synology_chat
from .schemas import MarketRecommendationsResponse, OrderMode
from .trading import Candle, evaluate_candles_integrity

HOLD_SIGNAL_REASON = "관망 신호: 명확한 매매 조건이 확인되지 않았습니다."

try:
    _configured_max_markets = int(os.getenv("AUTOPILOT_MAX_MARKETS_PER_CYCLE", "60"))
except ValueError:
    _configured_max_markets = 60

_AUTOPILOT_MAX_MARKETS_PER_CYCLE = max(5, min(_configured_max_markets, 200))


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
    min_confidence_pct: float = 55.0
    auto_select_market: bool = True
    recommendation_base: str = "KRW"
    recommendation_interval: str = "minute60"
    recommendation_max_markets: int = 180
    recommendation_include_warnings: bool = False


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
    ) -> None:
        self._broker = broker
        self._candle_fetcher = candle_fetcher
        self._news_fetcher = news_fetcher
        self._analyse_market = analyse_market
        self._autopilot_builder = autopilot_builder
        self._portfolio_builder = portfolio_builder
        self._time_provider = time_provider
        self._notifier = notifier
        self._recommendation_scanner = recommendation_scanner

        self._config: Optional[AutoTraderConfig] = None
        self._state = AutoTraderState()
        # Use an RLock so lifecycle hooks can append logs while holding the lock.
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle control
    # ------------------------------------------------------------------
    def start(self, config: AutoTraderConfig) -> AutoTraderState:
        """Apply configuration, execute an immediate cycle, and spawn the loop."""

        with self._lock:
            config.market = config.market.upper()
            config.recommendation_base = (config.recommendation_base or "KRW").upper()
            self._config = config
            self._state.config = config
            self._state.running = True
            self._state.last_skip_reason = None
            self._stop_event.clear()
            self._append_log("info", "자동매매 오토파일럿을 시작합니다.")
            self._safe_notify("자동매매 오토파일럿을 시작했습니다.")

        self.run_cycle()
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
            self.run_cycle()
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

        self._set_skip_reason(None)

        try:
            snapshot = self._broker.snapshot()
            news_feed = self._safe_news()
            candidate_markets = list(dict.fromkeys(candidate_markets or [market_code]))

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
            fallback_used = False

            skip_messages: List[str] = []
            best_confidence = float("-inf")

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

                if autopilot_plan.confidence_pct > reported_confidence:
                    reported_confidence = autopilot_plan.confidence_pct
                    reported_plan = autopilot_plan
                    reported_insight = insight
                    reported_portfolio = portfolio_plan
                    reported_candles = candles
                    reported_last_close = last_close
                    reported_market = candidate
                    reported_price_source = market_data.source

                skip_reason: Optional[str] = None
                if autopilot_plan.side == "ask" and not self._has_position(snapshot, autopilot_plan.market):
                    skip_reason = f"{autopilot_plan.market} 매도 신호이지만 보유 수량이 없어 다음 추천을 확인합니다."
                elif autopilot_plan.side == "flat":
                    skip_reason = f"{autopilot_plan.market} 관망 신호로 다음 후보를 탐색합니다."
                elif autopilot_plan.confidence_pct < config.min_confidence_pct:
                    skip_reason = (
                        f"{autopilot_plan.market} 신뢰도 {autopilot_plan.confidence_pct:.1f}%가 "
                        f"기준 {config.min_confidence_pct:.1f}% 미만입니다. 다음 추천을 확인합니다."
                    )

                position_pct = min(autopilot_plan.position_size_pct / 100.0, config.max_position_pct)
                if position_pct <= 0 and skip_reason is None:
                    skip_reason = f"{autopilot_plan.market} 포지션 비중이 0%로 계산되어 주문을 건너뜁니다."

                if skip_reason:
                    skip_messages.append(skip_reason)
                    self._append_log("info", skip_reason)
                    continue

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
            with self._lock:
                if self._config:
                    self._config.market = selected_market
                    self._state.config = self._config

            if len(candidate_markets) > 1:
                self._append_log(
                    "info",
                    f"총 {len(candidate_markets)}종목을 평가한 결과 {selected_market}을(를) 선택했습니다.",
                )

            if selected_price_source != "upbit" and config.mode == OrderMode.LIVE:
                self._append_log(
                    "warning",
                    f"{selected_market} 시세가 실시간 업비트가 아니므로 거래에 주의가 필요합니다. (source={selected_price_source})",
                )

            execution: Optional[AutoTraderExecution] = None
            if selected_plan_actionable:
                execution = self._maybe_execute(
                    config=config,
                    plan=selected_plan,
                    candles=selected_candles,
                    last_close=selected_last_close,
                    price_source=selected_price_source,
                )

            with self._lock:
                self._state.last_plan = selected_plan
                self._state.last_insight = selected_insight
                if execution:
                    self._state.last_execution = execution
                    self._state.executions.append(execution)
                    self._state.executions = self._state.executions[-40:]
                    self._state.last_skip_reason = None
                self._state.last_price_source = selected_price_source
                self._state.last_error = None
                self._state.last_cycle_completed_at = self._time_provider()
                interval_seconds = max(10.0, float(config.poll_interval))
                self._state.next_cycle_due_at = self._state.last_cycle_completed_at + timedelta(
                    seconds=interval_seconds
                )

            if execution:
                self._append_log(
                    "trade",
                    f"{execution.mode.upper()} {execution.market} {execution.side} {execution.volume:.6f} 실행",
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
            batch = list(candidates)
            with self._lock:
                self._state.analysis_markets = list(batch)
                self._state.analysis_market_count = len(batch)
                self._state.candidate_rotation_cursor = 0
            return batch, False

        top_candidate = candidates[0]
        pool = candidates[1:]
        cursor = 0
        if pool:
            with self._lock:
                cursor = self._state.candidate_rotation_cursor % len(pool)

        limit_after_top = max(0, limit - 1)
        selection: List[str] = []
        if pool and limit_after_top > 0:
            for offset in range(limit_after_top):
                selection.append(pool[(cursor + offset) % len(pool)])
            next_cursor = (cursor + limit_after_top) % len(pool)
        else:
            next_cursor = cursor if pool else 0

        batch = [top_candidate] + selection

        with self._lock:
            self._state.analysis_markets = list(batch)
            self._state.analysis_market_count = len(batch)
            self._state.candidate_rotation_cursor = next_cursor

        return batch, True

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
    ) -> Optional[AutoTraderExecution]:
        if plan.side == "flat":
            self._set_skip_reason(HOLD_SIGNAL_REASON)
            return None
        if plan.confidence_pct < config.min_confidence_pct:
            self._append_log("info", "신뢰도가 낮아 주문을 건너뜁니다.")
            self._set_skip_reason(
                f"신뢰도 {plan.confidence_pct:.1f}%가 기준 {config.min_confidence_pct:.1f}%보다 낮습니다."
            )
            return None

        position_pct = min(plan.position_size_pct / 100.0, config.max_position_pct)
        if position_pct <= 0:
            self._append_log("info", "포지션 비중이 0%로 계산되어 주문을 생략합니다.")
            self._set_skip_reason("포지션 비중이 0%로 계산되어 관망합니다.")
            return None

        if config.mode == OrderMode.LIVE and price_source != "upbit":
            self._append_log(
                "warning",
                f"실거래 보호: {plan.market} 시세 소스({price_source})로 인해 주문을 차단했습니다.",
            )
            self._set_skip_reason("실거래는 업비트 실시간 시세에서만 실행됩니다.")
            return None

        if config.mode == OrderMode.PAPER:
            execution = self._execute_paper(plan=plan, position_pct=position_pct, last_close=last_close)
        else:
            execution = self._execute_live(plan=plan, position_pct=position_pct, last_close=last_close)

        if execution:
            self._set_skip_reason(None)
        return execution

    def _execute_paper(
        self,
        *,
        plan: ai.AutoPilotOrderPlan,
        position_pct: float,
        last_close: float,
    ) -> Optional[AutoTraderExecution]:
        snapshot = self._broker.snapshot()
        capital = snapshot.portfolio_value
        target_value = max(0.0, capital * position_pct)

        if plan.side == "bid":
            available_cash = snapshot.cash
            fee_rate = getattr(self._broker, "fee_rate", 0.0) or 0.0
            max_affordable = available_cash / (1.0 + max(fee_rate, 0.0))
            order_value = min(max_affordable, target_value)

            if max_affordable > 0:
                safety_value = max_affordable * 0.9995
                if safety_value < order_value:
                    order_value = safety_value
            else:
                order_value = 0.0

            if order_value < last_close * 0.0001:
                self._append_log("info", "현금이 부족해 매수를 생략합니다.")
                self._set_skip_reason(
                    f"가용 현금 {available_cash:,.0f} KRW로 매수 실행이 어렵습니다."
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
                        f"가용 현금 {available_cash:,.0f} KRW로 매수 실행이 어렵습니다."
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
            self._recommendation_scanner = scanner

    # ------------------------------------------------------------------
    # Recommendation support
    # ------------------------------------------------------------------
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

        try:
            result = self._recommendation_scanner(
                (config.recommendation_base or "KRW").upper(),
                config.recommendation_interval or config.interval,
                recommendation_limit,
                scan_limit,
                bool(config.recommendation_include_warnings),
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            self._append_log("warning", f"추천 종목 분석 실패: {exc}")
            with self._lock:
                self._state.last_recommendations = []
                self._state.last_recommendation_source = None
                self._state.recommendation_markets = candidate_markets
                self._state.recommendation_market_count = len(candidate_markets)
            raise MarketDataError(str(exc)) from exc

        top_market = market_code
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
            formatted.append(" · ".join(label_parts))
            if market_name not in candidate_markets:
                candidate_markets.append(market_name)
            if formatted and top_market == market_code:
                top_market = market_name

        try:
            source = getattr(result, "analysis_source", None)
        except Exception:  # pragma: no cover - unexpected payload shape
            source = None

        try:
            errors = getattr(result, "errors", []) or []
        except Exception:  # pragma: no cover
            errors = []

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
        base_currency = (config.recommendation_base or "KRW").upper()
        if config.auto_select_market and len(deduped_candidates) < max(3, recommendation_limit):
            fallback_limit = scan_limit
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
                        f"추천 결과가 부족해 상위 {len(fallback_candidates)}종목을 후보에 추가합니다: {preview}"
                    ),
                )
                if not source:
                    source = "fallback"

        with self._lock:
            self._state.last_recommendations = formatted[:5]
            self._state.last_recommendation_source = source
            self._state.recommendation_markets = deduped_candidates[:15]
            self._state.recommendation_market_count = len(deduped_candidates)

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

        base = (base_currency or "KRW").upper()
        only_krw = False if base == "ALL" else base == "KRW"
        try:
            listing = fetch_upbit_markets(only_krw=only_krw)
        except MarketDataError as exc:
            self._append_log(
                "warning",
                f"업비트 마켓 목록을 불러오지 못해 내장 후보를 사용합니다: {exc}",
            )
            market_infos = get_fallback_market_infos(only_krw=only_krw)
        else:
            market_infos = list(listing.markets)
            if not market_infos:
                market_infos = get_fallback_market_infos(only_krw=only_krw)

        seen = {code.upper() for code in exclude}
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
            if len(fallback) >= limit:
                break
        return fallback

    def _execute_live(
        self,
        *,
        plan: ai.AutoPilotOrderPlan,
        position_pct: float,
        last_close: float,
    ) -> Optional[AutoTraderExecution]:
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


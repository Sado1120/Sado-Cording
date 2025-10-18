"""Background auto-trading orchestration for Sado Trade Bot."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, List, Optional

from . import ai
from .execution import ExecutionError, PaperBroker, create_upbit_client_from_env
from .market import MarketData, MarketDataError, fetch_authoritative_news, fetch_upbit_candles
from .notifications import notify_synology_chat
from .schemas import OrderMode
from .trading import Candle


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
    ) -> None:
        self._broker = broker
        self._candle_fetcher = candle_fetcher
        self._news_fetcher = news_fetcher
        self._analyse_market = analyse_market
        self._autopilot_builder = autopilot_builder
        self._portfolio_builder = portfolio_builder
        self._time_provider = time_provider
        self._notifier = notifier

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
            self._config = config
            self._state.config = config
            self._state.running = True
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

        if thread and thread.is_alive():
            thread.join(timeout=2)
        return self.status()

    def status(self) -> AutoTraderState:
        with self._lock:
            snapshot = AutoTraderState(
                running=self._state.running,
                config=self._state.config,
                last_plan=self._state.last_plan,
                last_insight=self._state.last_insight,
                last_execution=self._state.last_execution,
                last_error=self._state.last_error,
                last_cycle_started_at=self._state.last_cycle_started_at,
                last_cycle_completed_at=self._state.last_cycle_completed_at,
                logs=list(self._state.logs),
            )
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
            market_data = self._candle_fetcher(
                market=config.market,
                interval=config.interval,
                count=200,
            )
            candles = market_data.candles
            if not candles:
                raise MarketDataError("캔들 데이터가 비어 있습니다.")

            last_close = candles[-1].close
            news = self._safe_news()
            insight = self._analyse_market(
                candles,
                market=config.market,
                interval=config.interval,
                news=news,
            )

            portfolio_plan = None
            if config.include_portfolio:
                try:
                    portfolio_plan = self._portfolio_builder(
                        risk_appetite=config.risk_appetite,
                        capital=config.capital,
                        include_cash=True,
                        preferred_markets=[config.market],
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

            execution = self._maybe_execute(
                config=config,
                plan=autopilot_plan,
                candles=candles,
                last_close=last_close,
            )

            with self._lock:
                self._state.last_plan = autopilot_plan
                self._state.last_insight = insight
                self._state.last_execution = execution or self._state.last_execution
                self._state.last_error = None
                self._state.last_cycle_completed_at = self._time_provider()

            if execution:
                self._append_log(
                    "trade",
                    f"{execution.mode.upper()} {execution.market} {execution.side} {execution.volume:.6f} 실행",
                )
            else:
                self._append_log("info", f"{autopilot_plan.market} 분석 완료: {autopilot_plan.bias} 모드")

        except (MarketDataError, ExecutionError, ValueError) as exc:
            with self._lock:
                self._state.last_error = str(exc)
                self._state.last_cycle_completed_at = self._time_provider()
            self._append_log("error", f"자동매매 사이클 실패: {exc}")

    def _safe_news(self) -> List[dict]:
        try:
            return self._news_fetcher(4)
        except Exception:  # pragma: no cover - network failures
            return []

    def _maybe_execute(
        self,
        *,
        config: AutoTraderConfig,
        plan: ai.AutoPilotOrderPlan,
        candles: Iterable[Candle],
        last_close: float,
    ) -> Optional[AutoTraderExecution]:
        if plan.side == "flat":
            return None
        if plan.confidence_pct < config.min_confidence_pct:
            self._append_log("info", "신뢰도가 낮아 주문을 건너뜁니다.")
            return None

        position_pct = min(plan.position_size_pct / 100.0, config.max_position_pct)
        if position_pct <= 0:
            self._append_log("info", "포지션 비중이 0%로 계산되어 주문을 생략합니다.")
            return None

        if config.mode == OrderMode.PAPER:
            return self._execute_paper(plan=plan, position_pct=position_pct, last_close=last_close)
        return self._execute_live(plan=plan, position_pct=position_pct, last_close=last_close)

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
            order_value = min(available_cash, target_value)
            if order_value < last_close * 0.0001:
                self._append_log("info", "현금이 부족해 매수를 생략합니다.")
                return None
            volume = order_value / last_close
            self._broker.mark_price(market=plan.market, price=last_close)
            self._broker.submit_order(
                market=plan.market,
                side="bid",
                price=None,
                volume=volume,
                ord_type="market",
            )
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
            return None

        order_value = min(position.market_value, capital * position_pct)
        if order_value <= 0:
            self._append_log("info", "매도 대상 가치가 없습니다.")
            return None

        volume = min(position.volume, order_value / last_close)
        if volume <= 0:
            self._append_log("info", "매도 수량이 0으로 계산되어 생략합니다.")
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


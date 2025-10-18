"""FastAPI application exposing Sado Trade Bot capabilities."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import ai, trading
from .autopilot import AutoTrader, AutoTraderConfig, AutoTraderState
from .schemas import (
    CandlePayload,
    MarketAIResponse,
    LiveBalancesResponse,
    MarketCandlesResponse,
    MarketInsightsResponse,
    NewsItem,
    NewsResponse,
    CopilotRequest,
    CopilotResponse,
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
    RiskControlAdvicePayload,
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
    build_market_insights,
    fetch_authoritative_news,
    fetch_upbit_candles,
)


app = FastAPI(
    title="Sado Trade Bot API",
    description="Professional Upbit and ETF trading assistant with live analytics",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


_paper_broker: PaperBroker = paper_broker()
_auto_trader = AutoTrader(broker=_paper_broker)


def _autopilot_status_payload(state: AutoTraderState) -> AutoPilotStatusResponse:
    config_payload = None
    if state.config:
        config_payload = AutoPilotConfigPayload(
            mode=state.config.mode,
            market=state.config.market,
            interval=state.config.interval,
            risk_appetite=state.config.risk_appetite,
            capital=state.config.capital,
            poll_interval=state.config.poll_interval,
            include_portfolio=state.config.include_portfolio,
            max_position_pct=state.config.max_position_pct,
            min_confidence_pct=state.config.min_confidence_pct,
        )

    execution_payload = None
    if state.last_execution:
        execution_payload = AutoPilotExecutionPayload(
            mode=state.last_execution.mode,
            market=state.last_execution.market,
            side=state.last_execution.side,
            price=state.last_execution.price,
            volume=state.last_execution.volume,
            value=state.last_execution.value,
            executed_at=state.last_execution.executed_at,
            detail=state.last_execution.detail,
        )

    plan_payload = None
    if state.last_plan:
        plan_payload = AutoPilotPlanPayload(
            market=state.last_plan.market,
            side=state.last_plan.side,
            bias=state.last_plan.bias,
            order_type=state.last_plan.order_type,
            suggested_price=state.last_plan.suggested_price,
            position_size_pct=state.last_plan.position_size_pct,
            stop_loss_pct=state.last_plan.stop_loss_pct,
            take_profit_pct=state.last_plan.take_profit_pct,
            trailing_stop_pct=state.last_plan.trailing_stop_pct,
            confidence_pct=state.last_plan.confidence_pct,
            reasoning=state.last_plan.reasoning,
            monitoring=state.last_plan.monitoring,
        )

    logs = [
        AutoPilotLogEntryPayload(
            timestamp=entry.timestamp,
            level=entry.level,
            message=entry.message,
        )
        for entry in state.logs
    ]

    return AutoPilotStatusResponse(
        running=state.running,
        config=config_payload,
        last_plan=plan_payload,
        last_execution=execution_payload,
        last_error=state.last_error,
        last_cycle_started_at=state.last_cycle_started_at,
        last_cycle_completed_at=state.last_cycle_completed_at,
        logs=logs,
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
    )


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
        executed_at=order.executed_at,
    )


def _serialize_balance(snapshot) -> PaperBalancePayload:
    return PaperBalancePayload(
        cash=snapshot.cash,
        portfolio_value=snapshot.portfolio_value,
        last_updated=snapshot.last_update,
        positions=[_serialize_position(pos) for pos in snapshot.positions],
        orders=[_serialize_order(order) for order in snapshot.orders],
    )


@app.get("/trading/autopilot/status", response_model=AutoPilotStatusResponse)
def get_autopilot_status() -> AutoPilotStatusResponse:
    state = _auto_trader.status()
    return _autopilot_status_payload(state)


@app.post("/trading/autopilot/start", response_model=AutoPilotStatusResponse)
def start_autopilot(payload: AutoPilotConfigRequest) -> AutoPilotStatusResponse:
    state = _auto_trader.start(_build_autopilot_config(payload))
    return _autopilot_status_payload(state)


@app.post("/trading/autopilot/stop", response_model=AutoPilotStatusResponse)
def stop_autopilot() -> AutoPilotStatusResponse:
    state = _auto_trader.stop()
    return _autopilot_status_payload(state)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/strategies/simulate", response_model=SimulationResponse)
def simulate_strategy(payload: SimulationRequest) -> SimulationResponse:
    candles = (
        [
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
        if payload.prices
        else trading.generate_synthetic_prices(seed=payload.seed)
    )

    try:
        report = trading.run_ema_strategy(
            candles,
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
        )
        for trade in report.trades
    ]

    trade_summary = trading.summarize_trades(report.trades)

    return SimulationResponse(
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
    )


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

    return MarketCandlesResponse(
        market=market.upper(),
        interval=interval,
        source=data.source,
        candles=candles,
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

    return MarketInsightsResponse(source=data.source, **insights)


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

    return MarketAIResponse(
        market=insight.market,
        interval=insight.interval,
        regime=insight.regime,
        recommended_action=insight.recommended_action,
        confidence_pct=insight.confidence_pct,
        summary=insight.summary,
        signals=insight.signals,
        metrics=insight.metrics.__dict__,
        risk=insight.risk.__dict__,
        generated_at=insight.generated_at,
        news=_news_items(insight.news),
    )


@app.get("/market/news", response_model=NewsResponse)
def get_news(limit: int = 8) -> NewsResponse:
    items = [
        NewsItem(**entry)
        for entry in fetch_authoritative_news(limit=limit)
    ]
    return NewsResponse(generated_at=datetime.utcnow(), items=items)


def _paper_status_response() -> PaperStatusResponse:
    snapshot = _paper_broker.snapshot()
    balance = _serialize_balance(snapshot)
    return PaperStatusResponse(**balance.dict())


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

    return PortfolioOptimizationResponse(
        generated_at=plan.generated_at,
        risk_profile_label=plan.risk_profile_label,
        risk_appetite=plan.risk_appetite,
        expected_return_pct=plan.expected_return_pct,
        expected_volatility_pct=plan.expected_volatility_pct,
        sharpe_estimate=plan.sharpe_estimate,
        diversification_score_pct=plan.diversification_score_pct,
        tail_risk_guard_pct=plan.tail_risk_guard_pct,
        allocations=[allocation.__dict__ for allocation in plan.allocations],
        hedging_notes=plan.hedging_notes,
        methodology=plan.methodology,
        market_briefings=plan.market_briefings,
    )


@app.post("/ai/copilot", response_model=CopilotResponse)
def run_ai_copilot(payload: CopilotRequest) -> CopilotResponse:
    market = payload.market.upper()
    interval = payload.interval

    try:
        data = fetch_upbit_candles(market=market, interval=interval, count=220)
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    headlines = fetch_authoritative_news(limit=6)

    try:
        insight = ai.analyse_market(
            data.candles,
            market=market,
            interval=interval,
            news=headlines,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    portfolio_plan: Optional[ai.PortfolioAIPlan] = None
    if payload.include_portfolio:

        def _fetch(market_code: str, fetch_interval: str, count: int):
            fetched = fetch_upbit_candles(
                market=market_code,
                interval=fetch_interval,
                count=count,
            )
            return fetched.candles

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

    autopilot = ai.craft_autopilot_plan(
        insight=insight,
        risk_appetite=payload.risk_appetite,
        capital=payload.capital,
        mode=payload.mode.value,
        portfolio_plan=portfolio_plan,
    )

    synthesis = ai.generate_copilot_synthesis(
        question=payload.question,
        insight=insight,
        autopilot=autopilot,
        portfolio_plan=portfolio_plan,
        mode=payload.mode.value,
    )

    autopilot_payload = AutoPilotPlanPayload(
        market=autopilot.market,
        side=autopilot.side,
        bias=autopilot.bias,
        order_type=autopilot.order_type,
        suggested_price=autopilot.suggested_price,
        position_size_pct=autopilot.position_size_pct,
        stop_loss_pct=autopilot.stop_loss_pct,
        take_profit_pct=autopilot.take_profit_pct,
        trailing_stop_pct=autopilot.trailing_stop_pct,
        confidence_pct=autopilot.confidence_pct,
        reasoning=autopilot.reasoning,
        monitoring=autopilot.monitoring,
    )

    insight_payload = MarketAIResponse(
        market=insight.market,
        interval=insight.interval,
        regime=insight.regime,
        recommended_action=insight.recommended_action,
        confidence_pct=insight.confidence_pct,
        summary=insight.summary,
        signals=insight.signals,
        metrics=MarketAIMetricsPayload(**insight.metrics.__dict__),
        risk=RiskControlAdvicePayload(**insight.risk.__dict__),
        generated_at=insight.generated_at,
        news=_news_items(insight.news),
    )

    return CopilotResponse(
        generated_at=synthesis.generated_at,
        answer=synthesis.answer,
        summary_points=synthesis.summary_points,
        risk_notices=synthesis.risk_notices,
        action_items=synthesis.action_items,
        highlights=synthesis.highlights,
        autopilot=autopilot_payload,
        insight=insight_payload,
        news=_news_items(insight.news),
    )


@app.post("/trading/order", response_model=OrderResponse)
def submit_order(payload: OrderRequest) -> OrderResponse:
    if payload.mode is OrderMode.PAPER:
        try:
            snapshot = _paper_broker.submit_order(
                market=payload.market,
                side=payload.side,
                price=payload.price,
                volume=payload.volume,
                ord_type=payload.ord_type,
            )
        except ExecutionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        balance = _serialize_balance(snapshot)
        latest_order = balance.orders[0] if balance.orders else None
        return OrderResponse(
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

    return OrderResponse(
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


@app.get("/trading/paper/status", response_model=PaperStatusResponse)
def get_paper_status() -> PaperStatusResponse:
    return _paper_status_response()


@app.post("/trading/paper/reset", response_model=PaperStatusResponse)
def reset_paper(payload: PaperResetRequest) -> PaperStatusResponse:
    _paper_broker.reset(initial_cash=payload.initial_cash)
    return _paper_status_response()


@app.post("/trading/paper/mark", response_model=PaperStatusResponse)
def mark_paper(payload: PaperMarkRequest) -> PaperStatusResponse:
    try:
        _paper_broker.mark_price(market=payload.market, price=payload.price)
    except ExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _paper_status_response()


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

    return RebalanceResponse(orders=orders)


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

    return PortfolioBlueprintResponse(**plan)


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

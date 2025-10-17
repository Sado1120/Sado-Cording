"""FastAPI application exposing ILJIN Copilot capabilities."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import trading
from .schemas import (
    CandlePayload,
    LiveBalancesResponse,
    OrderMode,
    OrderRequest,
    OrderResponse,
    PortfolioBlueprintRequest,
    PortfolioBlueprintResponse,
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
)
from .execution import (
    ExecutionError,
    PaperOrder,
    PaperPosition,
    PaperBroker,
    create_upbit_client_from_env,
    paper_broker,
)


app = FastAPI(
    title="ILJIN Copilot API",
    description="Automated trading research assistant for digital assets and ETFs",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",
        "http://127.0.0.1:8501",
        "http://0.0.0.0:8501",
        "https://localhost:8501",
        "https://127.0.0.1:8501",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_paper_broker: PaperBroker = paper_broker()


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
        positions=[_serialize_position(pos) for pos in snapshot.positions],
        orders=[_serialize_order(order) for order in snapshot.orders],
    )


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


def _paper_status_response() -> PaperStatusResponse:
    snapshot = _paper_broker.snapshot()
    balance = _serialize_balance(snapshot)
    return PaperStatusResponse(**balance.dict())


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

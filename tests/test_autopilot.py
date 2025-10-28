from datetime import datetime, timedelta, timezone

import pytest

try:
    from fastapi.testclient import TestClient
except RuntimeError:  # pragma: no cover - optional dependency missing
    TestClient = None
except ModuleNotFoundError:  # pragma: no cover - optional dependency missing
    TestClient = None

from backend import ai
from backend.autopilot import AutoTrader, AutoTraderConfig, AutoTraderExecution
import backend.autopilot as autopilot_module
from backend.execution import PaperBroker
from backend.market import (
    MarketData,
    MarketInfo,
    MarketList,
    MarketDataError,
    reset_market_state_for_tests,
)
from backend.schemas import (
    MarketRecommendationPayload,
    MarketRecommendationsResponse,
    OrderMode,
)
from backend.trading import Candle

import backend.app as app_module
from types import SimpleNamespace


def _make_market_data(count: int = 120) -> MarketData:
    base_time = datetime.now(timezone.utc) - timedelta(minutes=count)
    candles = []
    price = 10_000_000.0
    for index in range(count):
        timestamp = base_time + timedelta(minutes=index)
        close = price + index * 12_000
        candles.append(
            Candle(
                timestamp=timestamp,
                open=close - 8_000,
                high=close + 9_000,
                low=close - 12_000,
                close=close,
                volume=1.5 + index * 0.01,
            )
        )
    return MarketData(candles=candles, source="synthetic")


def _fake_candle_fetcher(*, market: str, interval: str, count: int) -> MarketData:
    return _make_market_data(count)


def _fake_news_fetcher(limit: int) -> list[dict]:
    return []


def _fake_autopilot_builder(**kwargs) -> ai.AutoPilotOrderPlan:
    insight = kwargs["insight"]
    return ai.AutoPilotOrderPlan(
        market=insight.market,
        side="bid",
        bias="long",
        order_type="market",
        suggested_price=None,
        position_size_pct=5.0,
        stop_loss_pct=2.0,
        take_profit_pct=4.0,
        trailing_stop_pct=1.5,
        confidence_pct=70.0,
        reasoning=["테스트 환경 자동매수"],
        monitoring=["가격 추세 감시"],
    )


def _make_recommendation_payload(market: str) -> MarketRecommendationPayload:
    return MarketRecommendationPayload(
        market=market,
        korean_name=market,
        english_name=market,
        base_currency="KRW",
        quote_currency=market.split("-")[-1],
        score=78.5,
        confidence_pct=70.0,
        regime="상승",
        recommended_action="bid",
        last_price=1_000_000.0,
        price_change_pct=2.5,
        trend_strength_pct=62.0,
        volatility_pct=35.0,
        institutional_sentiment_pct=58.0,
        breakout_probability_pct=41.0,
        summary="테스트 추천",
        reason="테스트 이유",
        source="synthetic",
    )


def _make_recommendation_response(markets: list[str]) -> MarketRecommendationsResponse:
    payloads = [_make_recommendation_payload(market) for market in markets]
    return MarketRecommendationsResponse(
        generated_at=datetime.now(timezone.utc),
        interval="minute60",
        base_currency="KRW",
        limit=len(payloads) or 1,
        analysed_markets=len(payloads),
        analysis_duration_ms=123.0,
        analysis_source="synthetic",
        recommendations=payloads,
        errors=[],
    )


def _build_trader(
    notifier=lambda _message: True,
    recommendation_scanner=None,
    autopilot_builder=_fake_autopilot_builder,
    analyse_market=ai.analyse_market,
) -> tuple[AutoTrader, PaperBroker]:
    broker = PaperBroker()
    trader = AutoTrader(
        broker=broker,
        candle_fetcher=_fake_candle_fetcher,
        news_fetcher=_fake_news_fetcher,
        analyse_market=analyse_market,
        autopilot_builder=autopilot_builder,
        portfolio_builder=lambda **_: None,
        time_provider=lambda: datetime.now(timezone.utc),
        notifier=notifier,
        recommendation_scanner=recommendation_scanner,
    )
    return trader, broker


def test_autotrader_runs_single_cycle_and_places_order():
    trader, broker = _build_trader()
    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.6,
        capital=20_000_000,
        poll_interval=600.0,
        include_portfolio=False,
        max_position_pct=0.2,
        min_confidence_pct=50.0,
    )

    state = trader.start(config)
    try:
        assert state.running is True
        assert state.last_plan is not None
        assert state.last_plan.side == "bid"
        assert state.last_execution is not None
        assert state.last_execution.side == "bid"
        assert state.logs, "오토파일럿 로그가 비어 있습니다."
        assert state.next_cycle_due_at is not None
        assert state.last_skip_reason is None

        snapshot = broker.snapshot()
        assert any(pos.market == "KRW-BTC" for pos in snapshot.positions)
    finally:
        stop_state = trader.stop()
        assert stop_state.running is False


def test_analysis_batch_rotates_through_candidates():
    trader, _ = _build_trader()
    candidates = [f"KRW-TEST{i:03d}" for i in range(1, 101)]

    batch1, truncated1 = trader._select_analysis_batch(candidates)
    assert truncated1 is True
    assert len(batch1) == autopilot_module._AUTOPILOT_MAX_MARKETS_PER_CYCLE
    assert batch1 == candidates[: autopilot_module._AUTOPILOT_MAX_MARKETS_PER_CYCLE]

    batch2, truncated2 = trader._select_analysis_batch(candidates)
    assert truncated2 is True
    assert len(batch2) == autopilot_module._AUTOPILOT_MAX_MARKETS_PER_CYCLE
    expected_start = autopilot_module._AUTOPILOT_MAX_MARKETS_PER_CYCLE
    assert batch2[0] == candidates[expected_start]
    assert trader._state.candidate_rotation_cursor != 0


def test_autopilot_forces_market_rotation_after_repeats():
    rotation_candidates = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-ADA", "KRW-SOL"]

    def recommendation_scanner(*_args, **_kwargs):
        return _make_recommendation_response(rotation_candidates)

    def analyse_market_stub(_candles, *, market: str, interval: str, news: list):
        return SimpleNamespace(market=market)

    confidence_map = {
        "KRW-BTC": 78.0,
        "KRW-ETH": 72.0,
        "KRW-XRP": 71.0,
        "KRW-ADA": 69.0,
        "KRW-SOL": 68.0,
    }

    def plan_builder(**kwargs):
        market = kwargs["insight"].market
        confidence = confidence_map.get(market, 60.0)
        return ai.AutoPilotOrderPlan(
            market=market,
            side="bid",
            bias="long",
            order_type="market",
            suggested_price=None,
            position_size_pct=5.0,
            stop_loss_pct=2.0,
            take_profit_pct=4.0,
            trailing_stop_pct=1.0,
            confidence_pct=confidence,
            reasoning=[f"{market} 테스트 신호"],
            monitoring=["테스트 모니터링"],
        )

    trader, _ = _build_trader(
        recommendation_scanner=recommendation_scanner,
        autopilot_builder=plan_builder,
        analyse_market=analyse_market_stub,
    )

    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.6,
        capital=30_000_000,
        poll_interval=180.0,
        include_portfolio=False,
        max_position_pct=0.2,
        min_confidence_pct=60.0,
        recommendation_max_markets=len(rotation_candidates),
    )

    trader._config = config
    trader._state.config = config
    trader._state.running = True

    trader.run_cycle()
    assert trader._state.last_plan is not None
    assert trader._state.last_plan.market == "KRW-BTC"
    assert trader._state.repeat_market_count == 1

    trader.run_cycle()
    trader.run_cycle()
    assert trader._state.repeat_market_count >= 3

    trader.run_cycle()
    assert trader._state.last_plan is not None
    assert trader._state.last_plan.market in {"KRW-ETH", "KRW-XRP"}
    assert trader._state.repeat_market_count == 1


def test_autopilot_rotates_even_with_lower_confidence_alternate():
    candidates = ["KRW-BTC", "KRW-ETH"]

    def recommendation_scanner(*_args, **_kwargs):
        return _make_recommendation_response(candidates)

    def analyse_market_stub(_candles, *, market: str, interval: str, news: list):
        return SimpleNamespace(market=market)

    confidence_map = {"KRW-BTC": 82.0, "KRW-ETH": 44.0}

    def plan_builder(**kwargs):
        market = kwargs["insight"].market
        return ai.AutoPilotOrderPlan(
            market=market,
            side="bid",
            bias="long",
            order_type="market",
            suggested_price=None,
            position_size_pct=4.0,
            stop_loss_pct=1.8,
            take_profit_pct=3.6,
            trailing_stop_pct=1.0,
            confidence_pct=confidence_map.get(market, 40.0),
            reasoning=[f"{market} 회전 테스트"],
            monitoring=["회전 보정"],
        )

    trader, _ = _build_trader(
        recommendation_scanner=recommendation_scanner,
        autopilot_builder=plan_builder,
        analyse_market=analyse_market_stub,
    )

    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.6,
        capital=25_000_000,
        poll_interval=180.0,
        include_portfolio=False,
        max_position_pct=0.25,
        min_confidence_pct=40.0,
        recommendation_max_markets=len(candidates),
    )

    trader._config = config
    trader._state.config = config
    trader._state.running = True

    trader.run_cycle()
    trader.run_cycle()
    trader.run_cycle()
    assert trader._state.repeat_market_count >= autopilot_module._REPEAT_MARKET_ROTATION_THRESHOLD

    trader.run_cycle()
    assert trader._state.last_plan is not None
    assert trader._state.last_plan.market == "KRW-ETH"
    assert trader._state.repeat_market_count == 1


def test_autopilot_executes_diversified_trades_when_enabled():
    markets = ["KRW-BTC", "KRW-ETH", "KRW-SOL"]

    def recommendation_scanner(*_args, **_kwargs):
        return _make_recommendation_response(markets)

    def analyse_market_stub(_candles, *, market: str, interval: str, news: list):
        return SimpleNamespace(market=market)

    def plan_builder(**kwargs):
        market = kwargs["insight"].market
        return ai.AutoPilotOrderPlan(
            market=market,
            side="bid",
            bias="long",
            order_type="market",
            suggested_price=None,
            position_size_pct=6.0,
            stop_loss_pct=2.0,
            take_profit_pct=4.5,
            trailing_stop_pct=1.2,
            confidence_pct=75.0,
            reasoning=[f"{market} 테스트 분산"],
            monitoring=["테스트 분산 실행"],
        )

    trader, broker = _build_trader(
        recommendation_scanner=recommendation_scanner,
        autopilot_builder=plan_builder,
        analyse_market=analyse_market_stub,
    )

    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.6,
        capital=40_000_000,
        poll_interval=180.0,
        include_portfolio=False,
        max_position_pct=0.3,
        min_confidence_pct=60.0,
        recommendation_max_markets=len(markets),
        max_trades_per_cycle=2,
    )

    state = trader.start(config)
    try:
        executed = state.executions
        assert len(executed) >= 2
        traded_markets = {item.market for item in executed[-2:]}
        assert len(traded_markets) >= 2
        snapshot = broker.snapshot()
        held_markets = {pos.market for pos in snapshot.positions}
        assert traded_markets.issubset(held_markets)
    finally:
        trader.stop()


def test_autotrader_handles_cash_rounding_without_failure():
    broker = PaperBroker(fee_rate=0.003, initial_cash=120_000.0)

    def aggressive_plan_builder(**kwargs) -> ai.AutoPilotOrderPlan:
        insight = kwargs["insight"]
        return ai.AutoPilotOrderPlan(
            market=insight.market,
            side="bid",
            bias="long",
            order_type="market",
            suggested_price=None,
            position_size_pct=65.0,
            stop_loss_pct=3.0,
            take_profit_pct=6.0,
            trailing_stop_pct=2.0,
            confidence_pct=72.0,
            reasoning=["현금 한계 테스트"],
            monitoring=["현금 대비 주문 규모 점검"],
        )

    trader = AutoTrader(
        broker=broker,
        candle_fetcher=_fake_candle_fetcher,
        news_fetcher=_fake_news_fetcher,
        analyse_market=ai.analyse_market,
        autopilot_builder=aggressive_plan_builder,
        portfolio_builder=lambda **_: None,
        time_provider=lambda: datetime.now(timezone.utc),
        notifier=lambda _message: True,
        recommendation_scanner=None,
    )

    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.7,
        capital=broker.initial_cash,
        poll_interval=180.0,
        include_portfolio=False,
        max_position_pct=0.9,
        min_confidence_pct=55.0,
    )

    state = trader.start(config)
    try:
        assert state.last_error is None
        assert state.last_execution is not None
        assert state.last_execution.side == "bid"
        # 주문 값이 가용 현금 범위 내에서 체결되었는지 확인한다.
        snapshot = broker.snapshot()
        assert snapshot.cash < broker.initial_cash
        assert any(pos.market == "KRW-BTC" for pos in snapshot.positions)
    finally:
        trader.stop()


def test_autotrader_rebalances_when_cash_short():
    broker = PaperBroker(initial_cash=20_000_000.0)

    def confident_plan_builder(**kwargs) -> ai.AutoPilotOrderPlan:
        insight = kwargs["insight"]
        return ai.AutoPilotOrderPlan(
            market=insight.market,
            side="bid",
            bias="long",
            order_type="market",
            suggested_price=None,
            position_size_pct=65.0,
            stop_loss_pct=2.5,
            take_profit_pct=5.0,
            trailing_stop_pct=1.5,
            confidence_pct=78.0,
            reasoning=["재배분 테스트"],
            monitoring=["현금 확보 필요"],
        )

    trader = AutoTrader(
        broker=broker,
        candle_fetcher=_fake_candle_fetcher,
        news_fetcher=_fake_news_fetcher,
        analyse_market=ai.analyse_market,
        autopilot_builder=confident_plan_builder,
        portfolio_builder=lambda **_: None,
        time_provider=lambda: datetime.now(timezone.utc),
        notifier=lambda _message: True,
        recommendation_scanner=None,
    )

    # 소액의 현금만 남도록 기존 포지션을 매수해 둔다.
    broker.mark_price(market="KRW-ETH", price=1_200_000.0)
    broker.submit_order(market="KRW-ETH", side="bid", price=1_200_000.0, volume=15.0)

    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.65,
        capital=broker.initial_cash,
        poll_interval=240.0,
        include_portfolio=False,
        max_position_pct=0.8,
        min_confidence_pct=55.0,
    )

    state = trader.start(config)
    try:
        snapshot = broker.snapshot()
        assert any(pos.market == "KRW-BTC" for pos in snapshot.positions), "재배분 후 신규 포지션이 없습니다."
        assert any("재배분" in log.message for log in state.logs), "재배분 로그가 기록되지 않았습니다."
    finally:
        trader.stop()


def test_autotrader_rotates_to_alternate_market_when_recent_market_matches():
    reset_market_state_for_tests()
    recommendations = _make_recommendation_response(["KRW-BTC", "KRW-ETH"])

    def recommendation_scanner(base, interval, limit, max_markets, include_warnings):
        return recommendations

    trader, broker = _build_trader(recommendation_scanner=recommendation_scanner)

    previous_execution = AutoTraderExecution(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        side="bid",
        price=28_000_000.0,
        volume=0.01,
        value=280_000.0,
        executed_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        detail="이전 테스트 체결",
    )

    with trader._lock:  # type: ignore[attr-defined]
        trader._state.last_execution = previous_execution

    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.6,
        capital=20_000_000,
        poll_interval=300.0,
        include_portfolio=False,
        max_position_pct=0.3,
        min_confidence_pct=50.0,
        auto_select_market=True,
        recommendation_max_markets=10,
    )

    state = trader.start(config)
    try:
        assert state.last_plan is not None
        assert state.last_plan.market == "KRW-ETH"
        assert state.last_execution is not None
        assert state.last_execution.market == "KRW-ETH"
    finally:
        trader.stop()


def test_autotrader_live_waits_when_upbit_down(monkeypatch):
    import backend.market as market_module

    down_state = {
        "status": "down",
        "message": "업비트 연결 실패",
        "detail": "네트워크 장애",
        "checked_at": datetime.now(timezone.utc),
        "backoff_seconds_remaining": 42.0,
    }

    monkeypatch.setattr(market_module, "is_upbit_network_operational", lambda: False)
    monkeypatch.setattr(market_module, "get_upbit_network_state", lambda: down_state)

    fetch_called = False

    def forbidden_fetcher(**_kwargs):
        nonlocal fetch_called
        fetch_called = True
        raise AssertionError("network guard should prevent candle fetch")

    broker = PaperBroker()
    trader = AutoTrader(
        broker=broker,
        candle_fetcher=forbidden_fetcher,
        news_fetcher=_fake_news_fetcher,
        analyse_market=ai.analyse_market,
        autopilot_builder=_fake_autopilot_builder,
        portfolio_builder=lambda **_: None,
        time_provider=lambda: datetime.now(timezone.utc),
        notifier=lambda _message: True,
        recommendation_scanner=None,
    )

    monkeypatch.setattr(trader, "_ensure_thread", lambda: None)

    config = AutoTraderConfig(
        mode=OrderMode.LIVE,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.6,
        capital=20_000_000,
        poll_interval=600.0,
        include_portfolio=False,
        max_position_pct=0.2,
        min_confidence_pct=50.0,
    )

    state = trader.start(config)
    try:
        assert fetch_called is False
        assert state.last_plan is None
        assert state.last_skip_reason is not None
        assert "업비트" in state.last_skip_reason
        assert any("업비트" in entry.message for entry in state.logs)
    finally:
        trader.stop()


def test_autotrader_notifier_invoked():
    notifications: list[str] = []

    def notifier(message: str) -> bool:
        notifications.append(message)
        return True

    trader, _ = _build_trader(notifier=notifier)
    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.6,
        capital=15_000_000,
        poll_interval=300.0,
        include_portfolio=False,
        max_position_pct=0.25,
        min_confidence_pct=50.0,
    )

    trader.start(config)
    try:
        assert notifications, "알림이 호출되지 않았습니다."
        assert any("Sado Trade Bot" in message for message in notifications)
        status = trader.status()
        assert status.last_skip_reason is None
    finally:
        trader.stop()


def test_autotrader_uses_fallback_catalog_when_directory_unavailable(monkeypatch):
    fallback_response = SimpleNamespace(
        recommendations=[],
        analysis_source="fallback",
        errors=[],
    )

    def scanner(base, interval, limit, max_markets, include_warnings):
        return fallback_response

    trader, _ = _build_trader(recommendation_scanner=scanner)

    def failing_directory(**_kwargs):
        raise MarketDataError("directory unavailable")

    monkeypatch.setattr(autopilot_module, "fetch_upbit_markets", failing_directory)

    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.55,
        capital=12_000_000,
        poll_interval=180.0,
        include_portfolio=False,
        max_position_pct=0.3,
        min_confidence_pct=45.0,
        auto_select_market=True,
        recommendation_max_markets=20,
    )

    state = trader.start(config)
    try:
        assert state.recommendation_market_count > 1
        assert len(state.recommendation_markets) > 1
        assert state.analysis_market_count >= 1
        assert state.analysis_markets, "분석 대상이 기록되지 않았습니다."
    finally:
        trader.stop()


def test_autotrader_blocks_live_when_price_not_upbit():
    reset_market_state_for_tests()
    trader, _ = _build_trader()
    config = AutoTraderConfig(
        mode=OrderMode.LIVE,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.5,
        capital=10_000_000,
        poll_interval=120.0,
        include_portfolio=False,
        max_position_pct=0.2,
        min_confidence_pct=50.0,
    )

    state = trader.start(config)
    try:
        assert state.last_execution is None
        assert state.last_price_source == "synthetic"
        assert state.last_skip_reason is not None
        assert "업비트" in state.last_skip_reason
        assert any("실거래 보호" in log.message for log in state.logs)
    finally:
        trader.stop()


def test_autotrader_momentum_fallback_converts_flat_signal():
    def flat_plan_builder(**kwargs) -> ai.AutoPilotOrderPlan:
        insight = kwargs["insight"]
        return ai.AutoPilotOrderPlan(
            market=insight.market,
            side="flat",
            bias="neutral",
            order_type="monitor",
            suggested_price=None,
            position_size_pct=0.0,
            stop_loss_pct=0.0,
            take_profit_pct=0.0,
            trailing_stop_pct=None,
            confidence_pct=42.0,
            reasoning=["관망 테스트"],
            monitoring=["추세 재확인 필요"],
        )

    trader, _ = _build_trader(autopilot_builder=flat_plan_builder)
    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.5,
        capital=10_000_000,
        poll_interval=180.0,
        include_portfolio=False,
        max_position_pct=0.2,
        min_confidence_pct=60.0,
    )

    state = trader.start(config)
    try:
        assert state.last_plan is not None
        assert state.last_plan.side == "bid"
        assert state.last_execution is not None
        assert state.last_skip_reason is None
        assert any("모멘텀" in log.message for log in state.logs)
    finally:
        trader.stop()


def test_autotrader_records_skip_reason_when_fallback_unavailable():
    def flat_plan_builder(**kwargs) -> ai.AutoPilotOrderPlan:
        insight = kwargs["insight"]
        return ai.AutoPilotOrderPlan(
            market=insight.market,
            side="flat",
            bias="neutral",
            order_type="monitor",
            suggested_price=None,
            position_size_pct=0.0,
            stop_loss_pct=0.0,
            take_profit_pct=0.0,
            trailing_stop_pct=None,
            confidence_pct=42.0,
            reasoning=["관망 테스트"],
            monitoring=["추세 재확인 필요"],
        )

    def tiny_candle_fetcher(*, market: str, interval: str, count: int) -> MarketData:
        return _make_market_data(15)

    trader, _ = _build_trader(
        autopilot_builder=flat_plan_builder,
        analyse_market=ai.analyse_market,
    )
    trader._candle_fetcher = tiny_candle_fetcher  # type: ignore[attr-defined]

    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.5,
        capital=10_000_000,
        poll_interval=180.0,
        include_portfolio=False,
        max_position_pct=0.2,
        min_confidence_pct=60.0,
    )

    state = trader.start(config)
    try:
        assert state.last_execution is None
        if state.last_plan is not None:
            assert state.last_plan.side == "flat"
        assert state.last_skip_reason is not None
        assert "관망" in state.last_skip_reason
    finally:
        trader.stop()


def test_autotrader_tries_alternative_market_when_first_is_untradeable():
    class DummyRecommendations:
        def __init__(self, markets: list[str]):
            self.recommendations = [
                SimpleNamespace(
                    market=code,
                    recommended_action="추세 추종 매수",
                    confidence_pct=72.0,
                    score=90.0,
                )
                for code in markets
            ]
            self.analysis_source = "unit-test"
            self.errors = []

    def scanner(_base, _interval, _limit, _max_markets, _include_warnings):
        return DummyRecommendations(["KRW-BTC", "KRW-ETH"])

    def builder(**kwargs) -> ai.AutoPilotOrderPlan:
        insight = kwargs["insight"]
        if insight.market == "KRW-BTC":
            side = "ask"
            bias = "short"
        else:
            side = "bid"
            bias = "long"
        return ai.AutoPilotOrderPlan(
            market=insight.market,
            side=side,
            bias=bias,
            order_type="market",
            suggested_price=None,
            position_size_pct=5.0,
            stop_loss_pct=2.0,
            take_profit_pct=4.0,
            trailing_stop_pct=1.0,
            confidence_pct=70.0,
            reasoning=["테스트 계획"],
            monitoring=["테스트 모니터링"],
        )

    trader, broker = _build_trader(recommendation_scanner=scanner, autopilot_builder=builder)
    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.6,
        capital=20_000_000,
        poll_interval=300.0,
        include_portfolio=False,
        max_position_pct=0.25,
        min_confidence_pct=50.0,
        auto_select_market=True,
    )

    state = trader.start(config)
    try:
        assert state.last_plan is not None
        assert state.last_plan.side == "bid"
        assert state.last_plan.market == "KRW-ETH"
        assert state.last_skip_reason is None

        snapshot = broker.snapshot()
        assert any(pos.market == "KRW-ETH" for pos in snapshot.positions)
    finally:
        trader.stop()


def test_autotrader_auto_select_market_switches_market():
    class DummyRecommendations:
        def __init__(self, markets: list[str]):
            self.recommendations = [
                SimpleNamespace(
                    market=code,
                    recommended_action="추세 추종 매수",
                    confidence_pct=72.5,
                    score=91.4,
                )
                for code in markets
            ]
            self.analysis_source = "synthetic"
            self.errors: list[str] = []

    def recommendation_scanner(base, interval, limit, max_markets, include_warnings):
        assert base == "KRW"
        assert interval == "minute60"
        assert max_markets >= 10
        return DummyRecommendations(["KRW-ETH", "KRW-SOL"])

    trader, broker = _build_trader(recommendation_scanner=recommendation_scanner)
    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.6,
        capital=25_000_000,
        poll_interval=120.0,
        include_portfolio=False,
        max_position_pct=0.2,
        min_confidence_pct=50.0,
        auto_select_market=True,
        recommendation_base="KRW",
        recommendation_interval="minute60",
        recommendation_max_markets=30,
        recommendation_include_warnings=False,
    )

    state = trader.start(config)
    try:
        assert state.config is not None
        assert state.last_plan is not None
        assert state.config.market == state.last_plan.market
        assert state.config.market in {"KRW-ETH", "KRW-SOL"}
        snapshot = broker.snapshot()
        assert any(pos.market == state.config.market for pos in snapshot.positions)
        assert state.last_recommendations, "추천 요약이 비어 있습니다."
        assert state.last_recommendations[0].startswith("KRW-ETH")
    finally:
        trader.stop()


def test_autotrader_uses_fallback_markets_when_recommendations_empty(monkeypatch):
    class EmptyRecommendations:
        def __init__(self):
            self.recommendations: list = []
            self.analysis_source = ""
            self.errors: list[str] = []

    fallback_listing = MarketList(
        markets=[
            MarketInfo(
                market="KRW-BTC",
                korean_name="비트코인",
                english_name="Bitcoin",
                base_currency="KRW",
                quote_currency="BTC",
                market_warning="NONE",
                trading_suspended=False,
            ),
            MarketInfo(
                market="KRW-ETH",
                korean_name="이더리움",
                english_name="Ethereum",
                base_currency="KRW",
                quote_currency="ETH",
                market_warning="NONE",
                trading_suspended=False,
            ),
            MarketInfo(
                market="KRW-SOL",
                korean_name="솔라나",
                english_name="Solana",
                base_currency="KRW",
                quote_currency="SOL",
                market_warning="NONE",
                trading_suspended=False,
            ),
        ],
        source="fallback",
        status="up",
        message="fallback",
    )

    def empty_scanner(_base, _interval, _limit, _max_markets, _include_warnings):
        return EmptyRecommendations()

    monkeypatch.setattr(
        "backend.autopilot.fetch_upbit_markets",
        lambda only_krw=True: fallback_listing,
    )

    trader, broker = _build_trader(recommendation_scanner=empty_scanner)
    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.6,
        capital=18_000_000,
        poll_interval=180.0,
        include_portfolio=False,
        max_position_pct=0.2,
        min_confidence_pct=50.0,
        auto_select_market=True,
    )

    state = trader.start(config)
    try:
        assert state.recommendation_markets
        assert state.recommendation_market_count >= len(state.recommendation_markets)
        assert state.recommendation_market_count >= 2
        assert state.last_recommendation_source in {"", "fallback"}
        assert any("추가" in entry.message for entry in state.logs)

        snapshot = broker.snapshot()
        assert any(pos.market in {"KRW-BTC", "KRW-ETH", "KRW-SOL"} for pos in snapshot.positions)
    finally:
        trader.stop()


def test_autopilot_api_endpoints(monkeypatch):
    if TestClient is None:
        pytest.skip("httpx not available")
    trader, _ = _build_trader()
    original_trader = app_module._auto_trader
    monkeypatch.setattr(app_module, "_auto_trader", trader)
    client = TestClient(app_module.app)

    try:
        payload = {
            "mode": "paper",
            "market": "KRW-BTC",
            "interval": "minute60",
            "risk_appetite": 0.6,
            "capital": 15_000_000,
            "poll_interval": 300,
            "max_position_pct": 0.2,
            "min_confidence_pct": 50,
            "include_portfolio": False,
            "max_trades_per_cycle": 1,
        }
        start_response = client.post("/trading/autopilot/start", json=payload)
        assert start_response.status_code == 200
        start_data = start_response.json()
        assert start_data["running"] is True
        assert start_data["last_plan"]["side"] == "bid"
        assert start_data["next_cycle_due_at"] is not None
        assert start_data["config"]["auto_select_market"] is False
        assert start_data["config"]["max_trades_per_cycle"] == 1
        assert "recent_recommendations" in start_data
        assert "candidate_market_count" in start_data

        status_response = client.get("/trading/autopilot/status")
        assert status_response.status_code == 200
        status_data = status_response.json()
        assert status_data["config"]["market"] == "KRW-BTC"
        assert status_data["config"]["max_trades_per_cycle"] == 1
        assert "next_cycle_due_at" in status_data
        assert "recommendation_source" in status_data
        assert status_data["candidate_market_count"] >= len(status_data["candidate_markets"])

        stop_response = client.post("/trading/autopilot/stop")
        assert stop_response.status_code == 200
        stop_data = stop_response.json()
        assert stop_data["running"] is False
    finally:
        stop_state = trader.stop()
        assert stop_state.running is False
        monkeypatch.setattr(app_module, "_auto_trader", original_trader)


def test_autopilot_api_allows_blank_market_when_auto_select(monkeypatch):
    if TestClient is None:
        pytest.skip("httpx not available")

    trader, _ = _build_trader()
    original_trader = app_module._auto_trader
    monkeypatch.setattr(app_module, "_auto_trader", trader)
    client = TestClient(app_module.app)

    try:
        payload = {
            "mode": "paper",
            "market": "",
            "interval": "minute60",
            "risk_appetite": 0.6,
            "capital": 12_000_000,
            "poll_interval": 180,
            "max_position_pct": 0.2,
            "min_confidence_pct": 50,
            "include_portfolio": False,
            "auto_select_market": True,
        }
        response = client.post("/trading/autopilot/start", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is True
        assert data["config"]["market"] == "KRW-BTC"
        assert data["config"]["auto_select_market"] is True
    finally:
        trader.stop()
        monkeypatch.setattr(app_module, "_auto_trader", original_trader)


def test_autopilot_api_rejects_blank_market_when_auto_select_disabled(monkeypatch):
    if TestClient is None:
        pytest.skip("httpx not available")

    client = TestClient(app_module.app)

    payload = {
        "mode": "paper",
        "market": " ",
        "interval": "minute60",
        "risk_appetite": 0.6,
        "capital": 10_000_000,
        "poll_interval": 180,
        "max_position_pct": 0.2,
        "min_confidence_pct": 50,
        "include_portfolio": False,
        "auto_select_market": False,
    }

    response = client.post("/trading/autopilot/start", json=payload)
    assert response.status_code == 422
    detail = response.json().get("detail")
    assert any("마켓" in (item.get("msg", "") or "") for item in detail)


def test_autotrader_handles_analysis_exception_gracefully():
    def failing_analyse(*_args, **_kwargs):
        raise RuntimeError("analysis boom")

    trader, _ = _build_trader(analyse_market=failing_analyse)
    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.5,
        capital=12_000_000,
        poll_interval=120.0,
        include_portfolio=False,
        max_position_pct=0.2,
        min_confidence_pct=55.0,
    )

    state = trader.start(config)
    try:
        assert state.running is True
        assert state.last_plan is None
        assert state.last_execution is None
        assert state.last_error is not None
        assert "내부 오류" in state.last_error
        assert state.last_skip_reason is not None
        assert "관망" in state.last_skip_reason
    finally:
        trader.stop()


def test_autotrader_handles_recommendation_failure():
    def failing_scanner(*_args, **_kwargs):
        raise RuntimeError("scan failed")

    trader, _ = _build_trader(recommendation_scanner=failing_scanner)
    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.55,
        capital=18_000_000,
        poll_interval=150.0,
        include_portfolio=False,
        max_position_pct=0.25,
        min_confidence_pct=50.0,
        auto_select_market=True,
    )

    state = trader.start(config)
    try:
        assert state.running is True
        assert state.last_plan is None
        assert state.last_execution is None
        assert state.last_error is not None
        assert "추천 엔진" in state.last_error
        assert state.last_skip_reason is not None
        assert "관망" in state.last_skip_reason
    finally:
        trader.stop()


def test_ai_copilot_falls_back_to_synthetic_data(monkeypatch):
    if TestClient is None:
        pytest.skip("httpx not available")
    client = TestClient(app_module.app)

    def failing_fetch(**_kwargs):
        raise app_module.MarketDataError("네트워크 오류")

    monkeypatch.setattr(app_module, "fetch_upbit_candles", failing_fetch)

    payload = {
        "question": "시장 요약 부탁해",
        "market": "KRW-BTC",
        "interval": "minute60",
        "mode": "paper",
        "risk_appetite": 0.55,
        "capital": 15_000_000,
        "include_portfolio": True,
    }

    response = client.post("/ai/copilot", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["autopilot"]["market"] == "KRW-BTC"
    assert data["summary_points"], "요약 정보가 비어 있습니다."


def test_ai_assistant_falls_back_to_synthetic_data(monkeypatch):
    if TestClient is None:
        pytest.skip("httpx not available")
    client = TestClient(app_module.app)

    def failing_fetch(**_kwargs):
        raise app_module.MarketDataError("연결 실패")

    monkeypatch.setattr(app_module, "fetch_upbit_candles", failing_fetch)

    payload = {
        "question": "전략 추천해줘",
        "market": "KRW-ETH",
        "interval": "minute60",
        "risk_appetite": 0.6,
        "capital": 13_000_000,
        "include_autopilot": True,
    }

    response = client.post("/ai/assistant", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["insight"]["market"] == "KRW-ETH"
    assert data["answer"], "어시스턴트 응답이 비어 있습니다."


def test_upbit_self_heal_endpoints(monkeypatch):
    if TestClient is None:
        pytest.skip("httpx not available")

    called = {}

    def fake_attempt(reason: str):
        called["reason"] = reason
        return {
            "performed_at": datetime.now(timezone.utc),
            "reason": reason,
            "status": "success",
            "steps": [],
        }

    monkeypatch.setattr(app_module, "attempt_upbit_self_heal", fake_attempt)
    monkeypatch.setattr(app_module, "get_upbit_recovery_log", lambda limit=20: [])

    client = TestClient(app_module.app)

    response = client.post("/diagnostics/upbit/self-heal", params={"reason": "unit"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert called["reason"] == "unit"

    log_response = client.get("/diagnostics/upbit/recovery-log")
    assert log_response.status_code == 200
    assert log_response.json() == {"entries": []}

from datetime import datetime, timedelta, timezone
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend import ai
from backend.adaptive import AdaptiveLearner
from backend.autopilot import AutoTrader, AutoTraderConfig
from backend.execution import PaperBroker
from backend.market import MarketData
from backend.schemas import OrderMode
from backend.version import APP_VERSION
from backend.app import _autopilot_status_payload, _auto_start_autopilot_if_needed
from backend.trading import Candle


def _fake_candles(count: int = 120) -> list[Candle]:
    base_time = datetime.now(timezone.utc) - timedelta(minutes=count)
    candles: list[Candle] = []
    price = 12_000_000.0
    for index in range(count):
        close = price + index * 15_000
        candles.append(
            Candle(
                timestamp=base_time + timedelta(minutes=index),
                open=close - 10_000,
                high=close + 11_000,
                low=close - 13_000,
                close=close,
                volume=1.2 + index * 0.01,
            )
        )
    return candles


def _make_trader(recommendations: list[str], *, state_path):
    broker = PaperBroker()

    def candle_fetcher(*, market: str, interval: str, count: int) -> MarketData:
        return MarketData(candles=_fake_candles(count), source="synthetic")

    def analyse_market(candles, **kwargs):
        kwargs.setdefault("news", [])
        return ai.analyse_market(candles, **kwargs)

    def autopilot_builder(**kwargs):
        insight = kwargs["insight"]
        return ai.AutoPilotOrderPlan(
            market=insight.market,
            side="bid",
            bias="long",
            order_type="market",
            suggested_price=None,
            position_size_pct=5.0,
            stop_loss_pct=1.5,
            take_profit_pct=3.0,
            trailing_stop_pct=1.0,
            confidence_pct=75.0,
            reasoning=["테스트 자동매수"],
            monitoring=["리스크 모니터링"],
        )

    def recommendation_scanner(*_args, **_kwargs):
        entries = []
        for label in recommendations:
            market = label.split()[0]
            entries.append(
                SimpleNamespace(
                    market=market,
                    recommended_action="매수 집중",
                    confidence_pct=74.0,
                    score=92.0,
                )
            )
        return SimpleNamespace(
            recommendations=entries,
            analysis_source="synthetic",
            errors=[],
        )

    trader = AutoTrader(
        broker=broker,
        candle_fetcher=candle_fetcher,
        news_fetcher=lambda _limit: [],
        analyse_market=analyse_market,
        autopilot_builder=autopilot_builder,
        portfolio_builder=lambda **_: None,
        time_provider=lambda: datetime.now(timezone.utc),
        notifier=None,
        recommendation_scanner=recommendation_scanner,
        learner=AdaptiveLearner(path=state_path),
    )

    return trader, broker


def test_status_contains_recommendations_and_source(tmp_path):
    trader, _ = _make_trader(["KRW-ETH · 점수 92"], state_path=tmp_path / "adaptive.json")
    config = AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.5,
        capital=10_000_000,
        poll_interval=120.0,
        include_portfolio=False,
        max_position_pct=0.2,
        min_confidence_pct=60.0,
        auto_select_market=True,
        max_trades_per_cycle=3,
    )

    state = trader.start(config, synchronous=True)
    try:
        payload = _autopilot_status_payload(state)
        assert payload.config is not None
        assert payload.config.max_trades_per_cycle == 3
        assert state.last_recommendations, "AI 추천 내역이 상태에 포함되지 않았습니다."
        assert state.last_recommendation_source == "synthetic"
        assert state.last_skip_reason in {None, "실시간 시세 부재로 관망합니다."}
        assert payload.effective_min_confidence >= 0
        assert payload.app_version == APP_VERSION
        assert payload.market_rotation_queue
        assert payload.adaptive_profile is not None
        assert payload.adaptive_profile.trades_tracked >= 0

        follow_up = trader.status()
        assert follow_up.last_recommendations, "상태 호출 시 추천 내역이 손실되었습니다."
        assert follow_up.last_skip_reason in {None, "실시간 시세 부재로 관망합니다."}
        assert (
            follow_up.recommendation_market_count
            >= len(follow_up.recommendation_markets)
            >= 1
        )
        assert follow_up.market_rotation_queue
        assert follow_up.last_network_status in {"unknown", "up", "warning", "down"}
        assert follow_up.last_network_message is not None

        mutated = follow_up.last_recommendations
        mutated.append("mutated")

        fresh = trader.status()
        assert all("mutated" not in entry for entry in fresh.last_recommendations)
        assert fresh.recommendation_market_count >= len(fresh.recommendation_markets)
    finally:
        trader.stop()


def test_auto_start_autopilot_when_idle(monkeypatch):
    from backend import app as app_module
    from backend.autopilot import AutoTraderState

    class StubTrader:
        def __init__(self) -> None:
            self.start_calls = 0
            self.state = AutoTraderState()

        def status(self) -> AutoTraderState:
            if self.start_calls:
                return self.state
            return AutoTraderState()

        def start(self, config, *, synchronous: bool = False):
            self.start_calls += 1
            self.state = AutoTraderState(
                running=True,
                config=config,
                last_cycle_started_at=datetime.now(timezone.utc),
                last_cycle_completed_at=datetime.now(timezone.utc),
            )
            return self.state

    stub_trader = StubTrader()
    monkeypatch.setattr(app_module, "_auto_trader", stub_trader)
    monkeypatch.setattr(app_module, "_paper_broker", SimpleNamespace(initial_cash=15_000_000))
    monkeypatch.setattr(app_module, "_AUTO_START_AUTOPILOT", True, raising=False)

    state = _auto_start_autopilot_if_needed()
    assert state.running is True
    assert stub_trader.start_calls == 1
    assert state.config is not None
    assert state.config.capital == 15_000_000

    # Subsequent calls should reuse the running instance without starting again.
    state_two = _auto_start_autopilot_if_needed()
    assert state_two.running is True
    assert stub_trader.start_calls == 1

from datetime import datetime, timezone

from backend.adaptive import AdaptiveLearner
from backend.autopilot import AutoTraderConfig
from backend.execution import BalanceSnapshot
from backend.schemas import OrderMode


def _snapshot(value: float) -> BalanceSnapshot:
    return BalanceSnapshot(
        cash=value,
        portfolio_value=value,
        last_update=datetime.now(timezone.utc),
        initial_cash=value,
        positions=[],
        orders=[],
    )


def _base_config() -> AutoTraderConfig:
    return AutoTraderConfig(
        mode=OrderMode.PAPER,
        market="KRW-BTC",
        interval="minute60",
        risk_appetite=0.5,
        capital=10_000_000,
        poll_interval=120.0,
        include_portfolio=False,
        max_position_pct=0.2,
        min_confidence_pct=50.0,
        auto_select_market=True,
        recommendation_base="KRW",
        recommendation_interval="minute60",
        recommendation_max_markets=60,
        recommendation_include_warnings=False,
        max_trades_per_cycle=1,
    )


def test_adaptive_learner_lowers_threshold_on_profit(tmp_path):
    learner = AdaptiveLearner(path=tmp_path / "adaptive-positive.json", history=20)
    config = _base_config()
    before = _snapshot(1_000_000)
    learner.reset(initial_equity=before.portfolio_value, config=config)
    learner.apply(config=config)
    after = _snapshot(1_080_000)

    profile = learner.observe(
        config=config,
        before=before,
        after=after,
        executed=True,
    )

    assert profile.min_confidence_pct < 50.0
    assert profile.risk_appetite > 0.5
    assert profile.max_position_pct >= 0.2


def test_adaptive_learner_raises_threshold_on_loss(tmp_path):
    learner = AdaptiveLearner(path=tmp_path / "adaptive-negative.json", history=20)
    config = _base_config()
    before = _snapshot(1_000_000)
    learner.reset(initial_equity=before.portfolio_value, config=config)
    learner.apply(config=config)
    after = _snapshot(880_000)

    profile = learner.observe(
        config=config,
        before=before,
        after=after,
        executed=True,
    )

    assert profile.min_confidence_pct > 50.0
    assert profile.risk_appetite <= 0.5
    assert profile.max_position_pct <= 0.2

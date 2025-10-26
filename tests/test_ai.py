from types import SimpleNamespace
from backend import ai
from backend.trading import generate_synthetic_prices


def test_analyse_market_with_synthetic():
    candles = generate_synthetic_prices(days=120, seed=2024)
    insight = ai.analyse_market(candles, market="KRW-BTC", interval="minute60", news=[])
    assert insight.recommended_action
    assert insight.metrics.rsi >= 0
    assert 0 <= insight.confidence_pct <= 100
    assert insight.timeframe_consensus.dominant_trend
    assert insight.timeframe_consensus.details
    assert 0 <= insight.metrics.institutional_sentiment <= 1
    assert insight.metrics.hurst_exponent >= 0
    assert insight.institutional_confidence_pct >= 0
    assert insight.institutional_commentary
    assert insight.metrics.volatility_spike_score >= 0.6
    assert insight.metrics.shock_risk_pct >= 0
    assert insight.metrics.impulse_direction in {"상승", "하락", "중립"}
    assert 0 <= insight.metrics.regime_shift_probability <= 1


def test_optimise_portfolio_with_trend_adjustment():
    candles = generate_synthetic_prices(days=160, seed=77)

    def fake_fetch(_market: str, _interval: str, _count: int):
        return candles

    plan = ai.optimise_portfolio(
        risk_appetite=0.65,
        capital=30_000_000,
        include_cash=True,
        candle_fetcher=fake_fetch,
    )

    total_weight = sum(allocation.weight for allocation in plan.allocations)
    assert abs(total_weight - 1) < 1e-6
    assert plan.expected_return_pct > 0
    assert plan.market_briefings  # AI가 생성한 시장 브리핑 확인


def test_copilot_autopilot_generates_plan():
    candles = generate_synthetic_prices(days=180, seed=404)
    insight = ai.analyse_market(candles, market="KRW-BTC", interval="minute60", news=[])

    autopilot = ai.craft_autopilot_plan(
        insight=insight,
        risk_appetite=0.6,
        capital=25_000_000,
        mode="paper",
    )

    assert autopilot.market == "KRW-BTC"
    assert autopilot.monitoring
    assert any("기관" in item for item in autopilot.monitoring)
    assert any("급등" in reason or "급락" in reason for reason in autopilot.reasoning)

    synthesis = ai.generate_copilot_synthesis(
        question="지금 어떤 전략이 좋을까?",
        insight=insight,
        autopilot=autopilot,
        portfolio_plan=None,
        mode="paper",
    )

    assert "Sado Trade Bot" in synthesis.answer
    assert synthesis.summary_points
    assert synthesis.autopilot is autopilot
    assert any("다중 타임프레임" in point for point in synthesis.summary_points)


def test_assistant_synthesis_provides_actions_and_risk():
    candles = generate_synthetic_prices(days=150, seed=808)
    insight = ai.analyse_market(candles, market="KRW-XRP", interval="minute60", news=[])
    autopilot = ai.craft_autopilot_plan(
        insight=insight,
        risk_appetite=0.5,
        capital=18_000_000,
        mode="paper",
    )

    insight.signals = ["EMA 상향돌파", "RSI 상승"] + list(insight.signals or [])
    insight.news = [{"title": "기관 테스트 브리핑", "source": "테스트 연구소"}]
    insight.metrics.volatility_regime = insight.metrics.volatility_regime or "중립"
    insight.metrics.probability_of_trend = 0.62

    synthesis = ai.generate_assistant_synthesis(
        question="에이다 코인 매수 포지션 잡아줘",
        insight=insight,
        autopilot=autopilot,
        include_autopilot=True,
    )

    assert "Sado Trade Bot" in synthesis.answer
    assert synthesis.insights
    assert synthesis.next_steps
    assert any("모니터링" in notice for notice in synthesis.risk_notices)
    assert any("시그널" in insight_line for insight_line in synthesis.insights)
    assert any("추세 확률" in notice for notice in synthesis.risk_notices)
    assert synthesis.autopilot is autopilot

    synthesis_no_auto = ai.generate_assistant_synthesis(
        question="BTC 어떻게 할까?",
        insight=insight,
        autopilot=None,
        include_autopilot=False,
    )

    assert synthesis_no_auto.autopilot is None
    assert any("오토파일럿" in step for step in synthesis_no_auto.next_steps)


def test_loss_recovery_playbook_surfaces_risk_guidance():
    candles = generate_synthetic_prices(days=90, seed=512)
    insight = ai.analyse_market(candles, market="KRW-SOL", interval="minute60", news=[])

    orders = [
        SimpleNamespace(
            market="KRW-SOL",
            realized_pnl=-180_000.0,
            price=30_000.0,
            volume=6.0,
        ),
        SimpleNamespace(
            market="KRW-BTC",
            realized_pnl=90_000.0,
            price=31_000_000.0,
            volume=0.003,
        ),
    ]

    class _DummyPosition:
        def __init__(self, market: str, average_price: float, market_price: float, volume: float) -> None:
            self.market = market
            self.average_price = average_price
            self.market_price = market_price
            self.volume = volume

        @property
        def unrealized_pnl(self) -> float:
            return (self.market_price - self.average_price) * self.volume

    positions = [
        _DummyPosition("KRW-XRP", average_price=700.0, market_price=640.0, volume=400.0),
    ]

    executions = [
        SimpleNamespace(mode="paper", market="KRW-SOL", side="bid", price=31_000.0, volume=5.0),
    ]

    plan = ai.build_loss_recovery_playbook(
        orders=orders,
        positions=positions,
        executions=executions,
        last_insight=insight,
        news_items=[{"source": "테스트 기관", "title": "시장 모니터링 강화"}],
    )

    assert plan.realized_loss_krw > 0
    assert plan.unrealized_loss_krw > 0
    assert plan.loss_markets and "KRW-SOL" in plan.loss_markets
    assert any(step.actions for step in plan.steps)
    assert any(step.guardrails for step in plan.steps)
    assert plan.risk_commandments
    assert plan.chart_playbook
    assert plan.institutional_briefs

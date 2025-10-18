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

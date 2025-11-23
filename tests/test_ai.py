from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import importlib

import pytest

from backend import ai
from backend.history import AssistantHistoryEntry
from backend.trading import Candle, generate_synthetic_prices
from .helpers import authenticate_client

try:  # pragma: no cover - optional dependency for API tests
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - FastAPI test client not available
    TestClient = None


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
    assert 0 <= insight.technical_confluence.score <= 100
    assert insight.technical_confluence.label
    assert isinstance(insight.technical_confluence.drivers, list)
    assert 0 <= insight.metrics.institutional_alert_level <= 100
    assert isinstance(insight.metrics.institutional_alerts, list)
    assert isinstance(insight.metrics.credible_sources, list)


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
    assert any("컨플루언스" in reason for reason in autopilot.reasoning)


def test_institutional_alerts_trigger_on_credible_news():
    candles = generate_synthetic_prices(days=140, seed=5123)
    news = [
        {
            "title": "BIS warns of crypto leverage build-up",
            "source": "BIS Quarterly Review",
        }
    ]

    insight = ai.analyse_market(candles, market="KRW-ETH", interval="minute60", news=news)
    assert insight.metrics.institutional_alert_level > 0
    assert insight.metrics.institutional_alerts
    autopilot = ai.craft_autopilot_plan(
        insight=insight,
        risk_appetite=0.6,
        capital=12_000_000,
        mode="paper",
    )
    assert any("기관 경보" in note for note in autopilot.monitoring)


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


def test_assistant_history_endpoints(monkeypatch, tmp_path):
    if TestClient is None:  # pragma: no cover - FastAPI client unavailable
        pytest.skip("fastapi.testclient not available")

    storage_file = tmp_path / "assistant_history.json"
    monkeypatch.setenv("ASSISTANT_HISTORY_PATH", str(storage_file))

    # Reload app module so the assistant history store picks up the temp path
    app_module = importlib.import_module("backend.app")
    app_module = importlib.reload(app_module)

    client = TestClient(app_module.app)
    authenticate_client(client)

    response = client.delete("/ai/assistant/history")
    assert response.status_code == 200
    assert response.json() == {"entries": [], "count": 0}

    entry = AssistantHistoryEntry(
        generated_at=app_module._utcnow(),
        question="BTC 전략 추천",
        answer="관망 후 분할매수 전략을 유지하세요.",
        insights=["EMA 상향돌파 감지"],
        next_steps=["12시간 봉으로 재확인"],
        risk_notices=["거래량 급감 주의"],
        pushed_to_chat=True,
    )
    app_module._assistant_history_store.append(entry)

    response = client.get("/ai/assistant/history")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["entries"][0]["question"] == "BTC 전략 추천"
    assert payload["entries"][0]["pushed_to_chat"] is True

    response = client.delete("/ai/assistant/history")
    assert response.status_code == 200
    assert response.json() == {"entries": [], "count": 0}


def _build_scalping_friendly_candles() -> list[Candle]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    price = 1_000_000.0
    candles: list[Candle] = []
    for index in range(80):
        open_price = price
        price *= 1.0015 + (0.0002 * (index % 3))
        close_price = price
        high = max(open_price, close_price) * 1.001
        low = min(open_price, close_price) * 0.999
        volume = 120_000 + index * 2_000
        candles.append(
            Candle(
                timestamp=start + timedelta(minutes=3 * index),
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                volume=volume,
            )
        )
    return candles


def test_scalping_signal_detects_intraday_bias():
    candles = _build_scalping_friendly_candles()
    signal = ai.evaluate_scalping_signal(candles, interval="minute3")
    assert signal is not None
    assert signal.bias == "long"
    assert signal.conviction_pct >= 50
    assert signal.stop_loss_pct > 0
    assert signal.structure_note in {"고점 돌파", "중앙 수렴", "박스권 유지", "범위 관망", "저점 붕괴"}
    assert isinstance(signal.vwap_gap_pct, float)
    assert isinstance(signal.liquidity_zscore, float)
    assert signal.micro_trend in {"상승", "하락", "중립"}
    assert any("EMA" in reason for reason in signal.reasoning)


def test_apply_scalping_signal_enhances_plan():
    base_plan = ai.AutoPilotOrderPlan(
        market="KRW-ADA",
        side="flat",
        bias="neutral",
        order_type="monitor",
        suggested_price=None,
        position_size_pct=0.0,
        stop_loss_pct=3.0,
        take_profit_pct=5.0,
        trailing_stop_pct=1.0,
        confidence_pct=42.0,
        reasoning=["기본 관망"],
        monitoring=["기본 모니터링"],
    )

    scalping_signal = ai.ScalpingSignal(
        bias="long",
        conviction_pct=68.0,
        stop_loss_pct=1.2,
        take_profit_pct=2.6,
        trailing_stop_pct=0.8,
        reasoning=["단타 EMA 상향 돌파"],
        interval="minute3",
        volatility_pct=1.1,
        volume_ratio=1.4,
        label="스캘핑 매수 시그널",
        vwap_gap_pct=0.8,
        micro_trend="상승",
        liquidity_zscore=1.2,
        structure_note="고점 돌파",
    )

    enhanced_plan, applied = ai.apply_scalping_signal(
        plan=base_plan,
        scalping=scalping_signal,
        risk_appetite=0.6,
        allow_short=False,
    )

    assert applied is True
    assert enhanced_plan.side == "bid"
    assert enhanced_plan.confidence_pct >= scalping_signal.conviction_pct
    assert any("스캘핑" in reason for reason in enhanced_plan.reasoning)
    assert any("구조" in reason for reason in enhanced_plan.reasoning)
    assert enhanced_plan.position_size_pct > 0

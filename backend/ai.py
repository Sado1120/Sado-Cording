"""AI-enhanced analytics and portfolio optimisation utilities."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt
from statistics import fmean, pstdev
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .market import fetch_authoritative_news
from .trading import Candle


@dataclass
class MarketAIMetrics:
    """Structured analytics describing the current market regime."""

    fast_ema: float
    slow_ema: float
    rsi: float
    macd: float
    macd_signal: float
    macd_histogram: float
    volatility_pct: float
    trend_strength: float
    regime_score: float
    probability_of_trend: float
    price_change_pct: float
    support_level: float
    resistance_level: float


@dataclass
class RiskControlAdvice:
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: Optional[float]
    position_size_pct: float
    confidence_note: str
    notes: List[str]


@dataclass
@dataclass
class TimeframeConsensus:
    """Consensus view across multiple look-back windows."""

    dominant_trend: str
    agreement_pct: float
    intervals: List[str]
    details: List[str]


@dataclass
class MarketAIInsight:
    market: str
    interval: str
    regime: str
    recommended_action: str
    confidence_pct: float
    summary: str
    signals: List[str]
    metrics: MarketAIMetrics
    risk: RiskControlAdvice
    generated_at: datetime
    news: List[Dict[str, str]]
    timeframe_consensus: TimeframeConsensus


@dataclass
class PortfolioAsset:
    symbol: str
    name: str
    asset_type: str
    expected_return_pct: float
    expected_volatility_pct: float
    risk_score: float
    narrative: str
    market: Optional[str] = None


@dataclass
class PortfolioAllocation:
    symbol: str
    name: str
    asset_type: str
    weight: float
    allocation_krw: float
    expected_return_pct: float
    expected_volatility_pct: float
    rationale: str


@dataclass
class PortfolioAIPlan:
    generated_at: datetime
    risk_profile_label: str
    risk_appetite: float
    expected_return_pct: float
    expected_volatility_pct: float
    sharpe_estimate: float
    diversification_score_pct: float
    tail_risk_guard_pct: float
    allocations: List[PortfolioAllocation]
    hedging_notes: List[str]
    methodology: str
    market_briefings: List[Dict[str, str]]


@dataclass
class AutoPilotOrderPlan:
    """Structured auto-trading instruction returned by the AI copilot."""

    market: str
    side: str
    bias: str
    order_type: str
    suggested_price: Optional[float]
    position_size_pct: float
    stop_loss_pct: float
    take_profit_pct: Optional[float]
    trailing_stop_pct: Optional[float]
    confidence_pct: float
    reasoning: List[str]
    monitoring: List[str]


@dataclass
class CopilotSynthesis:
    """Rich conversational answer generated for the copilot endpoint."""

    answer: str
    summary_points: List[str]
    risk_notices: List[str]
    action_items: List[str]
    highlights: List[str]
    autopilot: AutoPilotOrderPlan
    generated_at: datetime


_MIN_CANDLES = 30

_ANNUALISATION_FACTORS = {
    "minute1": 60 * 24 * 365,
    "minute5": 12 * 24 * 365,
    "minute15": 4 * 24 * 365,
    "minute60": 24 * 365,
    "day": 365,
    "week": 52,
    "month": 12,
}


def _ema(values: Sequence[float], period: int) -> float:
    if not values:
        return 0.0
    multiplier = 2 / (period + 1)
    ema_value = values[0]
    for price in values[1:]:
        ema_value = (price - ema_value) * multiplier + ema_value
    return float(ema_value)


def _rsi(values: Sequence[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains: List[float] = []
    losses: List[float] = []
    for previous, current in zip(values[:-1], values[1:]):
        change = current - previous
        if change >= 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-change)
    avg_gain = fmean(gains[-period:]) if gains else 0.0
    avg_loss = fmean(losses[-period:]) if losses else 0.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(values: Sequence[float]) -> Tuple[float, float, float]:
    fast = _ema(values, 12)
    slow = _ema(values, 26)
    macd_value = fast - slow
    signal = _ema([macd_value] + list(values[-9:]), 9)
    histogram = macd_value - signal
    return macd_value, signal, histogram


def _annualised_volatility(returns: Sequence[float], interval: str) -> float:
    if not returns:
        return 0.0
    factor = _ANNUALISATION_FACTORS.get(interval, 365)
    return pstdev(returns) * sqrt(factor)


def _support_resistance(candles: Sequence[Candle]) -> Tuple[float, float]:
    lows = [candle.low for candle in candles[-20:]]
    highs = [candle.high for candle in candles[-20:]]
    if not lows or not highs:
        return 0.0, 0.0
    return min(lows), max(highs)


def _classify_trend_window(candles: Sequence[Candle]) -> Tuple[str, float]:
    if len(candles) < 10:
        return "데이터 부족", 0.0

    closes = [candle.close for candle in candles]
    fast = _ema(closes[-min(len(closes), 12) :], min(12, len(closes)))
    slow = _ema(closes[-min(len(closes), 26) :], min(26, len(closes)))
    trend = 0.0 if slow == 0 else (fast - slow) / slow
    rsi = _rsi(closes, period=min(14, len(closes) - 1))

    if trend > 0.004 and rsi >= 55:
        label = "상승"
    elif trend < -0.004 and rsi <= 45:
        label = "하락"
    else:
        label = "중립"

    confidence = min(100.0, abs(trend) * 8000 + abs(rsi - 50) * 2)
    return label, confidence


def build_timeframe_consensus(
    candles: Sequence[Candle], *, market: str, interval: str
) -> TimeframeConsensus:
    if len(candles) < _MIN_CANDLES:
        return TimeframeConsensus(
            dominant_trend="데이터 부족",
            agreement_pct=0.0,
            intervals=[f"{market} {interval}"],
            details=["캔들이 충분하지 않아 합의를 도출하지 못했습니다."],
        )

    specs = [
        ("단기 (최근 20)", 20),
        ("중기 (최근 60)", 60),
        ("장기 (최근 120)", 120),
    ]

    results: List[Tuple[str, str, float]] = []
    for label, window in specs:
        window_candles = list(candles[-window:]) if len(candles) >= window else list(candles)
        trend_label, confidence = _classify_trend_window(window_candles)
        results.append((label, trend_label, confidence))

    score_map: Dict[str, float] = defaultdict(float)
    for _, trend_label, confidence in results:
        score_map[trend_label] += confidence

    if not score_map:
        dominant = "중립"
    else:
        dominant = max(score_map.items(), key=lambda item: item[1])[0]

    agreement = (
        sum(1 for _, trend_label, _ in results if trend_label == dominant) / len(results) * 100
        if results
        else 0.0
    )

    details = [f"{label}: {trend_label} (신뢰도 {confidence:.0f}%)" for label, trend_label, confidence in results]

    return TimeframeConsensus(
        dominant_trend=dominant,
        agreement_pct=agreement,
        intervals=[label for label, _, _ in results],
        details=details,
    )


def analyse_market(
    candles: Sequence[Candle],
    *,
    market: str,
    interval: str,
    news: Optional[List[Dict[str, str]]] = None,
) -> MarketAIInsight:
    if len(candles) < _MIN_CANDLES:
        raise ValueError("시장 분석에 필요한 캔들이 부족합니다.")

    closes = [candle.close for candle in candles]
    fast_ema = _ema(closes[-26:], 12)
    slow_ema = _ema(closes[-60:], 26)
    rsi = _rsi(closes[-40:])
    macd_value, macd_signal, macd_hist = _macd(closes[-60:])
    macd_norm = macd_hist / closes[-1] if closes else 0.0

    returns: List[float] = []
    for prev, current in zip(closes[:-1], closes[1:]):
        if prev == 0:
            continue
        returns.append((current / prev) - 1)

    volatility = _annualised_volatility(returns[-120:], interval)
    trend_strength = 0.0 if slow_ema == 0 else (fast_ema - slow_ema) / slow_ema
    regime_score = (trend_strength * 0.6) + ((50 - abs(rsi - 50)) / 100 * 0.2) + (macd_norm * 3)
    probability_of_trend = max(0.0, min(1.0, abs(trend_strength) * 5 + abs(macd_norm) * 3))

    price_change_pct = returns[-1] * 100 if returns else 0.0
    support, resistance = _support_resistance(candles)

    if trend_strength > 0.005 and rsi < 72 and macd_norm >= -0.002:
        regime = "강세 추세"
        recommended_action = "추세 추종 매수"
        confidence = min(95.0, 55 + probability_of_trend * 40)
    elif trend_strength < -0.005 and macd_norm <= 0.002:
        regime = "약세 추세"
        recommended_action = "방어적 매도 및 현금 비중 확대"
        confidence = min(90.0, 50 + probability_of_trend * 35)
    elif macd_norm <= -0.01 and rsi >= 65:
        regime = "과열 반전"
        recommended_action = "방어적 매도 및 현금 비중 확대"
        confidence = min(85.0, 48 + probability_of_trend * 30)
    elif macd_norm >= 0.01 and rsi <= 35:
        regime = "과매도 반등"
        recommended_action = "추세 추종 매수"
        confidence = min(85.0, 48 + probability_of_trend * 30)
    else:
        regime = "중립 / 박스권"
        recommended_action = "범위 매매 또는 대기"
        confidence = 45.0

    summary = (
        f"{market} {interval} 캔들 기준으로 EMA 격차는 {trend_strength * 100:.2f}%이며 RSI는 {rsi:.1f} 수준입니다. "
        f"MACD 히스토그램은 {macd_norm * 100:.2f}%로 {'상승' if macd_norm >= 0 else '하락'} 압력이 우세합니다."
    )

    signals = [
        f"Fast EMA {fast_ema:,.0f}",
        f"Slow EMA {slow_ema:,.0f}",
        f"RSI {rsi:.1f}",
        f"MACD {macd_value:.3f} / Signal {macd_signal:.3f}",
        f"연환산 변동성 {volatility * 100:.2f}%",
    ]

    risk = RiskControlAdvice(
        stop_loss_pct=max(0.8, (volatility * 100) / 3),
        take_profit_pct=min(25.0, max(3.0, volatility * 100 / 2)),
        trailing_stop_pct=min(15.0, max(2.0, abs(macd_norm) * 100)),
        position_size_pct=max(1.0, 5.0 - probability_of_trend * 2),
        confidence_note="신호 강도에 기반하여 포지션 규모 자동 조정",
        notes=[
            "변동성이 높을수록 목표 수익과 손절폭을 넓게 설정하세요.",
            "박스권 시에는 포지션을 축소하고 포트폴리오 재조정을 우선시합니다.",
        ],
    )

    metrics = MarketAIMetrics(
        fast_ema=fast_ema,
        slow_ema=slow_ema,
        rsi=rsi,
        macd=macd_value,
        macd_signal=macd_signal,
        macd_histogram=macd_norm,
        volatility_pct=volatility * 100,
        trend_strength=trend_strength,
        regime_score=regime_score,
        probability_of_trend=probability_of_trend,
        price_change_pct=price_change_pct,
        support_level=support,
        resistance_level=resistance,
    )

    consensus = build_timeframe_consensus(candles, market=market, interval=interval)

    return MarketAIInsight(
        market=market,
        interval=interval,
        regime=regime,
        recommended_action=recommended_action,
        confidence_pct=confidence,
        summary=summary,
        signals=signals,
        metrics=metrics,
        risk=risk,
        generated_at=datetime.now(timezone.utc),
        news=news or fetch_authoritative_news(limit=4),
        timeframe_consensus=consensus,
    )


_DEFAULT_ASSETS: List[PortfolioAsset] = [
    PortfolioAsset(
        symbol="BND",
        name="Vanguard Total Bond Market ETF",
        asset_type="etf",
        expected_return_pct=4.2,
        expected_volatility_pct=5.5,
        risk_score=0.15,
        narrative="미국 채권 시장 전체에 분산 투자하는 초저변동 자산",
    ),
    PortfolioAsset(
        symbol="JEPI",
        name="JPMorgan Equity Premium Income ETF",
        asset_type="etf",
        expected_return_pct=6.5,
        expected_volatility_pct=9.0,
        risk_score=0.25,
        narrative="커버드콜 전략으로 안정적 인컴을 제공",
    ),
    PortfolioAsset(
        symbol="SPY",
        name="SPDR S&P 500 ETF",
        asset_type="etf",
        expected_return_pct=8.5,
        expected_volatility_pct=16.0,
        risk_score=0.45,
        narrative="미국 대형주에 분산된 코어 자산",
    ),
    PortfolioAsset(
        symbol="QQQ",
        name="Invesco QQQ Trust",
        asset_type="etf",
        expected_return_pct=10.5,
        expected_volatility_pct=21.0,
        risk_score=0.55,
        narrative="미국 기술주 성장성을 반영",
    ),
    PortfolioAsset(
        symbol="BTC",
        name="Bitcoin",  # noqa: S105 - 티커 설명
        asset_type="crypto",
        expected_return_pct=35.0,
        expected_volatility_pct=65.0,
        risk_score=0.85,
        narrative="디지털 골드, 장기 성장 및 헤지 수단",
        market="KRW-BTC",
    ),
    PortfolioAsset(
        symbol="ETH",
        name="Ethereum",
        asset_type="crypto",
        expected_return_pct=28.0,
        expected_volatility_pct=55.0,
        risk_score=0.75,
        narrative="스마트 컨트랙트 인프라 핵심",
        market="KRW-ETH",
    ),
    PortfolioAsset(
        symbol="SOL",
        name="Solana",
        asset_type="crypto",
        expected_return_pct=45.0,
        expected_volatility_pct=90.0,
        risk_score=0.92,
        narrative="고성능 디파이·NFT 생태계",
        market="KRW-SOL",
    ),
]


def _bucket_weights(risk_appetite: float, include_cash: bool) -> Dict[str, float]:
    cash_weight = 0.0
    if include_cash:
        cash_weight = max(0.05, 0.18 - 0.12 * risk_appetite)
    stable_weight = max(0.2, 0.65 - 0.45 * risk_appetite)
    aggressive_weight = max(0.2, 1.0 - (cash_weight + stable_weight))

    total = cash_weight + stable_weight + aggressive_weight
    if total <= 0:
        return {"cash": 0.0, "stable": 0.0, "aggressive": 1.0}

    return {
        "cash": cash_weight / total,
        "stable": stable_weight / total,
        "aggressive": aggressive_weight / total,
    }


def _normalise_weights(assets: Sequence[Tuple[PortfolioAsset, float]]) -> Dict[str, float]:
    total = sum(weight for _, weight in assets)
    if total == 0:
        return {asset.symbol: 0.0 for asset, _ in assets}
    return {asset.symbol: weight / total for asset, weight in assets}


def optimise_portfolio(
    *,
    risk_appetite: float,
    capital: float,
    include_cash: bool,
    custom_assets: Optional[List[PortfolioAsset]] = None,
    preferred_markets: Optional[List[str]] = None,
    candle_fetcher: Optional[Callable[[str, str, int], Sequence[Candle]]] = None,
) -> PortfolioAIPlan:
    if not 0 <= risk_appetite <= 1:
        raise ValueError("risk_appetite 값은 0~1 범위여야 합니다.")

    assets = custom_assets or _DEFAULT_ASSETS
    if not assets:
        raise ValueError("포트폴리오 구성 자산이 필요합니다.")

    preferred_markets = preferred_markets or [asset.market for asset in assets if asset.market]

    bucket_share = _bucket_weights(risk_appetite, include_cash)

    stable_assets = [asset for asset in assets if asset.asset_type == "etf" and asset.risk_score <= 0.55]
    aggressive_assets = [asset for asset in assets if asset.asset_type == "crypto" or asset.risk_score > 0.55]

    stable_weights = _normalise_weights([
        (asset, 1 / max(asset.expected_volatility_pct, 1)) for asset in stable_assets
    ])
    aggressive_weights = _normalise_weights([
        (asset, asset.expected_return_pct / max(asset.expected_volatility_pct, 1))
        for asset in aggressive_assets
    ])

    trend_adjustments: Dict[str, float] = {}

    def _fetch_candles(market_code: str):
        if not candle_fetcher:
            return []
        try:
            return candle_fetcher(market_code, interval="minute60", count=120)
        except TypeError:
            return candle_fetcher(market_code, "minute60", 120)

    if candle_fetcher:
        for asset in aggressive_assets:
            if not asset.market:
                continue
            market_data = _fetch_candles(asset.market)
            if hasattr(market_data, "candles"):
                candles = list(market_data.candles)
            else:
                candles = list(market_data)
            if len(candles) < _MIN_CANDLES:
                continue
            insight = analyse_market(candles, market=asset.market, interval="minute60", news=[])
            trend_adjustments[asset.symbol] = 1 + insight.metrics.trend_strength * 2

    allocations: List[PortfolioAllocation] = []

    def add_allocation(asset: PortfolioAsset, weight: float, bucket: str) -> None:
        trend_boost = trend_adjustments.get(asset.symbol, 1.0)
        effective_weight = weight * bucket_share[bucket] * trend_boost
        allocations.append(
            PortfolioAllocation(
                symbol=asset.symbol,
                name=asset.name,
                asset_type=asset.asset_type,
                weight=effective_weight,
                allocation_krw=capital * effective_weight,
                expected_return_pct=asset.expected_return_pct,
                expected_volatility_pct=asset.expected_volatility_pct,
                rationale=f"{bucket} 버킷 · {asset.narrative}",
            )
        )

    for asset in stable_assets:
        weight = stable_weights.get(asset.symbol, 0.0)
        add_allocation(asset, weight, "stable")

    for asset in aggressive_assets:
        weight = aggressive_weights.get(asset.symbol, 0.0)
        add_allocation(asset, weight, "aggressive")

    if include_cash:
        allocations.append(
            PortfolioAllocation(
                symbol="CASH",
                name="현금 / MMF",
                asset_type="cash",
                weight=bucket_share["cash"],
                allocation_krw=capital * bucket_share["cash"],
                expected_return_pct=3.0,
                expected_volatility_pct=0.1,
                rationale="유동성 확보 및 급락시 기회자본",  # noqa: E501
            )
        )

    total_weight = sum(allocation.weight for allocation in allocations)
    if total_weight == 0:
        raise ValueError("유효한 포트폴리오 비중을 계산하지 못했습니다.")

    for allocation in allocations:
        allocation.weight /= total_weight
        allocation.allocation_krw = capital * allocation.weight

    expected_return = sum(
        allocation.weight * allocation.expected_return_pct for allocation in allocations
    )
    expected_volatility = sqrt(
        sum((allocation.weight * allocation.expected_volatility_pct) ** 2 for allocation in allocations)
    )
    sharpe_estimate = (
        (expected_return - 2.0) / expected_volatility if expected_volatility else expected_return / 2
    )

    n_assets = len(allocations)
    diversification_score = max(
        0.0,
        1 - sum((allocation.weight - (1 / n_assets)) ** 2 for allocation in allocations),
    )

    tail_guard = min(35.0, expected_volatility * 1.65)

    hedging_notes = [
        "변동성이 급등하면 현금 및 채권 비중을 확대하세요.",
        "기관 뉴스에서 정책 변화가 감지되면 ETF 비중을 조정하세요.",
    ]

    if risk_appetite > 0.7:
        hedging_notes.append("레버리지 사용 시 최대 하락폭을 25% 이하로 제한하세요.")
    else:
        hedging_notes.append("월간 리밸런싱으로 위험 프로파일을 유지하세요.")

    if trend_adjustments:
        hedging_notes.append("실시간 추세 스코어가 음수로 전환되면 공격 자산 비중을 절반으로 축소하세요.")

    risk_label = (
        "안정형" if risk_appetite < 0.3 else "균형형" if risk_appetite < 0.7 else "공격형"
    )

    briefings: List[Dict[str, str]] = []
    if candle_fetcher:
        for market_code in preferred_markets:
            market_data = _fetch_candles(market_code)
            if hasattr(market_data, "candles"):
                candles = list(market_data.candles)
            else:
                candles = list(market_data)
            if len(candles) < _MIN_CANDLES:
                continue
            insight = analyse_market(candles, market=market_code, interval="minute60", news=[])
            briefings.append(
                {
                    "market": market_code,
                    "regime": insight.regime,
                    "action": insight.recommended_action,
                    "confidence_pct": f"{insight.confidence_pct:.1f}",
                    "summary": insight.summary,
                }
            )

    return PortfolioAIPlan(
        generated_at=datetime.now(timezone.utc),
        risk_profile_label=risk_label,
        risk_appetite=risk_appetite,
        expected_return_pct=expected_return,
        expected_volatility_pct=expected_volatility,
        sharpe_estimate=sharpe_estimate,
        diversification_score_pct=diversification_score * 100,
        tail_risk_guard_pct=tail_guard,
        allocations=sorted(allocations, key=lambda a: a.weight, reverse=True),
        hedging_notes=hedging_notes,
        methodology="AI 리스크 버짓팅 + 추세 스코어 조정",
        market_briefings=briefings,
    )


def _derive_trade_bias(insight: MarketAIInsight) -> str:
    action = insight.recommended_action or ""
    if any(keyword in action for keyword in ("매수", "롱", "추세")):
        return "long"
    if any(keyword in action for keyword in ("매도", "숏", "헤지", "현금", "청산")):
        return "short"
    if "약세" in (insight.regime or ""):
        return "short"
    return "neutral"


def craft_autopilot_plan(
    *,
    insight: MarketAIInsight,
    risk_appetite: float,
    capital: float,
    mode: str,
    portfolio_plan: Optional[PortfolioAIPlan] = None,
) -> AutoPilotOrderPlan:
    """Translate market insight into a structured trading plan."""

    bias = _derive_trade_bias(insight)
    side = "bid" if bias == "long" else "ask" if bias == "short" else "flat"

    # Position sizing blends the AI risk guidance with the user's risk appetite.
    base_position = insight.risk.position_size_pct
    scaled_position = base_position * max(0.4, risk_appetite + 0.2)
    position_size_pct = max(0.5, min(15.0, scaled_position))

    confidence_pct = max(5.0, min(99.0, insight.confidence_pct))

    order_type = "market" if confidence_pct >= 60 else "limit"
    suggested_price = None

    if bias == "neutral":
        order_type = "monitor"
        position_size_pct = 0.0

    risk = insight.risk

    support_level = insight.metrics.support_level
    resistance_level = insight.metrics.resistance_level

    monitoring: List[str] = [
        f"지지선 {support_level:,.0f}" if support_level else "지지선 데이터 부족",
        f"저항선 {resistance_level:,.0f}" if resistance_level else "저항선 데이터 부족",
        f"연환산 변동성 {insight.metrics.volatility_pct:.2f}%",
    ]

    reasoning = [
        f"EMA 격차 {insight.metrics.trend_strength * 100:.2f}%",  # trend indication
        f"RSI {insight.metrics.rsi:.1f}",
        f"MACD 히스토그램 {insight.metrics.macd_histogram:.3f}",
    ]

    consensus = insight.timeframe_consensus

    if portfolio_plan:
        top_allocation = max(portfolio_plan.allocations, key=lambda item: item.weight, default=None)
        if top_allocation:
            reasoning.append(
                f"포트폴리오 핵심 배분: {top_allocation.symbol} {top_allocation.weight * 100:.1f}%"
            )

    if consensus and consensus.details:
        reasoning.append(
            f"다중 타임프레임 합의: {consensus.dominant_trend} ({consensus.agreement_pct:.0f}% 일치)"
        )

    if bias == "short" and insight.metrics.price_change_pct > 0:
        reasoning.append("최근 상승폭을 활용한 차익 실현 구간")
    elif bias == "long" and insight.metrics.price_change_pct < 0:
        reasoning.append("조정 구간 매수 기회")

    position_value = capital * (position_size_pct / 100) if position_size_pct else 0.0
    if position_value:
        monitoring.append(f"권장 포지션 규모 약 {position_value:,.0f} KRW")

    monitoring.extend(risk.notes)
    if consensus and consensus.details:
        for detail in consensus.details[:2]:
            if detail not in monitoring:
                monitoring.append(detail)

    return AutoPilotOrderPlan(
        market=insight.market,
        side=side,
        bias=bias,
        order_type=order_type,
        suggested_price=suggested_price,
        position_size_pct=position_size_pct,
        stop_loss_pct=max(0.5, risk.stop_loss_pct),
        take_profit_pct=risk.take_profit_pct,
        trailing_stop_pct=risk.trailing_stop_pct,
        confidence_pct=confidence_pct,
        reasoning=reasoning,
        monitoring=monitoring,
    )


def generate_copilot_synthesis(
    *,
    question: str,
    insight: MarketAIInsight,
    autopilot: AutoPilotOrderPlan,
    portfolio_plan: Optional[PortfolioAIPlan],
    mode: str,
) -> CopilotSynthesis:
    """Create a conversational style answer for the copilot endpoint."""

    summary_points = [
        f"{insight.market} {insight.interval} · {insight.regime} ({insight.confidence_pct:.1f}% 신뢰도)",
        f"EMA {insight.metrics.fast_ema:,.0f}/{insight.metrics.slow_ema:,.0f} · RSI {insight.metrics.rsi:.1f}",
        f"연환산 변동성 {insight.metrics.volatility_pct:.2f}% · 최근 변화 {insight.metrics.price_change_pct:.2f}%",
    ]

    consensus = insight.timeframe_consensus
    if consensus and consensus.details:
        summary_points.append(
            f"다중 타임프레임: {consensus.dominant_trend} ({consensus.agreement_pct:.0f}% 일치)"
        )

    highlights: List[str] = []
    if portfolio_plan:
        sorted_allocations = sorted(
            portfolio_plan.allocations,
            key=lambda allocation: allocation.weight,
            reverse=True,
        )
        for allocation in sorted_allocations[:3]:
            highlights.append(
                f"{allocation.symbol} {allocation.weight * 100:.1f}% · 기대수익 {allocation.expected_return_pct:.1f}%"
            )
        if portfolio_plan.hedging_notes:
            highlights.append(portfolio_plan.hedging_notes[0])

    action_items = []
    if autopilot.side != "flat":
        action_items.append(
            f"{autopilot.market} {('매수' if autopilot.side == 'bid' else '매도')} · 포지션 {autopilot.position_size_pct:.1f}%"
        )
        if autopilot.stop_loss_pct:
            action_items.append(f"손절 {autopilot.stop_loss_pct:.2f}% · 테이크 {autopilot.take_profit_pct or '-'}%")
        if autopilot.trailing_stop_pct:
            action_items.append(f"트레일링 스톱 {autopilot.trailing_stop_pct:.2f}%")
    else:
        action_items.append("추세 모호 · 포지션 축소 또는 관망 유지")

    risk_notices = [f"모니터링: {item}" for item in autopilot.monitoring]
    if consensus and consensus.details:
        risk_notices.append(f"컨센서스 상세: {consensus.details[0]}")
    if mode == "live":
        risk_notices.append("실거래 모드: 업비트 API 키와 주문 한도를 다시 확인하세요.")
    else:
        risk_notices.append("페이퍼 모드: 실시간 하트비트를 확인하며 가상 계좌 동기화를 점검하세요.")

    answer_lines = [
        "Sado Trade Bot 코파일럿이 질문을 검토했습니다.",
        f"질문: \"{question.strip()}\"",
        f"현재 시장은 {insight.regime} 상태이며 권장 액션은 {insight.recommended_action} 입니다.",
    ]

    if autopilot.side != "flat":
        action_text = "매수" if autopilot.side == "bid" else "매도"
        answer_lines.append(
            f"AI는 {action_text} 시나리오를 {autopilot.confidence_pct:.1f}% 신뢰도로 제안하며 포지션 규모는 자본의 "
            f"약 {autopilot.position_size_pct:.1f}%를 추천합니다."
        )
    else:
        answer_lines.append("신호가 엇갈려 관망을 권장합니다.")

    if portfolio_plan:
        answer_lines.append(
            f"포트폴리오 관점에서는 {portfolio_plan.risk_profile_label} 프로파일에 맞춰 ETF와 코인을 병행 배분했습니다."
        )

    answer_lines.append("리스크 체크리스트와 모니터링 항목을 함께 확인하세요.")

    return CopilotSynthesis(
        answer=" ".join(answer_lines),
        summary_points=summary_points,
        risk_notices=risk_notices,
        action_items=action_items,
        highlights=highlights,
        autopilot=autopilot,
        generated_at=datetime.now(timezone.utc),
    )

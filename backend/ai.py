"""AI-enhanced analytics and portfolio optimisation utilities."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from math import log, sqrt
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
    atr: float = 0.0
    hurst_exponent: float = 0.5
    bollinger_bandwidth_pct: float = 0.0
    institutional_sentiment: float = 0.5
    liquidity_score: float = 0.0
    breakout_probability: float = 0.0
    volatility_regime: str = "중립"


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
    institutional_confidence_pct: float
    institutional_commentary: str


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


@dataclass
class AssistantSynthesis:
    """Conversational summary used by the Q&A assistant endpoint."""

    answer: str
    insights: List[str]
    next_steps: List[str]
    risk_notices: List[str]
    autopilot: Optional[AutoPilotOrderPlan]
    generated_at: datetime


@dataclass
class LossRecoveryStep:
    """Actionable step for converting drawdowns back to profit."""

    title: str
    objective: str
    threshold_pct: float
    actions: List[str]
    guardrails: List[str]
    metrics: Dict[str, float]


@dataclass
class LossRecoveryPlaybook:
    """Holistic plan describing how to recover from losses safely."""

    generated_at: datetime
    realized_loss_krw: float
    unrealized_loss_krw: float
    loss_markets: List[str]
    recovery_horizon: str
    steps: List[LossRecoveryStep]
    risk_commandments: List[str]
    chart_playbook: List[str]
    institutional_briefs: List[str]
    proprietary_edge: str


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
    """Calculate MACD, signal, and histogram using full EMA series."""

    if not values:
        return 0.0, 0.0, 0.0

    ema_fast_series: List[float] = []
    ema_slow_series: List[float] = []

    fast = float(values[0])
    slow = float(values[0])
    fast_multiplier = 2 / (12 + 1)
    slow_multiplier = 2 / (26 + 1)

    for price in values:
        fast = (price - fast) * fast_multiplier + fast
        slow = (price - slow) * slow_multiplier + slow
        ema_fast_series.append(fast)
        ema_slow_series.append(slow)

    macd_series = [fast_val - slow_val for fast_val, slow_val in zip(ema_fast_series, ema_slow_series)]
    if not macd_series:
        return 0.0, 0.0, 0.0

    macd_value = macd_series[-1]
    signal = _ema(macd_series, 9)
    histogram = macd_value - signal
    return float(macd_value), float(signal), float(histogram)


def _annualised_volatility(returns: Sequence[float], interval: str) -> float:
    if not returns:
        return 0.0
    factor = _ANNUALISATION_FACTORS.get(interval, 365)
    return pstdev(returns) * sqrt(factor)


def _average_true_range(candles: Sequence[Candle], period: int = 14) -> float:
    if not candles:
        return 0.0
    tr_values: List[float] = []
    prev_close: Optional[float] = None
    for candle in candles[-max(period * 2, period) :]:
        high_low = candle.high - candle.low
        if prev_close is None:
            true_range = high_low
        else:
            true_range = max(high_low, abs(candle.high - prev_close), abs(candle.low - prev_close))
        tr_values.append(true_range)
        prev_close = candle.close
    if not tr_values:
        return 0.0
    return _ema(tr_values, min(period, len(tr_values)))


def _bollinger_bandwidth(values: Sequence[float], period: int = 20) -> float:
    if len(values) < period:
        return 0.0
    window = values[-period:]
    mean_value = fmean(window)
    if period <= 1:
        return 0.0
    std_dev = pstdev(window)
    if mean_value == 0:
        return 0.0
    upper = mean_value + 2 * std_dev
    lower = mean_value - 2 * std_dev
    if mean_value == 0:
        return 0.0
    bandwidth = ((upper - lower) / abs(mean_value)) * 100
    return float(bandwidth)


def _hurst(values: Sequence[float]) -> float:
    if len(values) < 32:
        return 0.5
    lags = range(2, min(20, len(values) // 2))
    tau: List[float] = []
    filtered_lags: List[int] = []
    for lag in lags:
        differences = [values[i + lag] - values[i] for i in range(len(values) - lag)]
        if not differences:
            continue
        deviation = pstdev(differences)
        if deviation <= 0:
            continue
        tau.append(sqrt(deviation))
        filtered_lags.append(lag)
    if len(tau) < 2 or len(filtered_lags) < 2:
        return 0.5
    log_lags = [log(lag) for lag in filtered_lags]
    log_tau = [log(value) for value in tau]
    mean_x = fmean(log_lags)
    mean_y = fmean(log_tau)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(log_lags, log_tau))
    denominator = sum((x - mean_x) ** 2 for x in log_lags)
    if denominator == 0:
        return 0.5
    slope = numerator / denominator
    hurst = max(0.0, min(1.0, 2 * slope))
    return float(hurst)


_POSITIVE_TERMS = ("상향", "호재", "기관", "승인", "도입", "투자", "수용", "ETF", "채택")
_NEGATIVE_TERMS = ("하향", "경고", "중단", "제재", "규제", "소송", "해킹", "유출")


def _news_sentiment(news_items: Optional[List[Dict[str, str]]]) -> float:
    if not news_items:
        return 0.5
    score = 0.0
    total_weight = 0.0
    for item in news_items:
        text = " ".join(
            filter(
                None,
                [
                    (item.get("title") or ""),
                    (item.get("summary") or item.get("description") or ""),
                    (item.get("source") or ""),
                ],
            )
        ).lower()
        weight = 1.0
        if "sec" in text or "federal reserve" in text or "institut" in text:
            weight += 0.4
        if "msci" in text or "s&p" in text or "ftse" in text:
            weight += 0.3
        delta = 0.0
        if any(term in text for term in _POSITIVE_TERMS):
            delta += 1.0
        if any(term in text for term in _NEGATIVE_TERMS):
            delta -= 1.0
        score += delta * weight
        total_weight += weight
    if total_weight == 0:
        return 0.5
    normalised = 0.5 + (score / (total_weight * 6))
    return max(0.0, min(1.0, normalised))


def _liquidity_pulse(candles: Sequence[Candle]) -> float:
    volumes = [candle.volume for candle in candles if candle.volume is not None]
    if len(volumes) < 10:
        return 0.0
    recent = fmean(volumes[-10:])
    baseline = fmean(volumes[-60:]) if len(volumes) >= 60 else fmean(volumes)
    if baseline == 0:
        return 0.0
    ratio = recent / baseline
    return max(0.0, min(2.0, ratio))


def _breakout_probability(
    *,
    trend_strength: float,
    bandwidth_pct: float,
    consensus: Optional[TimeframeConsensus],
    institutional_sentiment: float,
) -> float:
    base = max(0.0, min(1.0, abs(trend_strength) * 6))
    bandwidth_component = min(0.6, bandwidth_pct / 40)
    consensus_component = 0.0
    if consensus:
        consensus_component = (consensus.agreement_pct / 100) * 0.3
        if consensus.dominant_trend == "상승" and trend_strength > 0:
            consensus_component += 0.1
        if consensus.dominant_trend == "하락" and trend_strength < 0:
            consensus_component += 0.1
    institutional_component = (institutional_sentiment - 0.5) * 0.4
    probability = base + bandwidth_component + consensus_component + institutional_component
    return max(0.0, min(1.0, probability))

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

    atr_value = _average_true_range(candles[-120:])
    atr_pct = (atr_value / closes[-1] * 100) if closes and closes[-1] else 0.0
    bollinger_bandwidth = _bollinger_bandwidth(closes)
    hurst = _hurst(closes[-180:])

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

    consensus = build_timeframe_consensus(candles, market=market, interval=interval)

    news_feed = news or fetch_authoritative_news(limit=6)
    institutional_sentiment = _news_sentiment(news_feed)
    liquidity_pulse = _liquidity_pulse(candles)
    breakout_probability = _breakout_probability(
        trend_strength=trend_strength,
        bandwidth_pct=bollinger_bandwidth,
        consensus=consensus,
        institutional_sentiment=institutional_sentiment,
    )

    volatility_regime = "중립"
    if bollinger_bandwidth >= 12 or atr_pct > 2.5:
        volatility_regime = "확장"
    elif bollinger_bandwidth <= 6 or atr_pct < 1.2:
        volatility_regime = "축소"

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

    confidence += (institutional_sentiment - 0.5) * 25
    confidence += (liquidity_pulse - 1.0) * 10
    if hurst < 0.45:
        confidence -= 7
    confidence = max(10.0, min(98.0, confidence))

    summary = (
        f"{market} {interval} 캔들 기준으로 EMA 격차는 {trend_strength * 100:.2f}%이며 RSI는 {rsi:.1f} 수준입니다. "
        f"MACD 히스토그램은 {macd_norm * 100:.2f}%로 {'상승' if macd_norm >= 0 else '하락'} 압력이 우세합니다. "
        f"ATR은 {atr_pct:.2f}%이며 변동성 국면은 {volatility_regime}로 평가됩니다."
    )

    signals = [
        f"Fast EMA {fast_ema:,.0f}",
        f"Slow EMA {slow_ema:,.0f}",
        f"RSI {rsi:.1f}",
        f"MACD {macd_value:.3f} / Signal {macd_signal:.3f}",
        f"연환산 변동성 {volatility * 100:.2f}%",
        f"ATR {atr_pct:.2f}%",
        f"밴드폭 {bollinger_bandwidth:.1f}%",
        f"허스트 {hurst:.2f}",
        f"기관 센티 {institutional_sentiment * 100:.1f}%",
        f"유동성 펄스 {liquidity_pulse * 100:.1f}%",
    ]

    stop_loss_pct = max(0.8, (volatility * 100) / 3)
    stop_loss_pct *= 1 + max(0.0, (0.6 - institutional_sentiment) * 0.8)
    if hurst < 0.45:
        stop_loss_pct *= 1.1

    take_profit_pct = min(30.0, max(3.0, (volatility * 100 / 2) * (0.8 + breakout_probability)))
    trailing_stop_pct = min(18.0, max(2.0, abs(macd_norm) * 100 * (0.6 + breakout_probability)))

    position_size_pct = max(1.0, 5.0 - probability_of_trend * 2)
    position_size_pct *= (0.85 + institutional_sentiment * 0.5)
    position_size_pct *= (0.7 + min(liquidity_pulse, 1.5) * 0.2)
    if hurst < 0.45:
        position_size_pct *= 0.8
    position_size_pct = max(0.8, min(18.0, position_size_pct))

    risk_notes = [
        "변동성 확장 국면에서는 손절폭을 넓히되 포지션을 나눠 진입하세요.",
        "기관 뉴스가 부정적이면 포지션 사이즈를 축소하고 헤지 비중을 검토하세요.",
    ]
    if volatility_regime == "축소":
        risk_notes.append("박스권 가능성이 커 포지션을 줄이고 돌파 시 재진입을 권장합니다.")
    if breakout_probability > 0.55:
        risk_notes.append("돌파 확률이 높아 트레일링 스톱을 적극 활용하세요.")

    risk = RiskControlAdvice(
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
        position_size_pct=position_size_pct,
        confidence_note="신호·기관 센티먼트·유동성 기반 동적 포지션 관리",
        notes=risk_notes,
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
        atr=atr_pct,
        hurst_exponent=hurst,
        bollinger_bandwidth_pct=bollinger_bandwidth,
        institutional_sentiment=institutional_sentiment,
        liquidity_score=liquidity_pulse,
        breakout_probability=breakout_probability,
        volatility_regime=volatility_regime,
    )

    institutional_commentary = (
        f"기관/뉴스 신뢰도 {institutional_sentiment * 100:.1f}% · 유동성 펄스 {liquidity_pulse * 100:.1f}% · "
        f"돌파 확률 {breakout_probability * 100:.1f}%"
    )

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
        news=news_feed,
        timeframe_consensus=consensus,
        institutional_confidence_pct=institutional_sentiment * 100,
        institutional_commentary=institutional_commentary,
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
    """Infer a practical trading bias from the AI insight payload."""

    action_text = (insight.recommended_action or "").lower()
    if any(keyword in action_text for keyword in ("매수", "롱", "추세", "돌파")):
        return "long"
    if any(keyword in action_text for keyword in ("매도", "숏", "헤지", "현금", "청산")):
        return "short"

    metrics = insight.metrics
    trend_strength = float(getattr(metrics, "trend_strength", 0.0) or 0.0)
    rsi = float(getattr(metrics, "rsi", 50.0) or 50.0)
    macd_hist = float(getattr(metrics, "macd_histogram", 0.0) or 0.0)
    price_change = float(getattr(metrics, "price_change_pct", 0.0) or 0.0)
    breakout_probability = float(getattr(metrics, "breakout_probability", 0.0) or 0.0)
    institutional_sentiment = float(getattr(metrics, "institutional_sentiment", 0.5) or 0.5)
    liquidity_score = float(getattr(metrics, "liquidity_score", 0.5) or 0.5)

    bullish_score = 0.0
    bearish_score = 0.0

    if trend_strength > 0.01:
        bullish_score += 1.6
    elif trend_strength < -0.01:
        bearish_score += 1.6

    if rsi >= 57:
        bullish_score += 1.0
    elif rsi <= 43:
        bearish_score += 1.0

    if macd_hist >= 0:
        bullish_score += 0.7
    else:
        bearish_score += 0.7

    if price_change >= 0.5:
        bullish_score += 0.5
    elif price_change <= -0.5:
        bearish_score += 0.5

    if breakout_probability >= 0.55:
        bullish_score += 1.0 + (breakout_probability - 0.55) * 2.5
    elif breakout_probability <= 0.35:
        bearish_score += 0.9 + (0.35 - breakout_probability) * 2.0

    sentiment_delta = institutional_sentiment - 0.5
    if sentiment_delta >= 0.05:
        bullish_score += sentiment_delta * 3.0
    elif sentiment_delta <= -0.05:
        bearish_score += abs(sentiment_delta) * 3.2

    if liquidity_score >= 0.6:
        bullish_score += 0.4
    elif liquidity_score <= 0.35:
        bearish_score += 0.4

    consensus = insight.timeframe_consensus
    consensus_trend = (getattr(consensus, "dominant_trend", "") or "").lower()
    consensus_agreement = float(getattr(consensus, "agreement_pct", 0.0) or 0.0)
    if consensus_agreement:
        weight = min(1.2, consensus_agreement / 60.0)
        if "상승" in consensus_trend or "강세" in consensus_trend:
            bullish_score += weight
        elif any(keyword in consensus_trend for keyword in ("하락", "약세")):
            bearish_score += weight

    if "대기" in action_text or "중립" in action_text or "범위" in action_text:
        bullish_score -= 0.3
        bearish_score -= 0.3

    score = bullish_score - bearish_score
    if breakout_probability >= 0.7 and score >= 0.3:
        return "long"
    if breakout_probability <= 0.2 and score <= -0.4:
        return "short"

    if score >= 0.6:
        return "long"
    if score <= -0.9:
        return "short"

    regime_text = (insight.regime or "").lower()
    if any(keyword in regime_text for keyword in ("강세", "상승")):
        return "long"
    if any(keyword in regime_text for keyword in ("약세", "하락")):
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
    metrics = insight.metrics
    trend_strength = float(getattr(metrics, "trend_strength", 0.0) or 0.0)
    rsi = float(getattr(metrics, "rsi", 50.0) or 50.0)
    macd_hist = float(getattr(metrics, "macd_histogram", 0.0) or 0.0)
    price_change_pct = float(getattr(metrics, "price_change_pct", 0.0) or 0.0)
    breakout_probability = float(getattr(metrics, "breakout_probability", 0.0) or 0.0)
    institutional_sentiment = float(getattr(metrics, "institutional_sentiment", 0.5) or 0.5)
    liquidity_score = float(getattr(metrics, "liquidity_score", 0.5) or 0.5)

    contrarian_note: Optional[str] = None
    if bias == "short" and rsi <= 38 and price_change_pct <= 2.0:
        bias = "long"
        contrarian_note = "과매도 반등 시나리오"
    elif bias == "long" and rsi >= 72 and price_change_pct >= 4.0:
        bias = "neutral"
        contrarian_note = "과열 신호로 관망 전환"

    side = "bid" if bias == "long" else "ask" if bias == "short" else "flat"

    # Position sizing blends the AI risk guidance with the user's risk appetite.
    base_position = insight.risk.position_size_pct
    breakout_boost = 1.0 + max(0.0, breakout_probability - 0.5) * 1.2
    sentiment_boost = 1.0 + (institutional_sentiment - 0.5) * 0.8
    liquidity_boost = max(0.4, 1.0 + (liquidity_score - 0.5) * 0.6)
    scaled_position = base_position * max(0.4, risk_appetite + 0.2) * breakout_boost * sentiment_boost * liquidity_boost
    position_size_pct = max(0.6, min(18.0, scaled_position))

    consensus = insight.timeframe_consensus

    confidence_base = float(insight.confidence_pct)
    confidence_bonus = 0.0

    if consensus and getattr(consensus, "agreement_pct", 0):
        confidence_bonus += min(24.0, float(consensus.agreement_pct) * 0.24)

    if bias == "long":
        confidence_bonus += max(0.0, trend_strength) * 120.0
        if rsi >= 55:
            confidence_bonus += min(12.0, (rsi - 50) * 0.6)
        if macd_hist > 0:
            confidence_bonus += 5.0
        if price_change_pct > 0:
            confidence_bonus += 2.5
        confidence_bonus += breakout_probability * 45.0
        confidence_bonus += max(0.0, liquidity_score - 0.5) * 40.0
        confidence_bonus += max(0.0, institutional_sentiment - 0.5) * 70.0
    elif bias == "short":
        confidence_bonus += max(0.0, -trend_strength) * 120.0
        if rsi <= 45:
            confidence_bonus += min(12.0, (50 - rsi) * 0.6)
        if macd_hist < 0:
            confidence_bonus += 5.0
        if price_change_pct < 0:
            confidence_bonus += 2.5
        confidence_bonus += max(0.0, 0.4 - breakout_probability) * 55.0
        confidence_bonus += max(0.0, 0.5 - liquidity_score) * 35.0
        confidence_bonus += max(0.0, 0.5 - institutional_sentiment) * 70.0

    confidence_pct = confidence_base + confidence_bonus
    if bias == "neutral":
        confidence_pct = min(confidence_pct, 45.0)
    else:
        confidence_pct = max(confidence_pct, confidence_base + 10.0)
    confidence_pct = max(20.0 if bias != "neutral" else 5.0, min(95.0, confidence_pct))

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
        f"변동성 국면 {insight.metrics.volatility_regime}",
        f"돌파 확률 {insight.metrics.breakout_probability * 100:.1f}%",
    ]

    reasoning = [
        f"EMA 격차 {insight.metrics.trend_strength * 100:.2f}%",  # trend indication
        f"RSI {insight.metrics.rsi:.1f}",
        f"MACD 히스토그램 {insight.metrics.macd_histogram:.3f}",
        f"허스트 {insight.metrics.hurst_exponent:.2f}",
        f"기관 센티 {insight.metrics.institutional_sentiment * 100:.1f}%",
        f"유동성 펄스 {insight.metrics.liquidity_score * 100:.1f}%",
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

    if contrarian_note:
        reasoning.append(contrarian_note)

    monitoring.extend(risk.notes)
    if consensus and consensus.details:
        for detail in consensus.details[:2]:
            if detail not in monitoring:
                monitoring.append(detail)

    monitoring.append(insight.institutional_commentary)

    stop_loss_pct = max(0.6, min(25.0, float(getattr(risk, "stop_loss_pct", 5.0) or 5.0)))
    take_profit_pct = max(1.0, min(80.0, float(getattr(risk, "take_profit_pct", 8.0) or 8.0)))
    trailing_stop_pct = max(0.0, min(25.0, float(getattr(risk, "trailing_stop_pct", 3.0) or 0.0)))

    return AutoPilotOrderPlan(
        market=insight.market,
        side=side,
        bias=bias,
        order_type=order_type,
        suggested_price=suggested_price,
        position_size_pct=position_size_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
        confidence_pct=confidence_pct,
        reasoning=reasoning,
        monitoring=monitoring,
    )


def craft_momentum_fallback_plan(
    *,
    insight: MarketAIInsight,
    candles: Sequence[Candle],
    risk_appetite: float,
    capital: float,
    allow_short: bool,
) -> Optional[AutoPilotOrderPlan]:
    """Create a momentum-based plan when the primary AI returns a flat signal.

    The helper inspects recent price momentum, RSI, and ATR to produce a
    high-confidence directional bias so the autopilot keeps trading even when
    the conversational model prefers to wait.  ``allow_short`` should be set to
    ``True`` only when an existing position can be trimmed.
    """

    closes = [candle.close for candle in candles if candle.close > 0]
    if len(closes) < 25:
        return None

    fast = _ema(closes[-26:], 9)
    slow = _ema(closes[-26:], 21)
    baseline = (fast + slow) / 2 or slow or 1.0
    momentum_pct = (fast - slow) / baseline
    rsi_value = _rsi(closes, 14)
    atr_value = _average_true_range(candles[-40:])
    last_close = closes[-1]
    atr_pct = (atr_value / last_close) * 100 if last_close else 0.0

    bias = "neutral"
    side = "flat"
    confidence_pct = 0.0
    trigger_note: Optional[str] = None

    bullish_trigger = momentum_pct > 0.001 or (momentum_pct > 0 and rsi_value >= 53)
    bearish_trigger = momentum_pct < -0.0012 or (momentum_pct < 0 and rsi_value <= 47)

    if bullish_trigger:
        bias = "long"
        side = "bid"
        trigger_note = "EMA 모멘텀 상향 돌파 감지"
        confidence_pct = 60.0 + momentum_pct * 8000 + max(0.0, rsi_value - 50.0) * 1.2
    elif allow_short and bearish_trigger:
        bias = "short"
        side = "ask"
        trigger_note = "EMA 모멘텀 하향 돌파 감지"
        confidence_pct = 59.0 + abs(momentum_pct) * 8000 + max(0.0, 50.0 - rsi_value) * 1.2
    else:
        return None

    confidence_pct = max(55.0, min(78.0, confidence_pct))
    position_multiplier = max(0.4, min(1.8, risk_appetite + 0.6))
    position_size_pct = min(18.0, max(2.5, position_multiplier * 5.5))

    stop_loss_pct = max(0.7, min(18.0, atr_pct * 1.8 if atr_pct else 3.2))
    take_profit_pct = max(stop_loss_pct + 1.5, stop_loss_pct * 1.9)
    trailing_stop_pct = max(0.0, min(12.0, stop_loss_pct * 0.75))

    position_value = capital * (position_size_pct / 100)
    reasoning = [
        "AI 관망 신호 대체: 모멘텀 기반 전략",
        trigger_note or "EMA 스프레드 기반 모멘텀 감지",
        f"RSI {rsi_value:.1f}",
        f"ATR {atr_pct:.2f}%",
    ]

    if bias == "long":
        reasoning.append("상승 모멘텀을 활용한 공격적 매수 시나리오")
    else:
        reasoning.append("관성 약화를 활용한 방어형 청산 전략")

    monitoring = [
        f"권장 포지션 약 {position_value:,.0f} KRW",
        f"손절 {stop_loss_pct:.2f}% · 익절 {take_profit_pct:.2f}%",
    ]

    monitoring.append(insight.institutional_commentary)

    return AutoPilotOrderPlan(
        market=insight.market,
        side=side,
        bias=bias,
        order_type="market" if confidence_pct >= 62 else "limit",
        suggested_price=None,
        position_size_pct=position_size_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
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
        f"기관 신뢰도 {insight.institutional_confidence_pct:.1f}% · 돌파 확률 {insight.metrics.breakout_probability * 100:.1f}%",
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
    risk_notices.append(f"기관 브리핑: {insight.institutional_commentary}")
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


def generate_assistant_synthesis(
    *,
    question: str,
    insight: MarketAIInsight,
    autopilot: Optional[AutoPilotOrderPlan],
    include_autopilot: bool,
) -> AssistantSynthesis:
    metrics = insight.metrics

    ema_bias = "상승" if metrics.fast_ema >= metrics.slow_ema else "하락"
    rsi_state = "과열" if metrics.rsi >= 70 else "침체" if metrics.rsi <= 30 else "중립"
    atr_note = "높음" if metrics.atr > 0 and metrics.volatility_pct > 35 else "보통"

    insights = [
        f"EMA {metrics.fast_ema:,.0f}/{metrics.slow_ema:,.0f} · {ema_bias} 추세",
        f"RSI {metrics.rsi:.1f} · {rsi_state} 영역",
        f"ATR 기반 변동성 {metrics.atr:.2f} · {atr_note}",
        f"돌파 확률 {metrics.breakout_probability * 100:.1f}% · 유동성 스코어 {metrics.liquidity_score * 100:.1f}%",
    ]

    if insight.timeframe_consensus and insight.timeframe_consensus.details:
        insights.append(
            f"다중 타임프레임 {insight.timeframe_consensus.dominant_trend} ({insight.timeframe_consensus.agreement_pct:.0f}% 일치)"
        )

    if insight.institutional_commentary:
        insights.append(f"기관 코멘트: {insight.institutional_commentary}")

    if insight.summary:
        summary_line = insight.summary.strip()
        if len(summary_line) > 140:
            summary_line = summary_line[:137] + "..."
        insights.append(f"시장 요약: {summary_line}")

    top_signals = [signal for signal in (insight.signals or []) if signal][:3]
    if top_signals:
        insights.append("시그널: " + " · ".join(top_signals))

    headline = None
    for entry in insight.news or []:
        title = entry.get("title") if isinstance(entry, dict) else None
        if title:
            headline = entry
            break

    if headline:
        source = headline.get("source") or "기관"
        title = headline.get("title", "").strip()
        insights.append(f"주요 뉴스: {source} · {title}")

    next_steps: List[str] = []
    risk_notices: List[str] = []

    if include_autopilot and autopilot:
        if autopilot.side != "flat":
            action_text = "매수" if autopilot.side == "bid" else "매도"
            next_steps.append(
                f"{autopilot.market} {action_text} · 포지션 {autopilot.position_size_pct:.1f}% · 신뢰도 {autopilot.confidence_pct:.1f}%"
            )
            if autopilot.stop_loss_pct:
                risk_notices.append(f"손절 {autopilot.stop_loss_pct:.2f}% · 테이크 {autopilot.take_profit_pct or '-'}%")
            if autopilot.trailing_stop_pct:
                risk_notices.append(f"트레일링 스톱 {autopilot.trailing_stop_pct:.2f}% 유지")
        else:
            next_steps.append("신호 혼조 · 포지션 축소 또는 관망 유지")
        for monitor in autopilot.monitoring:
            risk_notices.append(f"모니터링: {monitor}")
    else:
        next_steps.append("AI 오토파일럿 미포함 · 수동 전략 확인 필요")

    if top_signals:
        next_steps.append("시그널 체크: " + " / ".join(top_signals[:2]))

    risk_notices.append(f"시장 레짐: {insight.regime} · 권장 행동: {insight.recommended_action}")
    risk_notices.append(f"기관 신뢰도 {insight.institutional_confidence_pct:.1f}% · 변동성 {metrics.volatility_pct:.2f}%")
    if metrics.volatility_regime:
        risk_notices.append(f"변동성 레짐: {metrics.volatility_regime}")
    if 0 <= metrics.probability_of_trend <= 1:
        risk_notices.append(f"추세 확률 {metrics.probability_of_trend * 100:.1f}% · 변동성 경계 유지")

    answer_lines = [
        "Sado Trade Bot 어시스턴트가 요청을 분석했습니다.",
        f"질문: \"{question.strip()}\"",
        f"현재 {insight.market} ({insight.interval})은 {ema_bias} 흐름이며 {insight.recommended_action} 시나리오가 우세합니다.",
    ]

    if include_autopilot and autopilot:
        if autopilot.side != "flat":
            action_text = "매수" if autopilot.side == "bid" else "매도"
            answer_lines.append(
                f"오토파일럿은 {action_text} 전략을 제안하며 포지션은 자본의 {autopilot.position_size_pct:.1f}%로 제한합니다."
            )
        else:
            answer_lines.append("오토파일럿은 뚜렷한 우위를 찾지 못해 대기 상태를 유지합니다.")
    else:
        answer_lines.append("오토파일럿 제안 없이 핵심 지표만 요약했습니다.")

    answer_lines.append("상세 체크리스트와 위험 경고를 함께 검토하세요.")

    return AssistantSynthesis(
        answer=" ".join(answer_lines),
        insights=insights,
        next_steps=next_steps,
        risk_notices=risk_notices,
        autopilot=autopilot if include_autopilot else None,
        generated_at=datetime.now(timezone.utc),
    )


def build_loss_recovery_playbook(
    *,
    orders: Sequence[object],
    positions: Sequence[object],
    executions: Optional[Sequence[object]] = None,
    last_insight: Optional[MarketAIInsight] = None,
    news_items: Optional[Sequence[Dict[str, str]]] = None,
) -> LossRecoveryPlaybook:
    """Synthesize a risk playbook that converts losses into a recovery roadmap."""

    now = datetime.now(timezone.utc)
    realized_loss = 0.0
    unrealized_loss = 0.0
    loss_markets: Dict[str, float] = defaultdict(float)
    loss_pct_values: List[float] = []

    for order in orders:
        market = str(getattr(order, "market", "KRW-BTC")).upper()
        pnl = float(getattr(order, "realized_pnl", 0.0) or 0.0)
        price = float(getattr(order, "price", 0.0) or 0.0)
        volume = float(getattr(order, "volume", 0.0) or 0.0)
        notional = abs(price * volume)
        if pnl < 0:
            realized_loss += -pnl
            loss_markets[market] += -pnl
            if notional > 1e-9:
                loss_pct_values.append(pnl / notional * 100)

    for position in positions:
        market = str(getattr(position, "market", "KRW-BTC")).upper()
        pnl = float(getattr(position, "unrealized_pnl", 0.0) or 0.0)
        if pnl < 0:
            unrealized_loss += -pnl
            loss_markets[market] += -pnl

    total_loss = realized_loss + unrealized_loss
    if total_loss > 5_000_000:
        horizon = "장기 회복 (90일 이상)"
    elif total_loss > 1_500_000:
        horizon = "중기 회복 (30~60일)"
    elif total_loss > 0:
        horizon = "단기 회복 (14~21일)"
    else:
        horizon = "예방 중심 (손실 없음)"

    average_loss_pct = abs(fmean(loss_pct_values)) if loss_pct_values else 0.0
    worst_loss_pct = abs(min(loss_pct_values)) if loss_pct_values else 0.0
    loss_market_list = [market for market, _ in sorted(loss_markets.items(), key=lambda item: item[1], reverse=True)]

    insight_metrics = last_insight.metrics if last_insight else None
    chart_playbook: List[str] = []
    if insight_metrics:
        chart_playbook.append(
            f"EMA {insight_metrics.fast_ema:.2f}/{insight_metrics.slow_ema:.2f} · 추세 강도 {insight_metrics.trend_strength:.2f}"
        )
        chart_playbook.append(
            f"RSI {insight_metrics.rsi:.1f} · 허스트 {insight_metrics.hurst_exponent:.2f} · ATR {insight_metrics.atr:.2f}"
        )
        chart_playbook.append(
            f"MACD {insight_metrics.macd:.2f} vs 시그널 {insight_metrics.macd_signal:.2f} · 변동성 {insight_metrics.volatility_pct:.2f}%"
        )
    else:
        chart_playbook = [
            "EMA·RSI·ATR 조합으로 추세/변동성 확인",
            "손절은 최근 스윙 저점, 익절은 2R 이상으로 설정",
        ]

    if insight_metrics and insight_metrics.breakout_probability > 0:
        chart_playbook.append(
            f"돌파 확률 {insight_metrics.breakout_probability * 100:.1f}% 구간에서 포지션 스케일링"
        )

    executions_count = len(executions or [])

    base_guardrail = max(5.0, worst_loss_pct or 5.0)
    dynamic_risk = max(2.5, average_loss_pct * 1.5 if average_loss_pct else 3.0)

    steps: List[LossRecoveryStep] = [
        LossRecoveryStep(
            title="손실 구조 정밀 진단",
            objective="누적 손실의 원인·시장·전략 패턴을 파악합니다.",
            threshold_pct=base_guardrail,
            actions=[
                "손실 발생 상위 종목 재평가 및 불필요 포지션 축소",
                "실거래·페이퍼 손실 전환율을 분석해 전략별 성과를 분리",
                "관망 중인 오토파일럿 루프 로그를 검토하고 오류를 제거",
            ],
            guardrails=[
                "손실 확정 전 추가 매수 금지",
                "손절 재설정은 ATR x 1.5 이상 여유 확보",
            ],
            metrics={
                "loss_markets": float(len(loss_market_list)),
                "worst_loss_pct": float(worst_loss_pct),
                "average_loss_pct": float(average_loss_pct),
            },
        ),
        LossRecoveryStep(
            title="리스크 버짓 재편성과 재진입 조건",
            objective="현금흐름을 방어하고 손실을 보전할 트레이딩 창을 정의합니다.",
            threshold_pct=dynamic_risk,
            actions=[
                "자본 대비 포지션 규모를 1회 2% 이하로 축소",
                "손실 종목은 EMA 재돌파 또는 RSI 50 상향 시점에만 재진입",
                "기관 뉴스 기반 모멘텀 이벤트(ETF 유입·규제 이슈) 체크",
            ],
            guardrails=[
                "재진입 전 동일 손실 종목 2회 연속 양봉 확인",
                "위험 노출 시간(Time-in-Market)을 45% 이하로 유지",
            ],
            metrics={
                "executions_reviewed": float(executions_count),
                "risk_budget_pct": float(dynamic_risk),
            },
        ),
        LossRecoveryStep(
            title="AI 하이브리드 공략 전략",
            objective="AI 추천 TOP5·기관 데이터·멀티타임프레임을 결합한 천재 투자 알고리즘 가동",
            threshold_pct=max(8.0, dynamic_risk * 1.5),
            actions=[
                "AI 추천 TOP5 중 모멘텀·기본면 우수 종목 2~3개 선정",
                "다중 타임프레임 합의도가 65% 이상일 때만 순차 매수",
                "Synology Chat 알림을 통해 실시간 리스크·성과 모니터",
            ],
            guardrails=[
                "추세 확률이 55% 미만이면 자동 관망",
                "기관 센티먼트 40% 이하 종목은 공격 비중 축소",
            ],
            metrics={
                "trend_probability": float(
                    (insight_metrics.probability_of_trend * 100)
                    if (insight_metrics and insight_metrics.probability_of_trend is not None)
                    else 0.0
                ),
                "institutional_sentiment": float(
                    (insight_metrics.institutional_sentiment * 100)
                    if (insight_metrics and insight_metrics.institutional_sentiment is not None)
                    else 0.0
                ),
            },
        ),
    ]

    risk_commandments = [
        "한 종목 최대 손실 5% 이내, 계좌 손실 12% 돌파 시 즉시 관망",
        "손실 전환을 위한 승률·손익비 점검: 최소 승률 45% + 기대수익 1.5R 확보",
        "포트폴리오 안정: 미국 ETF 60%, 코인 40% 내에서 공격 비중 자동 조정",
    ]

    if insight_metrics:
        risk_commandments.append(
            f"현재 변동성 {insight_metrics.volatility_pct:.2f}% · ATR {insight_metrics.atr:.2f} 기준으로 손절 폭 재조정"
        )

    institutional_briefs: List[str] = []
    for item in news_items or []:
        source = item.get("source") or "기관 리서치"
        title = item.get("title") or item.get("summary") or "시장 브리핑"
        institutional_briefs.append(f"{source}: {title}")

    if not institutional_briefs:
        institutional_briefs.append("신뢰 가능한 기관 헤드라인 미확보 · 자체 리서치 강화 필요")

    proprietary_edge_parts = [
        "EMA·RSI·ATR 기반 차트 분석",
        "기관 뉴스 및 센티먼트 필터",
        "AI 추천 TOP5 & 자동 포트폴리오 회전",
    ]
    proprietary_edge = " + ".join(proprietary_edge_parts)

    if insight_metrics:
        proprietary_edge += f" · 추세확률 {insight_metrics.probability_of_trend * 100:.1f}% 기반 동적 포지셔닝"

    return LossRecoveryPlaybook(
        generated_at=now,
        realized_loss_krw=realized_loss,
        unrealized_loss_krw=unrealized_loss,
        loss_markets=loss_market_list,
        recovery_horizon=horizon,
        steps=steps,
        risk_commandments=risk_commandments,
        chart_playbook=chart_playbook,
        institutional_briefs=institutional_briefs,
        proprietary_edge=proprietary_edge,
    )

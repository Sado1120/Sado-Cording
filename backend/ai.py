"""AI-enhanced analytics and portfolio optimisation utilities."""
from __future__ import annotations

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

    returns: List[float] = []
    for prev, current in zip(closes[:-1], closes[1:]):
        if prev == 0:
            continue
        returns.append((current / prev) - 1)

    volatility = _annualised_volatility(returns[-120:], interval)
    trend_strength = 0.0 if slow_ema == 0 else (fast_ema - slow_ema) / slow_ema
    regime_score = (trend_strength * 0.6) + ((50 - abs(rsi - 50)) / 100 * 0.2) + (macd_hist * 0.2)
    probability_of_trend = max(0.0, min(1.0, abs(trend_strength) * 5 + abs(macd_hist)))

    price_change_pct = returns[-1] * 100 if returns else 0.0
    support, resistance = _support_resistance(candles)

    if trend_strength > 0.01 and rsi < 68 and macd_hist > 0:
        regime = "강세 추세"
        recommended_action = "추세 추종 매수"
        confidence = min(95.0, 55 + probability_of_trend * 40)
    elif trend_strength < -0.01 and rsi > 32 and macd_hist < 0:
        regime = "약세 추세"
        recommended_action = "헤지 또는 현금 비중 확대"
        confidence = min(90.0, 50 + probability_of_trend * 35)
    else:
        regime = "중립 / 박스권"
        recommended_action = "범위 매매 또는 대기"
        confidence = 45.0

    summary = (
        f"{market} {interval} 캔들 기준으로 EMA 격차는 {trend_strength * 100:.2f}%이며 RSI는 {rsi:.1f} 수준입니다. "
        f"MACD 히스토그램은 {macd_hist:.3f}로 {'상승' if macd_hist >= 0 else '하락'} 압력이 우세합니다."
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
        trailing_stop_pct=min(15.0, max(2.0, abs(macd_hist) * 10)),
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
        macd_histogram=macd_hist,
        volatility_pct=volatility * 100,
        trend_strength=trend_strength,
        regime_score=regime_score,
        probability_of_trend=probability_of_trend,
        price_change_pct=price_change_pct,
        support_level=support,
        resistance_level=resistance,
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
        news=news or fetch_authoritative_news(limit=4),
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
    if candle_fetcher:
        for asset in aggressive_assets:
            if not asset.market:
                continue
            candles = candle_fetcher(asset.market, "minute60", 120)
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
            candles = candle_fetcher(market_code, "minute60", 120)
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

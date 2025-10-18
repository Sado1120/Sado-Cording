from datetime import datetime, timezone

from backend.app import (
    get_market_insights,
    get_market_intelligence,
    get_news,
    get_upbit_candles,
    optimize_portfolio,
)
from backend.market import (
    MarketData,
    MarketInfo,
    MarketList,
    build_market_insights,
    fetch_authoritative_news,
    fetch_upbit_candles,
    fetch_upbit_markets,
)
from backend.schemas import PortfolioOptimizationRequest
from backend.trading import Candle, generate_synthetic_prices
import backend.app as app_module


def test_fetch_upbit_candles_fallback(monkeypatch):
    def raise_error(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr("backend.market.urlopen", raise_error)
    data = fetch_upbit_candles("KRW-BTC", interval="minute1", count=20)
    assert data.source == "synthetic"
    assert len(data.candles) == 20


def test_build_market_insights_from_synthetic():
    candles = generate_synthetic_prices(days=60, seed=123)
    insights = build_market_insights(candles, market="KRW-BTC", interval="minute1")
    assert "ema_fast" in insights
    assert "rsi" in insights
    assert insights["market"] == "KRW-BTC"
    assert insights["interval"] == "minute1"


def test_fetch_upbit_markets_fallback(monkeypatch):
    def raise_error(*args, **kwargs):
        raise OSError("down")

    monkeypatch.setattr("backend.market.urlopen", raise_error)
    listing = fetch_upbit_markets()
    assert listing.source == "fallback"
    assert listing.markets
    assert all(market.market.startswith("KRW-") for market in listing.markets)


def test_fetch_authoritative_news_fallback(monkeypatch):
    def raise_error(*args, **kwargs):
        raise OSError("timeout")

    monkeypatch.setattr("backend.market.urlopen", raise_error)
    headlines = fetch_authoritative_news(limit=3)
    assert len(headlines) == 3
    assert all("title" in item for item in headlines)


def test_market_endpoints(monkeypatch):
    sample_candles = [
        Candle(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=1_000_000,
            high=1_010_000,
            low=990_000,
            close=1_005_000,
            volume=12.5,
        ),
        Candle(
            timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
            open=1_005_000,
            high=1_020_000,
            low=995_000,
            close=1_015_000,
            volume=15.0,
        ),
    ]

    def fake_fetch(*args, **kwargs):
        return MarketData(candles=sample_candles, source="synthetic")

    monkeypatch.setattr("backend.app.fetch_upbit_candles", fake_fetch)

    candles_response = get_upbit_candles(market="KRW-BTC", interval="minute1")
    assert candles_response.source == "synthetic"
    assert len(candles_response.candles) == len(sample_candles)

    insights_response = get_market_insights(market="KRW-BTC", interval="minute1")
    assert insights_response.market == "KRW-BTC"
    assert insights_response.rsi >= 0
    assert insights_response.recommended_action

    monkeypatch.setattr(
        "backend.app.fetch_authoritative_news",
        lambda limit=8: [
            {
                "title": "Sample",
                "url": "https://example.com",
                "source": "Example",
                "published_at": "Now",
            }
        ],
    )
    news_response = get_news()
    assert news_response.items

    monkeypatch.setattr(
        "backend.app.fetch_upbit_markets",
        lambda only_krw=True: MarketList(
            markets=[
                MarketInfo(
                    market="KRW-BTC",
                    korean_name="비트코인",
                    english_name="Bitcoin",
                    base_currency="KRW",
                    quote_currency="BTC",
                    market_warning="NONE",
                    trading_suspended=False,
                )
            ],
            source="upbit",
        ),
    )

    markets_response = app_module.list_markets()
    assert markets_response.source == "upbit"
    assert markets_response.markets[0].market == "KRW-BTC"
    assert markets_response.groups
    group_keys = {group.key for group in markets_response.groups}
    assert "krw" in group_keys


def test_ai_endpoints(monkeypatch):
    synthetic = generate_synthetic_prices(days=200, seed=99)

    def fake_fetch(*args, **kwargs):
        return MarketData(candles=synthetic, source="synthetic")

    monkeypatch.setattr("backend.app.fetch_upbit_candles", fake_fetch)
    monkeypatch.setattr(
        "backend.app.fetch_authoritative_news",
        lambda limit=5: [
            {
                "title": "Authoritative Insight",
                "url": "https://example.com",
                "source": "Example",
                "published_at": "Just now",
            }
        ],
    )

    ai_response = get_market_intelligence(market="KRW-BTC", interval="minute60")
    assert ai_response.recommended_action
    assert ai_response.metrics.rsi >= 0

    request = PortfolioOptimizationRequest(risk_appetite=0.55, capital=15_000_000)
    portfolio = optimize_portfolio(request)
    assert portfolio.allocations
    assert portfolio.expected_return_pct > 0


def test_diagnostics_endpoint(monkeypatch):
    synthetic = generate_synthetic_prices(days=120, seed=5)

    def fake_fetch(*args, **kwargs):
        return MarketData(candles=synthetic, source="synthetic")

    monkeypatch.setattr("backend.app.fetch_upbit_candles", fake_fetch)

    diagnostics = app_module._diagnostics_summary()
    assert diagnostics.checks
    names = {check.name for check in diagnostics.checks}
    assert "업비트 연결" in names

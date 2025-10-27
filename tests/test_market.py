from datetime import datetime, timedelta, timezone

from backend.app import (
    get_market_insights,
    get_market_intelligence,
    get_news,
    get_upbit_candles,
    optimize_portfolio,
)
from backend.market import (
    MarketData,
    MarketDataError,
    MarketInfo,
    MarketList,
    attempt_upbit_self_heal,
    build_market_insights,
    fetch_authoritative_news,
    fetch_upbit_candles,
    fetch_upbit_markets,
    get_upbit_recovery_log,
    is_upbit_network_operational,
    reset_market_state_for_tests,
)
from backend.schemas import PortfolioOptimizationRequest
from backend.trading import Candle, generate_synthetic_prices
import backend.app as app_module


def test_fetch_upbit_candles_fallback(monkeypatch):
    import backend.market as market_module

    def raise_error(*args, **kwargs):
        raise market_module.MarketDataError("network down")

    monkeypatch.setattr(market_module, "_request_upbit", raise_error)
    data = fetch_upbit_candles("KRW-BTC", interval="minute1", count=20)
    assert data.source == "synthetic"
    assert len(data.candles) == 20
    assert data.status == "down"
    assert data.message
    second = fetch_upbit_candles("KRW-BTC", interval="minute1", count=20)
    assert [c.close for c in data.candles] == [c.close for c in second.candles]
    reset_market_state_for_tests()


def test_is_upbit_network_operational_reflects_down_state(monkeypatch):
    import backend.market as market_module

    reset_market_state_for_tests()
    assert is_upbit_network_operational() is True

    def raise_error(*args, **kwargs):
        raise market_module.MarketDataError("network down")

    monkeypatch.setattr(market_module, "_request_upbit", raise_error)

    data = fetch_upbit_candles("KRW-BTC", interval="minute1", count=20)
    assert data.source == "synthetic"
    assert is_upbit_network_operational() is False

    reset_market_state_for_tests()


def test_httpx_client_reuse_and_reset():
    import backend.market as market_module

    reset_market_state_for_tests()

    first = market_module._get_httpx_client()
    second = market_module._get_httpx_client()

    if market_module.httpx is None:
        assert first is None
        assert second is None
    else:
        assert first is second

    reset_market_state_for_tests()

    if market_module.httpx is not None:
        third = market_module._get_httpx_client()
        assert third is not None
        assert third is not first

    reset_market_state_for_tests()


def test_attempt_upbit_self_heal_skipped(monkeypatch):
    import backend.market as market_module

    reset_market_state_for_tests()
    monkeypatch.setattr(market_module, "_UPBIT_ENABLE_NETWORK", False, raising=False)

    result = attempt_upbit_self_heal("unit-test")
    assert result["status"] == "skipped"
    log = get_upbit_recovery_log(limit=1)
    assert log and log[0]["action"] == "network-disabled"

    reset_market_state_for_tests()


def test_attempt_upbit_self_heal_success(monkeypatch):
    import backend.market as market_module

    reset_market_state_for_tests()
    monkeypatch.setattr(market_module, "_UPBIT_ENABLE_NETWORK", True, raising=False)

    call_counter = {"count": 0}

    def fake_request(path, params=None, allow_self_heal=True):  # noqa: D401
        call_counter["count"] += 1
        assert allow_self_heal is False
        return [{"market": "KRW-BTC"}]

    monkeypatch.setattr(market_module, "_request_upbit", fake_request)

    result = attempt_upbit_self_heal("unit-test")
    assert result["status"] == "success"
    assert call_counter["count"] == 1
    entries = get_upbit_recovery_log(limit=5)
    assert any(entry["action"] == "probe-market-directory" for entry in entries)

    reset_market_state_for_tests()


def test_attempt_upbit_self_heal_cooldown(monkeypatch):
    import backend.market as market_module

    reset_market_state_for_tests()
    monkeypatch.setattr(market_module, "_UPBIT_ENABLE_NETWORK", True, raising=False)

    clock = {"value": 0.0}

    def fake_monotonic():
        return clock["value"]

    monkeypatch.setattr(market_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(
        market_module,
        "_request_upbit",
        lambda *args, **kwargs: [{"market": "KRW-BTC"}],
    )

    clock["value"] = 100.0
    first = attempt_upbit_self_heal("unit-test")
    assert first["status"] == "success"

    clock["value"] = 105.0
    second = attempt_upbit_self_heal("unit-test")
    assert second["status"] == "cooldown"

    reset_market_state_for_tests()


def test_fetch_upbit_candles_uses_cached_live_data(monkeypatch):
    import backend.market as market_module

    reset_market_state_for_tests()

    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    payload = [
        {
            "candle_date_time_utc": (now + timedelta(minutes=index)).isoformat(),
            "opening_price": 1_000_000 + index * 1_000,
            "high_price": 1_001_000 + index * 1_000,
            "low_price": 999_000 + index * 1_000,
            "trade_price": 1_002_000 + index * 1_000,
            "candle_acc_trade_volume": 10 + index,
        }
        for index in range(2)
    ]

    monkeypatch.setattr(market_module, "_request_upbit", lambda *_, **__: payload)
    live = fetch_upbit_candles("KRW-BTC", interval="minute1", count=2)
    assert live.source == "upbit"
    assert not live.stale

    def fail_request(*args, **kwargs):
        raise market_module.MarketDataError("temporary failure")

    monkeypatch.setattr(market_module, "_request_upbit", fail_request)
    cached = fetch_upbit_candles("KRW-BTC", interval="minute1", count=2)
    assert cached.source == "upbit_stale"
    assert cached.stale is True
    assert [c.close for c in cached.candles] == [c.close for c in live.candles]

    reset_market_state_for_tests()


def test_fetch_upbit_candles_handles_rate_limit_with_cache(monkeypatch):
    import backend.market as market_module

    reset_market_state_for_tests()

    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    payload = [
        {
            "candle_date_time_utc": (now + timedelta(minutes=index)).isoformat(),
            "opening_price": 1_000_000 + index * 1_000,
            "high_price": 1_001_000 + index * 1_000,
            "low_price": 999_000 + index * 1_000,
            "trade_price": 1_002_000 + index * 1_000,
            "candle_acc_trade_volume": 10 + index,
        }
        for index in range(3)
    ]

    monkeypatch.setattr(market_module, "_request_upbit", lambda *_, **__: payload)
    live = fetch_upbit_candles("KRW-BTC", interval="minute1", count=3)
    assert live.source == "upbit"

    def raise_rate_limit(*args, **kwargs):
        raise market_module.MarketRateLimitError("rate limit")

    monkeypatch.setattr(market_module, "_request_upbit", raise_rate_limit)
    limited = fetch_upbit_candles("KRW-BTC", interval="minute1", count=3)
    assert limited.source == "upbit_stale"
    assert limited.status in {"warning", "unknown"}
    assert any(c.close for c in limited.candles)

    reset_market_state_for_tests()


def test_fetch_upbit_candles_rate_limit_without_cache(monkeypatch):
    import backend.market as market_module

    reset_market_state_for_tests()

    def raise_rate_limit(*args, **kwargs):
        raise market_module.MarketRateLimitError("rate limit")

    monkeypatch.setattr(market_module, "_request_upbit", raise_rate_limit)
    data = fetch_upbit_candles("KRW-BTC", interval="minute1", count=3)
    assert data.source == "synthetic"
    assert data.status in {"warning", "unknown"}

    reset_market_state_for_tests()


def test_fetch_upbit_candles_offline_mode(monkeypatch):
    import importlib

    monkeypatch.setenv("UPBIT_ENABLE_NETWORK", "0")
    module = importlib.reload(__import__("backend.market", fromlist=["*"]))
    data = module.fetch_upbit_candles("KRW-BTC", interval="minute1", count=20)
    assert data.source == "synthetic"
    assert data.status == "down"

    listing = module.fetch_upbit_markets()
    assert listing.source == "fallback"
    assert listing.status == "down"

    # Restore module state for other tests
    monkeypatch.delenv("UPBIT_ENABLE_NETWORK")
    importlib.reload(__import__("backend.market", fromlist=["*"]))


def test_upbit_base_url_normalises_trailing_slash(monkeypatch):
    import importlib

    monkeypatch.setenv("UPBIT_BASE_URL", "https://proxy.example.com/api/")
    module = importlib.reload(__import__("backend.market", fromlist=["*"]))
    assert module._UPBIT_API_BASE == "https://proxy.example.com/api"  # type: ignore[attr-defined]

    monkeypatch.delenv("UPBIT_BASE_URL", raising=False)
    importlib.reload(__import__("backend.market", fromlist=["*"]))


def test_build_market_insights_from_synthetic():
    candles = generate_synthetic_prices(days=60, seed=123)
    insights = build_market_insights(candles, market="KRW-BTC", interval="minute1")
    assert "ema_fast" in insights
    assert "rsi" in insights
    assert insights["market"] == "KRW-BTC"
    assert insights["interval"] == "minute1"


def test_fetch_upbit_markets_fallback(monkeypatch):
    import backend.market as market_module

    def raise_error(*args, **kwargs):
        raise market_module.MarketDataError("down")

    monkeypatch.setattr(market_module, "_request_upbit", raise_error)
    listing = fetch_upbit_markets()
    assert listing.source == "fallback"
    assert listing.markets
    assert len(listing.markets) >= 100
    assert all(market.market.startswith("KRW-") for market in listing.markets)
    assert listing.status == "down"
    assert "내장" in listing.message


def test_request_upbit_retries_before_failing(monkeypatch):
    import backend.market as market_module

    monkeypatch.setattr(market_module, "httpx", None)
    attempts = {"count": 0}

    class DummyStream:
        def __init__(self, payload: str) -> None:
            self._payload = payload

        def __enter__(self):  # noqa: D401 - context manager protocol
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: D401 - context manager protocol
            return False

        def read(self) -> bytes:
            return self._payload.encode("utf-8")

    def fake_urlopen(request, timeout=None):  # noqa: ARG001
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise market_module.URLError("temporary failure")
        return DummyStream('{"status": "ok"}')

    monkeypatch.setattr(market_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(market_module.time, "sleep", lambda _s: None)

    payload = market_module._request_upbit("/v1/status", None)  # type: ignore[attr-defined]
    assert payload == {"status": "ok"}
    assert attempts["count"] == 3


def test_fetch_authoritative_news_fallback(monkeypatch):
    def raise_error(*args, **kwargs):
        raise OSError("timeout")

    monkeypatch.setattr("backend.market.urlopen", raise_error)
    monkeypatch.setattr(
        "backend.market._news_state",
        {"status": "unknown", "checked_at": 0.0, "cached_items": [], "cached_at": 0.0},
    )
    headlines = fetch_authoritative_news(limit=3)
    assert len(headlines) == 3
    assert all("title" in item for item in headlines)


def test_fetch_authoritative_news_backoff(monkeypatch):
    call_count = {"value": 0}

    def raise_error(*args, **kwargs):
        call_count["value"] += 1
        raise OSError("timeout")

    monkeypatch.setattr("backend.market.urlopen", raise_error)
    monkeypatch.setattr(
        "backend.market._news_state",
        {"status": "unknown", "checked_at": 0.0, "cached_items": [], "cached_at": 0.0},
    )

    monotonic_value = {"value": 0.0}

    def fake_monotonic():
        return monotonic_value["value"]

    monkeypatch.setattr("backend.market.time.monotonic", fake_monotonic)

    first = fetch_authoritative_news(limit=2)
    first_calls = call_count["value"]
    assert first

    monotonic_value["value"] = 100.0  # Still within backoff window
    second = fetch_authoritative_news(limit=2)

    assert call_count["value"] == first_calls
    assert second == first


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
    assert markets_response.status == "unknown"
    assert markets_response.message == ""


def test_market_recommendations_endpoint(monkeypatch):
    markets = [
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
            market="KRW-ERR",
            korean_name="에러코인",
            english_name="ErrorCoin",
            base_currency="KRW",
            quote_currency="ERR",
            market_warning="NONE",
            trading_suspended=False,
        ),
    ]

    monkeypatch.setattr(
        "backend.app.fetch_upbit_markets",
        lambda only_krw=True: MarketList(markets=markets, source="upbit"),
    )

    def fake_fetch(market: str, interval: str = "minute60", count: int = 200):
        if market.endswith("ERR"):
            raise MarketDataError("network error")
        seed = sum(ord(char) for char in market)
        candles = generate_synthetic_prices(days=200, seed=seed)
        return MarketData(candles=candles, source="synthetic")

    monkeypatch.setattr("backend.app.fetch_upbit_candles", fake_fetch)

    response = app_module.get_market_recommendations(base="KRW", interval="minute60", limit=3)

    assert response.limit == 3
    assert response.analysed_markets == 2
    assert response.analysis_source == "synthetic"
    assert 1 <= len(response.recommendations) <= 3
    assert any(item.market == "KRW-BTC" for item in response.recommendations)
    assert any(item.market == "KRW-ETH" for item in response.recommendations)
    assert response.errors and any("KRW-ERR" in error for error in response.errors)


def test_market_recommendations_internal_error(monkeypatch):
    import backend.app as app_module

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "_compute_market_recommendations", boom)

    response = app_module.get_market_recommendations(base="KRW", interval="minute60", limit=4)

    assert response.limit == 4
    assert response.analysed_markets == 0
    assert response.recommendations == []
    assert response.analysis_source == "synthetic"
    assert response.errors and "boom" in response.errors[0]


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
    assert 0 <= ai_response.technical_confluence.score <= 100
    assert ai_response.technical_confluence.label

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


def test_ai_self_check_endpoint(monkeypatch):
    synthetic = generate_synthetic_prices(days=90, seed=11)

    def fake_fetch(*args, **kwargs):
        return MarketData(candles=synthetic, source="synthetic")

    monkeypatch.setattr("backend.app.fetch_upbit_candles", fake_fetch)

    payload = app_module.get_ai_self_check(force=True)
    assert payload.summary
    assert payload.overall_severity in {"nominal", "warning", "critical"}
    assert isinstance(payload.issues, list)
    assert payload.issues, "Self-check should report at least one issue or info entry"

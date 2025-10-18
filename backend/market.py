"""Market data utilities for Upbit candles and institutional news."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Literal, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .trading import Candle, generate_synthetic_prices, generate_market_insights


Interval = Literal[
    "minute1",
    "minute3",
    "minute5",
    "minute15",
    "minute30",
    "minute60",
    "minute240",
    "day",
    "week",
    "month",
]


@dataclass
class MarketData:
    candles: List[Candle]
    source: Literal["upbit", "synthetic"]


@dataclass
class MarketInfo:
    market: str
    korean_name: str
    english_name: str
    base_currency: str
    quote_currency: str
    market_warning: str
    trading_suspended: bool


@dataclass
class MarketList:
    markets: List[MarketInfo]
    source: Literal["upbit", "fallback"]


class MarketDataError(RuntimeError):
    """Raised when market data cannot be retrieved."""


_INTERVAL_PATHS: Dict[Interval, Tuple[str, Optional[str]]] = {
    "minute1": ("minutes", "1"),
    "minute3": ("minutes", "3"),
    "minute5": ("minutes", "5"),
    "minute15": ("minutes", "15"),
    "minute30": ("minutes", "30"),
    "minute60": ("minutes", "60"),
    "minute240": ("minutes", "240"),
    "day": ("days", None),
    "week": ("weeks", None),
    "month": ("months", None),
}

_UPBIT_API_BASE = "https://api.upbit.com"

_UPBIT_ENABLE_NETWORK = os.getenv("UPBIT_ENABLE_NETWORK", "1").lower() not in {
    "0",
    "false",
    "no",
}
_UPBIT_NETWORK_BACKOFF_SECONDS = 300.0
_upbit_network_state = {
    "status": "unknown",  # "up" | "down" | "unknown"
    "checked_at": 0.0,
}


_FALLBACK_MARKETS: List[MarketInfo] = [
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
        market="KRW-SOL",
        korean_name="솔라나",
        english_name="Solana",
        base_currency="KRW",
        quote_currency="SOL",
        market_warning="NONE",
        trading_suspended=False,
    ),
    MarketInfo(
        market="KRW-XRP",
        korean_name="리플",
        english_name="Ripple",
        base_currency="KRW",
        quote_currency="XRP",
        market_warning="NONE",
        trading_suspended=False,
    ),
]


def _parse_upbit_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        # Upbit may return without timezone; assume UTC.
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def _upbit_network_down() -> bool:
    if not _UPBIT_ENABLE_NETWORK:
        return True
    state = _upbit_network_state
    if state["status"] != "down":
        return False
    elapsed = time.monotonic() - state["checked_at"]
    return elapsed < _UPBIT_NETWORK_BACKOFF_SECONDS


def _mark_network(status: str) -> None:
    _upbit_network_state["status"] = status
    _upbit_network_state["checked_at"] = time.monotonic()


def fetch_upbit_candles(
    market: str = "KRW-BTC",
    *,
    interval: Interval = "minute1",
    count: int = 120,
) -> MarketData:
    """Fetch recent candles from the Upbit public REST API.

    When the upstream request fails the function falls back to deterministic
    synthetic prices so that downstream analytics continue to operate without
    interruption.
    """

    market = market.upper()
    if interval not in _INTERVAL_PATHS:
        raise MarketDataError("지원하지 않는 캔들 주기입니다.")

    count = max(10, min(count, 200))
    if _upbit_network_down():
        synthetic = generate_synthetic_prices(days=count)
        return MarketData(candles=synthetic, source="synthetic")

    interval_path, unit = _INTERVAL_PATHS[interval]
    if unit:
        path = f"/v1/candles/{interval_path}/{unit}"
    else:
        path = f"/v1/candles/{interval_path}"

    query = urlencode({"market": market, "count": count})
    url = f"{_UPBIT_API_BASE}{path}?{query}"
    request = Request(url, headers={"Accept": "application/json"})

    try:
        with urlopen(request, timeout=3) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                raise MarketDataError("업비트에서 빈 응답을 받았습니다.")
            payload = json.loads(raw)
    except (HTTPError, URLError, TimeoutError, OSError):  # pragma: no cover - integration failures
        _mark_network("down")
        synthetic = generate_synthetic_prices(days=count)
        return MarketData(candles=synthetic, source="synthetic")
    except json.JSONDecodeError as exc:
        _mark_network("down")
        raise MarketDataError("업비트 응답을 해석하지 못했습니다.") from exc
    else:
        _mark_network("up")

    if not isinstance(payload, Iterable):
        raise MarketDataError("업비트 응답 형식이 올바르지 않습니다.")

    candles: List[Candle] = []
    for item in payload:
        try:
            timestamp_str = item.get("candle_date_time_utc") or item.get("timestamp")
            if isinstance(timestamp_str, (int, float)):
                timestamp = datetime.fromtimestamp(float(timestamp_str) / 1000, tz=timezone.utc)
            else:
                timestamp = _parse_upbit_timestamp(str(timestamp_str))
            candle = Candle(
                timestamp=timestamp,
                open=float(item["opening_price"]),
                high=float(item["high_price"]),
                low=float(item["low_price"]),
                close=float(item["trade_price"]),
                volume=float(item["candle_acc_trade_volume"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError("업비트 캔들 데이터를 읽을 수 없습니다.") from exc
        candles.append(candle)

    candles.reverse()  # Upbit returns newest first
    return MarketData(candles=candles, source="upbit")


def _fallback_markets(only_krw: bool) -> List[MarketInfo]:
    if only_krw:
        return list(_FALLBACK_MARKETS)
    return list(_FALLBACK_MARKETS)


def fetch_upbit_markets(*, only_krw: bool = True) -> MarketList:
    """Return tradable markets from Upbit or a deterministic fallback list."""

    if _upbit_network_down():
        return MarketList(markets=_fallback_markets(only_krw), source="fallback")

    url = f"{_UPBIT_API_BASE}/v1/market/all?isDetails=true"
    request = Request(url, headers={"Accept": "application/json"})

    try:
        with urlopen(request, timeout=3) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                raise MarketDataError("업비트에서 빈 마켓 목록을 받았습니다.")
            payload = json.loads(raw)
    except (HTTPError, URLError, TimeoutError, OSError):  # pragma: no cover - network failure
        _mark_network("down")
        return MarketList(markets=_fallback_markets(only_krw), source="fallback")
    except json.JSONDecodeError:  # pragma: no cover - malformed upstream response
        _mark_network("down")
        return MarketList(markets=_fallback_markets(only_krw), source="fallback")
    else:
        _mark_network("up")

    markets: List[MarketInfo] = []
    for item in payload:
        market_code = str(item.get("market", "")).upper()
        if not market_code:
            continue
        if only_krw and not market_code.startswith("KRW-"):
            continue

        base_currency, _, quote_currency = market_code.partition("-")
        market_state = str(item.get("market_state", "")).upper()
        warning = str(item.get("market_warning", "NONE")).upper() or "NONE"

        markets.append(
            MarketInfo(
                market=market_code,
                korean_name=str(item.get("korean_name", "")).strip() or market_code,
                english_name=str(item.get("english_name", "")).strip() or market_code,
                base_currency=base_currency or "KRW",
                quote_currency=quote_currency or market_code,
                market_warning=warning,
                trading_suspended=market_state not in {"ACTIVE", "RUNNING"},
            )
        )

    if not markets:
        return MarketList(markets=_fallback_markets(only_krw), source="fallback")

    return MarketList(markets=markets, source="upbit")


def build_market_insights(
    candles: List[Candle],
    *,
    market: str,
    interval: Interval,
) -> Dict[str, object]:
    if not candles:
        raise MarketDataError("분석할 캔들이 부족합니다.")

    report = generate_market_insights(candles)
    latest = candles[-1]
    return {
        "market": market,
        "interval": interval,
        "latest_close": latest.close,
        "latest_timestamp": latest.timestamp,
        **report,
    }


def fetch_authoritative_news(limit: int = 8) -> List[Dict[str, str]]:
    """Return curated institutional news headlines.

    The function prefers live data from BIS/IMF/ETF issuers' RSS feeds but falls
    back to a static institutional digest when network access is not available.
    """

    feeds = [
        "https://www.bis.org/rss/publ/index.xml",
        "https://www.imf.org/external/rss/feeds.aspx?Category=PressReleases",
        "https://www.blackrock.com/us/individual/rss",  # ETF insights
    ]
    headlines: List[Dict[str, str]] = []

    for feed in feeds:
        request = Request(feed, headers={"Accept": "application/rss+xml, application/xml"})
        try:
            with urlopen(request, timeout=5) as response:  # pragma: no cover - network success
                import xml.etree.ElementTree as ET

                tree = ET.fromstring(response.read())
                for item in tree.iterfind("channel/item"):
                    title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or "").strip()
                    pub_date = (item.findtext("pubDate") or "").strip()
                    if title and link:
                        headlines.append(
                            {
                                "title": title,
                                "url": link,
                                "published_at": pub_date,
                                "source": feed,
                            }
                        )
                    if len(headlines) >= limit:
                        break
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            continue
        if len(headlines) >= limit:
            break

    if headlines:
        return headlines[:limit]

    fallback = [
        {
            "title": "IMF, 디지털 자산 규제 프레임워크 제안",
            "url": "https://www.imf.org/",  # authoritative placeholder
            "published_at": "Fallback Digest",
            "source": "IMF",
        },
        {
            "title": "BIS, 토큰화된 증권 시장 리포트 발표",
            "url": "https://www.bis.org/",
            "published_at": "Fallback Digest",
            "source": "BIS",
        },
        {
            "title": "BlackRock, ETF 시장 유동성 전망 업데이트",
            "url": "https://www.blackrock.com/",
            "published_at": "Fallback Digest",
            "source": "BlackRock",
        },
        {
            "title": "Fidelity, 디지털 자산 리서치 하이라이트",
            "url": "https://www.fidelity.com/",
            "published_at": "Fallback Digest",
            "source": "Fidelity",
        },
    ]
    return fallback[:limit]

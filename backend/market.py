"""Market data utilities for Upbit candles and institutional news."""
from __future__ import annotations

import json
import os
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:  # pragma: no cover - optional dependency availability
    import httpx
except Exception:  # pragma: no cover - httpx may be absent in constrained envs
    httpx = None  # type: ignore[assignment]

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
    status: str = "unknown"
    message: str = ""
    detail: Optional[str] = None


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
    status: str = "unknown"
    message: str = ""
    detail: Optional[str] = None
    checked_at: Optional[datetime] = None
    backoff_seconds_remaining: float = 0.0


def _synthetic_seed_for_market(market: str, interval: Interval, count: int) -> int:
    token = f"{market.upper()}::{interval}::{count}"
    return zlib.crc32(token.encode("utf-8")) & 0xFFFFFFFF


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

def _normalise_upbit_base_url(raw: str) -> str:
    cleaned = (raw or "").strip()
    if not cleaned:
        return "https://api.upbit.com"
    return cleaned.rstrip("/") or "https://api.upbit.com"


_UPBIT_API_BASE = _normalise_upbit_base_url(os.getenv("UPBIT_BASE_URL", "https://api.upbit.com"))
_UPBIT_USER_AGENT = "SadoTradeBot/1.0 (+https://github.com/iljin-corp/sado-trade-bot)"
_UPBIT_HEADERS = {"Accept": "application/json", "User-Agent": _UPBIT_USER_AGENT}

_UPBIT_ENABLE_NETWORK = os.getenv("UPBIT_ENABLE_NETWORK", "1").lower() not in {
    "0",
    "false",
    "no",
}
_UPBIT_NETWORK_BACKOFF_SECONDS = 90.0
_upbit_network_state = {
    "status": "unknown",  # "up" | "down" | "unknown"
    "checked_at": 0.0,
    "checked_at_utc": None,
    "backoff_until": 0.0,
    "message": "업비트 연결 상태를 확인하는 중입니다.",
    "detail": "",
}

_UPBIT_HTTP_TIMEOUT = 10.0

_NEWS_CACHE_TTL_SECONDS = 300.0
_NEWS_BACKOFF_SECONDS = 600.0
_news_state = {
    "status": "unknown",  # "up" | "down" | "unknown"
    "checked_at": 0.0,
    "cached_items": [],
    "cached_at": 0.0,
}


_DEFAULT_FALLBACK_ROWS: List[Tuple[str, str, str]] = [
    ("KRW-BTC", "비트코인", "Bitcoin"),
    ("KRW-ETH", "이더리움", "Ethereum"),
    ("KRW-SOL", "솔라나", "Solana"),
    ("KRW-XRP", "리플", "Ripple"),
    ("KRW-ADA", "에이다", "Cardano"),
    ("KRW-MATIC", "폴리곤", "Polygon"),
    ("KRW-DOGE", "도지코인", "Dogecoin"),
    ("KRW-DOT", "폴카닷", "Polkadot"),
    ("KRW-LINK", "체인링크", "Chainlink"),
    ("KRW-BCH", "비트코인캐시", "Bitcoin Cash"),
    ("KRW-LTC", "라이트코인", "Litecoin"),
    ("KRW-AVAX", "아발란체", "Avalanche"),
    ("KRW-ATOM", "코스모스", "Cosmos"),
    ("KRW-SAND", "샌드박스", "The Sandbox"),
    ("KRW-NEAR", "니어", "NEAR Protocol"),
    ("KRW-APT", "앱토스", "Aptos"),
    ("KRW-ARB", "아비트럼", "Arbitrum"),
    ("KRW-TRX", "트론", "TRON"),
    ("KRW-XLM", "스텔라루멘", "Stellar"),
    ("KRW-ETC", "이더리움클래식", "Ethereum Classic"),
    ("KRW-FTM", "팬텀", "Fantom"),
    ("KRW-AXS", "엑시인피니티", "Axie Infinity"),
    ("KRW-ALGO", "알고랜드", "Algorand"),
    ("KRW-STX", "스택스", "Stacks"),
    ("KRW-SHIB", "시바이누", "Shiba Inu"),
    ("KRW-PEPE", "페페", "PEPE"),
    ("KRW-MANA", "디센트럴랜드", "Decentraland"),
    ("KRW-SXP", "솔라", "Solar"),
    ("KRW-CHZ", "칠리즈", "Chiliz"),
    ("KRW-AAVE", "에이브", "Aave"),
]


def _load_fallback_markets() -> List[MarketInfo]:
    """Load fallback markets from the bundled JSON asset or defaults."""

    candidates = [
        Path(__file__).resolve().parent.parent
        / "frontend"
        / "assets"
        / "upbit_markets_krw.json",
    ]

    for path in candidates:
        try:
            raw = path.read_text("utf-8")
        except OSError:
            continue

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if not isinstance(payload, list):
            continue

        markets: List[MarketInfo] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue

            market_code = str(item.get("market", "")).upper()
            if not market_code or market_code in seen:
                continue

            base_currency, _, quote_currency = market_code.partition("-")
            base_currency = (str(item.get("base_currency")) or base_currency or "KRW").upper()
            quote_currency = (str(item.get("quote_currency")) or quote_currency or market_code).upper()

            markets.append(
                MarketInfo(
                    market=market_code,
                    korean_name=str(item.get("korean_name") or quote_currency),
                    english_name=str(item.get("english_name") or quote_currency),
                    base_currency=base_currency,
                    quote_currency=quote_currency,
                    market_warning=str(item.get("market_warning") or "NONE").upper(),
                    trading_suspended=bool(item.get("trading_suspended", False)),
                )
            )
            seen.add(market_code)

        if markets:
            return markets

    fallback: List[MarketInfo] = []
    seen_defaults: set[str] = set()
    for code, korean, english in _DEFAULT_FALLBACK_ROWS:
        market_code = str(code).upper()
        if market_code in seen_defaults:
            continue
        seen_defaults.add(market_code)
        base_currency, _, quote_currency = market_code.partition("-")
        base_currency = base_currency or "KRW"
        quote_currency = quote_currency or market_code
        fallback.append(
            MarketInfo(
                market=market_code,
                korean_name=korean or quote_currency,
                english_name=english or quote_currency,
                base_currency=base_currency,
                quote_currency=quote_currency,
                market_warning="NONE",
                trading_suspended=False,
            )
        )

    return fallback


_FALLBACK_MARKETS: List[MarketInfo] = _load_fallback_markets()

_FALLBACK_NEWS: List[Dict[str, str]] = [
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


def _parse_upbit_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        # Upbit may return without timezone; assume UTC.
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def _describe_error(exc: BaseException) -> str:
    if exc.__cause__ is not None:
        return str(exc.__cause__)
    if exc.__context__ is not None and exc.__context__ is not exc:
        return str(exc.__context__)
    return str(exc)


def _upbit_network_down() -> bool:
    if not _UPBIT_ENABLE_NETWORK:
        return True
    state = _upbit_network_state
    if state["status"] != "down":
        return False
    elapsed = time.monotonic() - state["checked_at"]
    return elapsed < _UPBIT_NETWORK_BACKOFF_SECONDS


def _mark_network(
    status: str,
    *,
    message: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    state = _upbit_network_state
    state["status"] = status
    state["checked_at"] = time.monotonic()
    state["checked_at_utc"] = datetime.now(timezone.utc)
    if message is not None:
        state["message"] = message
    elif status == "up":
        state["message"] = "업비트 실시간 데이터에 연결되었습니다."
    if detail is not None:
        state["detail"] = detail
    elif status == "up":
        state["detail"] = ""
    if status == "down":
        state["backoff_until"] = state["checked_at"] + _UPBIT_NETWORK_BACKOFF_SECONDS
    else:
        state["backoff_until"] = 0.0


def get_upbit_network_state() -> Dict[str, Any]:
    state = _upbit_network_state
    status = state.get("status", "unknown")
    message = state.get("message") or "업비트 연결 상태를 확인하는 중입니다."
    detail = state.get("detail") or ""
    checked_at = state.get("checked_at_utc")
    remaining = 0.0
    if status == "down":
        backoff_until = state.get("backoff_until", 0.0)
        remaining = max(0.0, backoff_until - time.monotonic())
        if remaining > 0:
            seconds = int(round(remaining))
            if "재시도" not in message:
                message = f"{message} · 재시도까지 약 {seconds}초 남았습니다."
    return {
        "status": status,
        "message": message,
        "detail": detail,
        "checked_at": checked_at,
        "backoff_seconds_remaining": remaining,
    }


def _request_upbit(path: str, params: Optional[Dict[str, object]] = None) -> Any:
    query = urlencode(params or {})
    url = f"{_UPBIT_API_BASE}{path}{f'?{query}' if query else ''}"

    httpx_error: Optional[BaseException] = None
    if httpx is not None:
        try:
            with httpx.Client(
                headers=_UPBIT_HEADERS,
                timeout=_UPBIT_HTTP_TIMEOUT,
                follow_redirects=True,
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:  # pragma: no cover - requires network failure
            httpx_error = exc

    request = Request(url, headers=_UPBIT_HEADERS)
    try:
        with urlopen(request, timeout=_UPBIT_HTTP_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        error = httpx_error or exc
        raise MarketDataError(f"업비트 API 요청이 실패했습니다: {error}") from error

    if not raw:
        raise MarketDataError("업비트에서 빈 응답을 받았습니다.")

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MarketDataError("업비트 응답을 해석하지 못했습니다.") from exc


if not _UPBIT_ENABLE_NETWORK:
    _mark_network(
        "down",
        message="환경 설정에서 업비트 네트워크가 비활성화되어 시뮬레이션 데이터만 사용합니다.",
        detail="UPBIT_ENABLE_NETWORK=0",
    )


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
        seed = _synthetic_seed_for_market(market, interval, count)
        synthetic = generate_synthetic_prices(days=count, seed=seed)
        state = get_upbit_network_state()
        return MarketData(
            candles=synthetic,
            source="synthetic",
            status=state["status"],
            message=state["message"],
            detail=state.get("detail"),
        )

    interval_path, unit = _INTERVAL_PATHS[interval]
    if unit:
        path = f"/v1/candles/{interval_path}/{unit}"
    else:
        path = f"/v1/candles/{interval_path}"

    try:
        payload = _request_upbit(path, {"market": market, "count": count})
    except MarketDataError as exc:
        detail = _describe_error(exc)
        _mark_network(
            "down",
            message="업비트 캔들 데이터를 가져오지 못해 시뮬레이션 시세를 사용합니다.",
            detail=detail,
        )
        seed = _synthetic_seed_for_market(market, interval, count)
        synthetic = generate_synthetic_prices(days=count, seed=seed)
        state = get_upbit_network_state()
        return MarketData(
            candles=synthetic,
            source="synthetic",
            status=state["status"],
            message=state["message"],
            detail=state.get("detail"),
        )
    else:
        _mark_network("up", message="업비트 실시간 캔들 데이터를 사용 중입니다.")

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
    state = get_upbit_network_state()
    return MarketData(
        candles=candles,
        source="upbit",
        status=state["status"],
        message=state["message"],
        detail=state.get("detail"),
    )


def _fallback_markets(only_krw: bool) -> List[MarketInfo]:
    if only_krw:
        return list(_FALLBACK_MARKETS)
    return list(_FALLBACK_MARKETS)


def fetch_upbit_markets(*, only_krw: bool = True) -> MarketList:
    """Return tradable markets from Upbit or a deterministic fallback list."""

    if _upbit_network_down():
        state = get_upbit_network_state()
        return MarketList(
            markets=_fallback_markets(only_krw),
            source="fallback",
            status=state["status"],
            message=state["message"],
            detail=state.get("detail"),
            checked_at=state.get("checked_at"),
            backoff_seconds_remaining=state["backoff_seconds_remaining"],
        )

    try:
        payload = _request_upbit("/v1/market/all", {"isDetails": "true"})
    except MarketDataError as exc:
        detail = _describe_error(exc)
        _mark_network(
            "down",
            message="업비트 마켓 목록을 가져오지 못해 내장 디렉터리를 사용합니다.",
            detail=detail,
        )
        state = get_upbit_network_state()
        return MarketList(
            markets=_fallback_markets(only_krw),
            source="fallback",
            status=state["status"],
            message=state["message"],
            detail=state.get("detail"),
            checked_at=state.get("checked_at"),
            backoff_seconds_remaining=state["backoff_seconds_remaining"],
        )
    else:
        _mark_network("up", message="업비트 실시간 마켓 디렉터리를 사용 중입니다.")

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
        state = get_upbit_network_state()
        return MarketList(
            markets=_fallback_markets(only_krw),
            source="fallback",
            status=state["status"],
            message="업비트 응답이 비어 있어 내장 목록을 사용합니다.",
            detail=state.get("detail"),
            checked_at=state.get("checked_at"),
            backoff_seconds_remaining=state["backoff_seconds_remaining"],
        )

    state = get_upbit_network_state()
    return MarketList(
        markets=markets,
        source="upbit",
        status=state["status"],
        message=state["message"],
        detail=state.get("detail"),
        checked_at=state.get("checked_at"),
        backoff_seconds_remaining=state["backoff_seconds_remaining"],
    )


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


def _news_cache_valid(limit: int) -> Optional[List[Dict[str, str]]]:
    cached = _news_state["cached_items"]
    if not cached:
        return None
    if time.monotonic() - _news_state["cached_at"] > _NEWS_CACHE_TTL_SECONDS:
        return None
    return [dict(item) for item in cached[:limit]]


def _news_backoff_active() -> bool:
    if _news_state["status"] != "down":
        return False
    elapsed = time.monotonic() - _news_state["checked_at"]
    return elapsed < _NEWS_BACKOFF_SECONDS


def _record_news_state(*, status: str, items: Optional[List[Dict[str, str]]] = None) -> None:
    timestamp = time.monotonic()
    _news_state["status"] = status
    _news_state["checked_at"] = timestamp
    if items is not None:
        _news_state["cached_items"] = [dict(item) for item in items]
        _news_state["cached_at"] = timestamp
    elif not _news_state["cached_items"]:
        _news_state["cached_items"] = [dict(item) for item in _FALLBACK_NEWS]
        _news_state["cached_at"] = timestamp


def _news_from_cache_or_fallback(limit: int) -> List[Dict[str, str]]:
    cached = _news_state["cached_items"]
    source = cached if cached else _FALLBACK_NEWS
    return [dict(item) for item in source[:limit]]


def fetch_authoritative_news(limit: int = 8) -> List[Dict[str, str]]:
    """Return curated institutional news headlines with caching and backoff."""

    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):  # pragma: no cover - defensive guard
        limit = 1

    cached = _news_cache_valid(limit)
    if cached is not None:
        return cached

    if _news_backoff_active():
        return _news_from_cache_or_fallback(limit)

    feeds = [
        "https://www.bis.org/rss/publ/index.xml",
        "https://www.imf.org/external/rss/feeds.aspx?Category=PressReleases",
        "https://www.blackrock.com/us/individual/rss",  # ETF insights
    ]
    headlines: List[Dict[str, str]] = []

    for feed in feeds:
        request = Request(feed, headers={"Accept": "application/rss+xml, application/xml"})
        try:
            with urlopen(request, timeout=2.5) as response:  # pragma: no cover - network success
                import xml.etree.ElementTree as ET

                tree = ET.fromstring(response.read())
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            continue

        for item in tree.iterfind("channel/item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title or not link:
                continue
            pub_date = (item.findtext("pubDate") or "").strip()
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
        if len(headlines) >= limit:
            break

    if headlines:
        _record_news_state(status="up", items=headlines)
        return [dict(item) for item in headlines[:limit]]

    _record_news_state(status="down", items=None)
    return _news_from_cache_or_fallback(limit)

"""Market data utilities for Upbit candles and institutional news."""
from __future__ import annotations

import atexit
import json
import os
import time
import zlib
from collections import deque
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Literal, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:  # pragma: no cover - optional dependency availability
    import httpx
except Exception:  # pragma: no cover - httpx may be absent in constrained envs
    httpx = None  # type: ignore[assignment]

from .trading import Candle, generate_synthetic_prices, generate_market_insights
from .log_utils import log_event, log_exception


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
    source: Literal["upbit", "upbit_alt", "synthetic", "upbit_stale"]
    status: str = "unknown"
    message: str = ""
    detail: Optional[str] = None
    stale: bool = False
    fetched_at: Optional[datetime] = None


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


@dataclass
class MarketTicker:
    market: str
    trade_price: float
    acc_trade_price_24h: float
    acc_trade_volume_24h: float
    change_rate: float
    fetched_at: datetime


@dataclass
class TopMarketSelection:
    markets: List[MarketInfo]
    source: Literal["upbit", "fallback-volume"]
    errors: List[str] = field(default_factory=list)


@dataclass
class _CachedMarketSnapshot:
    candles: List[Candle]
    fetched_at: datetime
    message: str
    detail: Optional[str]


@dataclass
class RecoveryLogEntry:
    """Represents a single Upbit self-heal attempt step."""

    timestamp: datetime
    reason: str
    action: str
    status: Literal["success", "failed", "skipped", "info"]
    detail: str = ""

    def to_payload(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "reason": self.reason,
            "action": self.action,
            "status": self.status,
            "detail": self.detail,
        }


def _synthetic_seed_for_market(market: str, interval: Interval, count: int) -> int:
    token = f"{market.upper()}::{interval}::{count}"
    return zlib.crc32(token.encode("utf-8")) & 0xFFFFFFFF


class MarketDataError(RuntimeError):
    """Raised when market data cannot be retrieved."""


class MarketRateLimitError(MarketDataError):
    """Raised when Upbit signals that the request rate is too high."""


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
_UPBIT_ALT_BASE = "https://crix-api.upbit.com"
_UPBIT_USER_AGENT = "SadoTradeBot/1.0 (+https://github.com/iljin-corp/sado-trade-bot)"
_UPBIT_HEADERS = {"Accept": "application/json", "User-Agent": _UPBIT_USER_AGENT}


def _parse_positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _parse_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default

_UPBIT_ENABLE_NETWORK = os.getenv("UPBIT_ENABLE_NETWORK", "1").lower() not in {
    "0",
    "false",
    "no",
}
_UPBIT_NETWORK_BACKOFF_SECONDS = 45.0
_upbit_network_state = {
    "status": "unknown",  # "up" | "down" | "unknown"
    "checked_at": 0.0,
    "checked_at_utc": None,
    "backoff_until": 0.0,
    "message": "업비트 연결 상태를 확인하는 중입니다.",
    "detail": "",
}

_UPBIT_HTTP_TIMEOUT = _parse_positive_float_env("UPBIT_HTTP_TIMEOUT", 12.0)
_UPBIT_RATE_LIMIT_PER_SECOND = _parse_positive_int_env("UPBIT_RATE_LIMIT_PER_SECOND", 24)
_UPBIT_RATE_LIMIT_PER_MINUTE = _parse_positive_int_env("UPBIT_RATE_LIMIT_PER_MINUTE", 720)

_rate_limit_lock = threading.Lock()
_recent_upbit_requests: Deque[float] = deque()

_httpx_client: Optional["httpx.Client"] = None


def _chunked(sequence: Sequence[str], size: int) -> Iterable[List[str]]:
    for index in range(0, len(sequence), size):
        yield list(sequence[index : index + size])


def _get_httpx_client() -> Optional["httpx.Client"]:
    global _httpx_client
    if httpx is None:
        return None
    if _httpx_client is None:
        timeout = _UPBIT_HTTP_TIMEOUT
        limits = httpx.Limits(max_keepalive_connections=10, keepalive_expiry=30.0)
        _httpx_client = httpx.Client(
            headers=_UPBIT_HEADERS,
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
        )
    return _httpx_client


def _throttle_upbit_request() -> None:
    if not _UPBIT_ENABLE_NETWORK:
        return
    if _UPBIT_RATE_LIMIT_PER_SECOND <= 0 and _UPBIT_RATE_LIMIT_PER_MINUTE <= 0:
        return

    while True:
        now = time.monotonic()
        with _rate_limit_lock:
            cutoff = now - 60.0
            while _recent_upbit_requests and _recent_upbit_requests[0] <= cutoff:
                _recent_upbit_requests.popleft()

            wait = 0.0

            if _UPBIT_RATE_LIMIT_PER_SECOND > 0 and len(_recent_upbit_requests) >= _UPBIT_RATE_LIMIT_PER_SECOND:
                index = len(_recent_upbit_requests) - _UPBIT_RATE_LIMIT_PER_SECOND
                threshold = _recent_upbit_requests[index] + 1.0
                wait = max(wait, threshold - now)

            if _UPBIT_RATE_LIMIT_PER_MINUTE > 0 and len(_recent_upbit_requests) >= _UPBIT_RATE_LIMIT_PER_MINUTE:
                index = len(_recent_upbit_requests) - _UPBIT_RATE_LIMIT_PER_MINUTE
                threshold = _recent_upbit_requests[index] + 60.0
                wait = max(wait, threshold - now)

            if wait <= 0:
                _recent_upbit_requests.append(now)
                return

        sleep_for = min(wait, 1.0) if wait > 0 else 0.05
        time.sleep(sleep_for)


def _close_httpx_client() -> None:
    global _httpx_client
    client = _httpx_client
    if client is not None:
        try:
            client.close()
        finally:
            _httpx_client = None


if httpx is not None:
    atexit.register(_close_httpx_client)

_NEWS_CACHE_TTL_SECONDS = 300.0
_NEWS_BACKOFF_SECONDS = 600.0
_news_state = {
    "status": "unknown",  # "up" | "down" | "unknown"
    "checked_at": 0.0,
    "cached_items": [],
    "cached_at": 0.0,
}

_UPBIT_CACHE_MAX_AGE_SECONDS = 300.0
_recent_market_cache: Dict[Tuple[str, Interval], Tuple[_CachedMarketSnapshot, float]] = {}
_UPBIT_SELF_HEAL_INTERVAL_SECONDS = 15.0
_last_self_heal_attempt: float = 0.0
_upbit_recovery_log: Deque[RecoveryLogEntry] = deque(maxlen=50)


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


def get_fallback_market_infos(*, only_krw: bool = True) -> List[MarketInfo]:
    """Return a copy of the bundled Upbit market catalogue."""

    return list(_fallback_markets(only_krw))


def _record_recovery_step(
    *, reason: str, action: str, status: Literal["success", "failed", "skipped", "info"], detail: str = ""
) -> RecoveryLogEntry:
    entry = RecoveryLogEntry(
        timestamp=datetime.now(timezone.utc),
        reason=reason,
        action=action,
        status=status,
        detail=detail,
    )
    _upbit_recovery_log.appendleft(entry)
    return entry


def get_upbit_recovery_log(limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent Upbit self-heal attempts for diagnostics panels."""

    if limit <= 0:
        return []
    return [entry.to_payload() for entry in list(_upbit_recovery_log)[:limit]]


def attempt_upbit_self_heal(reason: str) -> Dict[str, Any]:
    """Attempt to recover the Upbit connection after repeated failures."""

    global _UPBIT_API_BASE, _UPBIT_ENABLE_NETWORK, _last_self_heal_attempt

    performed_at = datetime.now(timezone.utc)
    steps: List[RecoveryLogEntry] = []

    def _step(
        action: str,
        status: Literal["success", "failed", "skipped", "info"],
        detail: str = "",
    ) -> None:
        steps.append(
            _record_recovery_step(reason=reason, action=action, status=status, detail=detail)
        )

    if not _UPBIT_ENABLE_NETWORK:
        _step(
            "network-disabled",
            "skipped",
            "UPBIT_ENABLE_NETWORK 설정이 비활성화되어 있어 실시간 연결을 시도할 수 없습니다.",
        )
        return {
            "performed_at": performed_at,
            "reason": reason,
            "status": "skipped",
            "steps": [entry.to_payload() for entry in steps],
        }

    now = time.monotonic()
    if now - _last_self_heal_attempt < _UPBIT_SELF_HEAL_INTERVAL_SECONDS:
        remaining = max(0.0, _UPBIT_SELF_HEAL_INTERVAL_SECONDS - (now - _last_self_heal_attempt))
        _step(
            "cooldown",
            "info",
            f"최근 자가 복구를 시도했습니다. 약 {remaining:.1f}초 후 다시 시도하세요.",
        )
        return {
            "performed_at": performed_at,
            "reason": reason,
            "status": "cooldown",
            "steps": [entry.to_payload() for entry in steps],
        }

    _last_self_heal_attempt = now

    env_snapshot: Optional[Dict[str, str]] = None
    try:  # pragma: no cover - optional during minimal test envs
        from .notifications import ensure_env_from_file
    except Exception:  # pragma: no cover - fallback when notifications is unavailable
        ensure_env_from_file = None  # type: ignore
    else:
        try:
            env_snapshot = ensure_env_from_file() or {}  # type: ignore[misc]
        except Exception as exc:  # pragma: no cover - defensive guard
            _step("reload-env", "failed", f".env 로드 실패: {exc}")
        else:
            _step(
                "reload-env",
                "success",
                f"환경 변수 {len(env_snapshot)}개 확인",  # type: ignore[arg-type]
            )

    if env_snapshot:
        raw_base = env_snapshot.get("UPBIT_BASE_URL")
        if raw_base is not None:
            new_base = _normalise_upbit_base_url(raw_base)
            if new_base != _UPBIT_API_BASE:
                _UPBIT_API_BASE = new_base
                _step("refresh-base-url", "success", f"기준 URL을 {new_base}로 재설정했습니다.")

        raw_enable = env_snapshot.get("UPBIT_ENABLE_NETWORK")
        if raw_enable is not None:
            new_flag = raw_enable.lower() not in {"0", "false", "no"}
            if new_flag != _UPBIT_ENABLE_NETWORK:
                _UPBIT_ENABLE_NETWORK = new_flag
                flag_text = "사용" if new_flag else "비활성화"
                _step("refresh-network-flag", "success", f"네트워크 사용 설정을 {flag_text} 상태로 변경했습니다.")

    _mark_network(
        "unknown",
        message="업비트 연결 상태를 재점검하는 중입니다.",
        detail=f"self-heal:{reason}",
    )
    _step("reset-network-state", "info", "네트워크 상태를 초기화했습니다.")

    try:
        payload = _request_upbit(
            "/v1/market/all",
            {"isDetails": "false"},
            allow_self_heal=False,
        )
    except MarketDataError as exc:
        detail = str(exc)
        _step("probe-market-directory", "failed", detail)
        _mark_network(
            "down",
            message="업비트 네트워크 연결이 아직 복구되지 않았습니다.",
            detail=detail,
        )
        status = "failed"
    else:
        if isinstance(payload, list) and payload:
            _step(
                "probe-market-directory",
                "success",
                f"{len(payload)}개 마켓 응답 확인",
            )
            _mark_network(
                "up",
                message="업비트 실시간 데이터 연결을 복구했습니다.",
                detail=f"market/all {len(payload)}건 응답",
            )
            status = "success"
        else:
            _step("probe-market-directory", "failed", "업비트 응답이 비어 있습니다.")
            _mark_network(
                "down",
                message="업비트 응답이 비어 있어 연결을 복구하지 못했습니다.",
                detail="market/all 빈 응답",
            )
            status = "failed"

    return {
        "performed_at": performed_at,
        "reason": reason,
        "status": status,
        "steps": [entry.to_payload() for entry in steps],
    }

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
    previous_status = state.get("status")
    previous_message = state.get("message")
    previous_detail = state.get("detail")
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

    if (
        previous_status != state.get("status")
        or previous_message != state.get("message")
        or previous_detail != state.get("detail")
    ):
        log_event(
            "upbit.network",
            status=state.get("status"),
            message=state.get("message"),
            detail=state.get("detail"),
            backoff_seconds=state.get("backoff_until", 0.0),
        )


def _clone_candles(candles: Iterable[Candle]) -> List[Candle]:
    return [
        Candle(
            timestamp=item.timestamp,
            open=item.open,
            high=item.high,
            low=item.low,
            close=item.close,
            volume=item.volume,
        )
        for item in candles
    ]


def _store_cached_market_data(
    *, market: str, interval: Interval, data: MarketData
) -> None:
    if data.source not in {"upbit", "upbit_alt"} or not data.candles:
        return
    key = (market.upper(), interval)
    snapshot = _CachedMarketSnapshot(
        candles=_clone_candles(data.candles),
        fetched_at=data.fetched_at or datetime.now(timezone.utc),
        message=data.message,
        detail=data.detail,
    )
    _recent_market_cache[key] = (snapshot, time.monotonic())


def _load_cached_market_data(
    *, market: str, interval: Interval
) -> Optional[MarketData]:
    key = (market.upper(), interval)
    cached = _recent_market_cache.get(key)
    if not cached:
        return None
    snapshot, stored_at = cached
    if time.monotonic() - stored_at > _UPBIT_CACHE_MAX_AGE_SECONDS:
        return None
    message = snapshot.message or "업비트 응답 지연으로 최근 실시간 시세를 재사용합니다."
    detail = snapshot.detail or f"마지막 실시간 갱신: {snapshot.fetched_at.isoformat()}"
    return MarketData(
        candles=_clone_candles(snapshot.candles),
        source="upbit_stale",
        status="warning",
        message=message,
        detail=detail,
        stale=True,
        fetched_at=snapshot.fetched_at,
    )


def reset_market_state_for_tests() -> None:
    """Reset Upbit cache and network tracking (test helper)."""

    _recent_market_cache.clear()
    _upbit_recovery_log.clear()
    with _rate_limit_lock:
        _recent_upbit_requests.clear()
    global _last_self_heal_attempt
    _last_self_heal_attempt = 0.0
    _upbit_network_state.update(
        {
            "status": "unknown",
            "checked_at": 0.0,
            "checked_at_utc": None,
            "backoff_until": 0.0,
            "message": "업비트 연결 상태를 확인하는 중입니다.",
            "detail": "",
        }
    )
    _close_httpx_client()


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


def is_upbit_network_operational() -> bool:
    """Return whether live Upbit data is currently considered usable."""

    if not _UPBIT_ENABLE_NETWORK:
        return False

    state = get_upbit_network_state()
    status = state.get("status", "unknown")

    if status in {"up", "warning"}:
        return True

    if status == "down":
        remaining = float(state.get("backoff_seconds_remaining", 0.0) or 0.0)
        return remaining <= 0.0 and bool(_recent_market_cache)

    # Unknown 상태에서는 아직 네트워크를 판별하지 못했으므로 낙관적으로 허용한다.
    return True


def _coerce_rate_limit_error(error: BaseException) -> BaseException:
    """Convert HTTP status errors into a dedicated rate-limit exception."""

    status_code: Optional[int] = None
    retry_after: Optional[str] = None

    if httpx is not None and isinstance(error, getattr(httpx, "HTTPError", tuple())):
        response = getattr(error, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            headers = getattr(response, "headers", None)
            if headers is not None:
                retry_after = headers.get("Retry-After")

    if isinstance(error, HTTPError):
        status_code = getattr(error, "code", status_code)
        headers = getattr(error, "headers", None)
        if headers is not None and retry_after is None:
            retry_after = headers.get("Retry-After")

    if status_code in {429, 503}:
        retry_hint: Optional[float] = None
        if retry_after:
            try:
                retry_hint = float(retry_after)
            except (TypeError, ValueError):
                retry_hint = None
        message = f"업비트 API 요청이 일시적으로 제한되었습니다 (status={status_code})."
        if retry_hint is not None and retry_hint > 0:
            message += f" 약 {retry_hint:.0f}초 후 다시 시도하세요."
        return MarketRateLimitError(message)

    return error


def _request_upbit(
    path: str, params: Optional[Dict[str, object]] = None, *, allow_self_heal: bool = True
) -> Any:
    query = urlencode(params or {})
    url = f"{_UPBIT_API_BASE}{path}{f'?{query}' if query else ''}"

    attempts = 3
    last_error: Optional[BaseException] = None

    for attempt in range(1, attempts + 1):
        httpx_error: Optional[BaseException] = None
        client = _get_httpx_client()
        if client is not None:
            try:
                _throttle_upbit_request()
                response = client.get(url)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:  # pragma: no cover - requires network failure
                httpx_error = _coerce_rate_limit_error(exc)

        request = Request(url, headers=_UPBIT_HEADERS)
        try:
            _throttle_upbit_request()
            with urlopen(request, timeout=_UPBIT_HTTP_TIMEOUT) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = httpx_error or _coerce_rate_limit_error(exc)
        else:
            if not raw:
                last_error = MarketDataError("업비트에서 빈 응답을 받았습니다.")
            else:
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    last_error = exc

        if attempt < attempts:
            # 짧은 네트워크 오류로 인한 오탐지를 줄이기 위해 약간의 대기 후 재시도한다.
            time.sleep(min(1.0, 0.3 * attempt))

    if last_error is None:
        last_error = RuntimeError("업비트 API 요청이 반복해서 실패했습니다.")

    log_exception(
        "upbit.request.failed",
        last_error,
        url=url,
        attempts=attempts,
    )

    if allow_self_heal:
        attempt_upbit_self_heal("request-failed")

    if isinstance(last_error, MarketRateLimitError):
        raise last_error

    raise MarketDataError(
        f"업비트 API 요청이 반복해서 실패했습니다 ({attempts}회 시도)."
    ) from last_error


def _request_upbit_alt(path: str, params: Optional[Dict[str, object]] = None) -> Any:
    """Lightweight helper for the Upbit CRIX public API."""

    url = f"{_UPBIT_ALT_BASE}{path}"
    if params:
        query = urlencode(params)
        if query:
            url = f"{url}?{query}"

    attempts = 2
    last_error: Optional[BaseException] = None

    for attempt in range(1, attempts + 1):
        client = _get_httpx_client()
        if client is not None:
            try:
                _throttle_upbit_request()
                response = client.get(url)
                response.raise_for_status()
                return response.json()
            except Exception as exc:  # pragma: no cover - requires network failure
                last_error = exc

        request = Request(url, headers=_UPBIT_HEADERS)
        try:
            _throttle_upbit_request()
            with urlopen(request, timeout=_UPBIT_HTTP_TIMEOUT) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
        else:
            if not raw:
                last_error = MarketDataError("업비트 대체 API에서 빈 응답을 받았습니다.")
            else:
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    last_error = exc

        if attempt < attempts:
            time.sleep(0.25)

    if last_error is None:
        last_error = RuntimeError("업비트 대체 API 요청이 반복해서 실패했습니다.")

    raise MarketDataError("업비트 대체 API 요청이 실패했습니다.") from last_error


def _convert_alt_payload_to_candles(payload: Iterable[Dict[str, Any]]) -> List[Candle]:
    candles: List[Candle] = []
    for item in payload:
        try:
            timestamp_str = (
                item.get("candleDateTime")
                or item.get("candleDateTimeUtc")
                or item.get("candle_date_time_utc")
                or item.get("time_open")
            )
            if not timestamp_str:
                raise KeyError("candleDateTime")
            timestamp = _parse_upbit_timestamp(str(timestamp_str))
            opening = float(
                item.get("openingPrice")
                or item.get("opening_price")
                or item.get("open")
            )
            high = float(item.get("highPrice") or item.get("high_price") or item.get("high"))
            low = float(item.get("lowPrice") or item.get("low_price") or item.get("low"))
            close = float(
                item.get("tradePrice")
                or item.get("trade_price")
                or item.get("close")
            )
            volume = float(
                item.get("candleAccTradeVolume")
                or item.get("candle_acc_trade_volume")
                or item.get("volume")
                or 0.0
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketDataError("업비트 대체 캔들 데이터를 읽을 수 없습니다.") from exc

        candles.append(
            Candle(
                timestamp=timestamp,
                open=opening,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )

    candles.reverse()
    return candles


def _fetch_upbit_candles_via_alt(
    market: str, interval: Interval, count: int, *, fallback_detail: str
) -> Optional[MarketData]:
    if not _UPBIT_ENABLE_NETWORK:
        return None

    interval_path, unit = _INTERVAL_PATHS[interval]
    if unit:
        path = f"/v1/crix/candles/{interval_path}/{unit}"
    else:
        path = f"/v1/crix/candles/{interval_path}"

    try:
        payload = _request_upbit_alt(
            path,
            {
                "code": f"CRIX.UPBIT.{market.upper()}",
                "count": count,
            },
        )
    except MarketDataError:
        return None

    if not isinstance(payload, Iterable):
        return None

    try:
        candles = _convert_alt_payload_to_candles(payload)
    except MarketDataError:
        return None

    if not candles:
        return None

    _mark_network(
        "warning",
        message="업비트 메인 API 응답이 지연되어 대체 엔드포인트 시세를 사용합니다.",
        detail=fallback_detail,
    )
    data = MarketData(
        candles=candles,
        source="upbit_alt",
        status="warning",
        message="업비트 대체 엔드포인트 시세를 사용 중입니다.",
        detail=fallback_detail,
        stale=False,
        fetched_at=datetime.now(timezone.utc),
    )
    _store_cached_market_data(market=market, interval=interval, data=data)
    log_event(
        "upbit.candles.alt", level="WARNING", market=market, interval=interval, detail=fallback_detail
    )
    return data


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
        cached = _load_cached_market_data(market=market, interval=interval)
        if cached:
            log_event(
                "upbit.candles.cache",
                level="WARNING",
                market=market,
                interval=interval,
                source=cached.source,
                message=cached.message,
            )
            return cached
        recovery = attempt_upbit_self_heal("candles-blocked")
        if recovery.get("status") == "success" and not _upbit_network_down():
            # 네트워크 복구에 성공했으므로 즉시 실시간 데이터를 다시 시도한다.
            return fetch_upbit_candles(market=market, interval=interval, count=count)
        seed = _synthetic_seed_for_market(market, interval, count)
        synthetic = generate_synthetic_prices(days=count, seed=seed)
        state = get_upbit_network_state()
        result = MarketData(
            candles=synthetic,
            source="synthetic",
            status=state["status"],
            message=state["message"],
            detail=state.get("detail"),
            stale=False,
            fetched_at=datetime.now(timezone.utc),
        )
        log_event(
            "upbit.candles.synthetic",
            level="WARNING",
            market=market,
            interval=interval,
            reason="network-down",
            detail=result.detail,
        )
        return result

    interval_path, unit = _INTERVAL_PATHS[interval]
    if unit:
        path = f"/v1/candles/{interval_path}/{unit}"
    else:
        path = f"/v1/candles/{interval_path}"

    try:
        payload = _request_upbit(path, {"market": market, "count": count})
    except MarketRateLimitError as exc:
        detail = _describe_error(exc)
        _mark_network(
            "warning",
            message="업비트 요청 제한으로 최근 시세를 재사용합니다.",
            detail=detail,
        )
        cached = _load_cached_market_data(market=market, interval=interval)
        if cached:
            cached.message = "업비트 요청 제한으로 최근 실시간 시세를 재사용합니다."
            cached.detail = detail or cached.detail
            log_event(
                "upbit.candles.cache",
                level="WARNING",
                market=market,
                interval=interval,
                source=cached.source,
                reason="rate-limit",
                detail=cached.detail,
            )
            return cached
        seed = _synthetic_seed_for_market(market, interval, count)
        synthetic = generate_synthetic_prices(days=count, seed=seed)
        state = get_upbit_network_state()
        result = MarketData(
            candles=synthetic,
            source="synthetic",
            status=state["status"],
            message=state["message"],
            detail=detail,
            stale=False,
            fetched_at=datetime.now(timezone.utc),
        )
        log_event(
            "upbit.candles.synthetic",
            level="WARNING",
            market=market,
            interval=interval,
            reason="rate-limit",
            detail=detail,
        )
        return result
    except MarketDataError as exc:
        detail = _describe_error(exc)
        alt = _fetch_upbit_candles_via_alt(
            market,
            interval,
            count,
            fallback_detail=detail,
        )
        if alt is not None:
            return alt
        cached = _load_cached_market_data(market=market, interval=interval)
        if cached:
            _mark_network(
                "warning",
                message="업비트 응답 지연으로 최근 실시간 시세를 재사용합니다.",
                detail=detail or cached.detail,
            )
            cached.message = "업비트 응답 지연으로 최근 실시간 시세를 재사용합니다."
            cached.detail = detail or cached.detail
            log_event(
                "upbit.candles.cache",
                level="WARNING",
                market=market,
                interval=interval,
                source=cached.source,
                reason="request-failed",
                detail=cached.detail,
            )
            return cached
        _mark_network(
            "down",
            message="업비트 캔들 데이터를 가져오지 못해 시뮬레이션 시세를 사용합니다.",
            detail=detail,
        )
        seed = _synthetic_seed_for_market(market, interval, count)
        synthetic = generate_synthetic_prices(days=count, seed=seed)
        state = get_upbit_network_state()
        result = MarketData(
            candles=synthetic,
            source="synthetic",
            status=state["status"],
            message=state["message"],
            detail=state.get("detail"),
            stale=False,
            fetched_at=datetime.now(timezone.utc),
        )
        log_event(
            "upbit.candles.synthetic",
            level="WARNING",
            market=market,
            interval=interval,
            reason="request-failed",
            detail=result.detail,
        )
        return result
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
    result = MarketData(
        candles=candles,
        source="upbit",
        status=state["status"],
        message=state["message"],
        detail=state.get("detail"),
        stale=False,
        fetched_at=datetime.now(timezone.utc),
    )
    _store_cached_market_data(market=market, interval=interval, data=result)
    return result


def _fallback_markets(only_krw: bool) -> List[MarketInfo]:
    if only_krw:
        return list(_FALLBACK_MARKETS)
    return list(_FALLBACK_MARKETS)


def fetch_upbit_markets(*, only_krw: bool = True) -> MarketList:
    """Return tradable markets from Upbit or a deterministic fallback list."""

    if _upbit_network_down():
        recovery = attempt_upbit_self_heal("market-directory-blocked")
        if recovery.get("status") == "success" and not _upbit_network_down():
            return fetch_upbit_markets(only_krw=only_krw)
        state = get_upbit_network_state()
        result = MarketList(
            markets=_fallback_markets(only_krw),
            source="fallback",
            status=state["status"],
            message=state["message"],
            detail=state.get("detail"),
            checked_at=state.get("checked_at"),
            backoff_seconds_remaining=state["backoff_seconds_remaining"],
        )
        log_event(
            "upbit.markets.fallback",
            level="WARNING",
            reason="network-down",
            markets=len(result.markets),
            status=result.status,
        )
        return result

    try:
        payload = _request_upbit("/v1/market/all", {"isDetails": "true"})
    except MarketRateLimitError as exc:
        detail = _describe_error(exc)
        _mark_network(
            "warning",
            message="업비트 요청 제한으로 내장 디렉터리를 사용합니다.",
            detail=detail,
        )
        state = get_upbit_network_state()
        result = MarketList(
            markets=_fallback_markets(only_krw),
            source="fallback",
            status=state["status"],
            message=state["message"],
            detail=detail,
            checked_at=state.get("checked_at"),
            backoff_seconds_remaining=state["backoff_seconds_remaining"],
        )
        log_event(
            "upbit.markets.fallback",
            level="WARNING",
            reason="rate-limit",
            markets=len(result.markets),
            detail=detail,
        )
        return result
    except MarketDataError as exc:
        detail = _describe_error(exc)
        _mark_network(
            "down",
            message="업비트 마켓 목록을 가져오지 못해 내장 디렉터리를 사용합니다.",
            detail=detail,
        )
        state = get_upbit_network_state()
        result = MarketList(
            markets=_fallback_markets(only_krw),
            source="fallback",
            status=state["status"],
            message=state["message"],
            detail=state.get("detail"),
            checked_at=state.get("checked_at"),
            backoff_seconds_remaining=state["backoff_seconds_remaining"],
        )
        log_event(
            "upbit.markets.fallback",
            level="WARNING",
            reason="request-failed",
            markets=len(result.markets),
            detail=result.detail,
        )
        return result
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
        result = MarketList(
            markets=_fallback_markets(only_krw),
            source="fallback",
            status=state["status"],
            message="업비트 응답이 비어 있어 내장 목록을 사용합니다.",
            detail=state.get("detail"),
            checked_at=state.get("checked_at"),
            backoff_seconds_remaining=state["backoff_seconds_remaining"],
        )
        log_event(
            "upbit.markets.fallback",
            level="WARNING",
            reason="empty-response",
            markets=len(result.markets),
        )
        return result

    state = get_upbit_network_state()
    result = MarketList(
        markets=markets,
        source="upbit",
        status=state["status"],
        message=state["message"],
        detail=state.get("detail"),
        checked_at=state.get("checked_at"),
        backoff_seconds_remaining=state["backoff_seconds_remaining"],
    )
    log_event(
        "upbit.markets.success",
        markets=len(result.markets),
        status=result.status,
    )
    return result


def fetch_upbit_tickers(markets: Sequence[str]) -> List[MarketTicker]:
    """Return ticker statistics for the given Upbit markets.

    Upbit 제한에 맞추어 최대 30종목씩 끊어 요청하며, 실패 시 ``MarketDataError``를
    발생시켜 호출자가 폴백 경로를 사용할 수 있도록 한다.
    """

    unique_markets = [code.upper() for code in dict.fromkeys(markets) if code]
    if not unique_markets:
        return []

    tickers: List[MarketTicker] = []
    errors: List[str] = []
    for batch in _chunked(unique_markets, 30):
        try:
            payload = _request_upbit(
                "/v1/ticker",
                {"markets": ",".join(batch)},
                allow_self_heal=False,
            )
        except MarketRateLimitError as exc:
            errors.append(str(exc))
            continue
        except MarketDataError as exc:
            errors.append(str(exc))
            continue

        for item in payload:
            try:
                market_code = str(item.get("market", "")).upper()
                trade_price = float(item.get("trade_price", 0.0))
                acc_trade_price = float(item.get("acc_trade_price_24h", 0.0))
                acc_trade_volume = float(item.get("acc_trade_volume_24h", 0.0))
                change_rate = float(item.get("signed_change_rate", 0.0)) * 100.0
                timestamp = item.get("timestamp")
                if isinstance(timestamp, (int, float)):
                    fetched_at = datetime.fromtimestamp(timestamp / 1000.0, tz=timezone.utc)
                else:
                    fetched_at = datetime.now(timezone.utc)
            except (TypeError, ValueError):
                continue

            if not market_code:
                continue
            tickers.append(
                MarketTicker(
                    market=market_code,
                    trade_price=max(0.0, trade_price),
                    acc_trade_price_24h=max(0.0, acc_trade_price),
                    acc_trade_volume_24h=max(0.0, acc_trade_volume),
                    change_rate=change_rate,
                    fetched_at=fetched_at,
                )
            )

    if errors and not tickers:
        raise MarketDataError(errors[0])

    return tickers


def fetch_upbit_top_markets(
    *,
    base_currency: str,
    limit: int,
    include_warnings: bool = False,
) -> TopMarketSelection:
    """Return markets ordered by 최근 24시간 거래대금."""

    listing = fetch_upbit_markets(only_krw=base_currency.upper() != "ALL")
    filtered: List[MarketInfo] = []
    for info in listing.markets:
        if info.trading_suspended:
            continue
        if not include_warnings and info.market_warning not in {"", "NONE"}:
            continue
        if base_currency.upper() != "ALL" and info.base_currency.upper() != base_currency.upper():
            continue
        filtered.append(info)

    errors: List[str] = []
    try:
        tickers = fetch_upbit_tickers([item.market for item in filtered])
    except MarketDataError as exc:
        errors.append(str(exc))
        tickers = []

    if tickers:
        ticker_map = {ticker.market: ticker for ticker in tickers}

        def _volume_key(info: MarketInfo) -> Tuple[float, float]:
            ticker = ticker_map.get(info.market)
            if ticker is None:
                return (0.0, 0.0)
            return (ticker.acc_trade_price_24h, ticker.acc_trade_volume_24h)

        ordered = sorted(
            filtered,
            key=_volume_key,
            reverse=True,
        )
        return TopMarketSelection(
            markets=ordered[: max(1, limit)],
            source="upbit",
            errors=errors,
        )

    ordered_fallback = sorted(filtered, key=lambda info: info.market)
    if not ordered_fallback:
        ordered_fallback = _fallback_markets(base_currency.upper() != "ALL")
    return TopMarketSelection(
        markets=ordered_fallback[: max(1, limit)],
        source="fallback-volume",
        errors=errors,
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

"""Execution engines for live (Upbit) and paper trading."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Literal, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


Side = Literal["bid", "ask"]
OrderType = Literal["limit", "market", "price"]
Mode = Literal["paper", "live"]


class ExecutionError(RuntimeError):
    """Raised when executing a trade fails."""


def _urlsafe_b64encode(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _encode_jwt(payload: dict, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_segment = _urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = b".".join([header_segment, payload_segment])
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    signature_segment = _urlsafe_b64encode(signature)
    return ".".join(segment.decode("utf-8") for segment in [header_segment, payload_segment, signature_segment])


@dataclass
class PaperPosition:
    market: str
    volume: float
    average_price: float
    market_price: float

    @property
    def market_value(self) -> float:
        return self.volume * self.market_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.market_price - self.average_price) * self.volume


@dataclass
class PaperOrder:
    order_id: str
    market: str
    side: Side
    price: float
    volume: float
    fee: float
    executed_at: datetime
    realized_pnl: float = 0.0


@dataclass
class BalanceSnapshot:
    cash: float
    portfolio_value: float
    last_update: datetime
    initial_cash: float
    positions: List[PaperPosition] = field(default_factory=list)
    orders: List[PaperOrder] = field(default_factory=list)


class PaperBroker:
    """In-memory execution engine for paper trading."""

    def __init__(self, *, fee_rate: float = 0.0005, initial_cash: float = 20_000_000.0) -> None:
        self.fee_rate = fee_rate
        self._initial_cash = float(initial_cash)
        self.reset(initial_cash=initial_cash)

    def reset(self, *, initial_cash: Optional[float] = None) -> None:
        self.cash = float(initial_cash if initial_cash is not None else self._initial_cash)
        self.positions: Dict[str, PaperPosition] = {}
        self.orders: List[PaperOrder] = []
        self.last_prices: Dict[str, float] = {}
        self.last_update = datetime.utcnow()
        if initial_cash is not None:
            self._initial_cash = float(initial_cash)

    @property
    def initial_cash(self) -> float:
        """Expose the configured starting capital for reporting."""

        return self._initial_cash

    def mark_price(self, *, market: str, price: float) -> PaperPosition:
        if price <= 0:
            raise ExecutionError("시세는 0보다 커야 합니다.")
        market = market.upper()
        position = self.positions.get(market)
        if position is None:
            position = PaperPosition(market=market, volume=0.0, average_price=0.0, market_price=price)
            self.positions[market] = position
        else:
            position.market_price = price
        self.last_prices[market] = price
        self.last_update = datetime.utcnow()
        return position

    def get_last_price(self, market: str) -> Optional[float]:
        """Return the most recent marked price for the given market if available."""

        return self.last_prices.get(market.upper())

    def submit_order(
        self,
        *,
        market: str,
        side: Side,
        price: Optional[float],
        volume: Optional[float],
        ord_type: OrderType = "limit",
    ) -> BalanceSnapshot:
        market = market.upper()
        if volume is None or volume <= 0:
            raise ExecutionError("거래 수량은 0보다 커야 합니다.")
        existing = self.positions.get(market)
        if side == "ask" and (existing is None or existing.volume <= 0):
            raise ExecutionError("해당 종목을 보유하고 있지 않습니다.")

        def resolve_price() -> float:
            if price is not None and price > 0:
                return float(price)
            if existing and existing.market_price > 0:
                return float(existing.market_price)
            fallback = self.last_prices.get(market)
            if fallback and fallback > 0:
                return float(fallback)
            raise ExecutionError("시장가 주문을 실행하려면 최신 시세를 먼저 동기화하세요.")

        price_value = resolve_price()
        volume = float(volume)
        fee = price_value * volume * self.fee_rate
        realized_pnl = 0.0

        if side == "bid":
            cost = price_value * volume + fee
            if cost > self.cash + 1e-6:
                raise ExecutionError("가용 현금이 부족합니다.")
            if existing:
                total_value = existing.average_price * existing.volume + price_value * volume
                total_volume = existing.volume + volume
                avg_price = total_value / total_volume if total_volume else price_value
                existing.volume = total_volume
                existing.average_price = avg_price
                existing.market_price = price_value
            else:
                self.positions[market] = PaperPosition(
                    market=market,
                    volume=volume,
                    average_price=price_value,
                    market_price=price_value,
                )
            self.cash -= cost
        else:
            position = existing  # type: ignore[assignment]
            if position is None:
                raise ExecutionError("해당 종목을 보유하고 있지 않습니다.")
            if volume - position.volume > 1e-9:
                raise ExecutionError("보유 수량보다 많이 매도할 수 없습니다.")
            proceeds = price_value * volume - fee
            self.cash += proceeds
            realized_pnl = (price_value - position.average_price) * volume - fee
            position.volume -= volume
            position.market_price = price_value
            if position.volume <= 1e-9:
                del self.positions[market]

        order = PaperOrder(
            order_id=str(uuid.uuid4()),
            market=market,
            side=side,
            price=price_value,
            volume=volume,
            fee=fee,
            executed_at=datetime.utcnow(),
            realized_pnl=realized_pnl,
        )
        self.orders.insert(0, order)
        self.last_prices[market] = price_value
        self.last_update = datetime.utcnow()
        return self.snapshot()

    def snapshot(self) -> BalanceSnapshot:
        positions = [pos for pos in self.positions.values() if pos.volume > 1e-9]
        portfolio_value = self.cash + sum(pos.market_value for pos in positions)
        return BalanceSnapshot(
            cash=self.cash,
            portfolio_value=portfolio_value,
            last_update=self.last_update,
            initial_cash=self._initial_cash,
            positions=positions,
            orders=self.orders[:20],
        )


class UpbitClient:
    """Lightweight REST client for the Upbit exchange."""

    def __init__(self, *, access_key: str, secret_key: str, base_url: str = "https://api.upbit.com") -> None:
        self.access_key = access_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")

    def _auth_headers(self, params: Optional[dict] = None) -> dict:
        payload: dict = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
        }
        if params:
            query = urlencode(params, doseq=True)
            h = hashlib.sha512()
            h.update(query.encode("utf-8"))
            payload["query_hash"] = h.hexdigest()
            payload["query_hash_alg"] = "SHA512"
        token = _encode_jwt(payload, self.secret_key)
        return {"Authorization": f"Bearer {token}"}

    def _request(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = self._auth_headers(payload if payload else None)
        if payload is not None:
            headers["Content-Type"] = "application/json"

        request = Request(url, data=body, method=method.upper(), headers=headers)
        try:
            with urlopen(request, timeout=10) as response:
                content = response.read()
                if not content:
                    return {}
                return json.loads(content.decode("utf-8"))
        except HTTPError as exc:
            detail = None
            try:
                error_body = exc.read().decode("utf-8")
                detail_json = json.loads(error_body)
                detail = detail_json.get("error", {}).get("message")
            except Exception:
                detail = str(exc.reason)
            raise ExecutionError(detail or "업비트 요청에 실패했습니다.") from exc
        except URLError as exc:
            raise ExecutionError("업비트 요청에 실패했습니다.") from exc

    def create_order(
        self,
        *,
        market: str,
        side: Side,
        ord_type: OrderType,
        volume: Optional[float] = None,
        price: Optional[float] = None,
    ) -> dict:
        payload: dict = {
            "market": market,
            "side": side,
            "ord_type": ord_type,
        }
        if volume is not None:
            payload["volume"] = str(volume)
        if price is not None:
            payload["price"] = str(price)
        return self._request("POST", "/v1/orders", payload)

    def get_balances(self) -> List[dict]:
        data = self._request("GET", "/v1/accounts")
        if isinstance(data, list):
            return data
        raise ExecutionError("업비트 잔고 조회에 실패했습니다.")


_paper_broker = PaperBroker()


def paper_broker() -> PaperBroker:
    return _paper_broker


def create_upbit_client_from_env() -> UpbitClient:
    access_key = os.getenv("UPBIT_ACCESS_KEY")
    secret_key = os.getenv("UPBIT_SECRET_KEY")
    base_url = os.getenv("UPBIT_BASE_URL", "https://api.upbit.com")
    if not access_key or not secret_key:
        raise ExecutionError("UPBIT_ACCESS_KEY/SECRET_KEY 환경 변수를 설정하세요.")
    return UpbitClient(access_key=access_key, secret_key=secret_key, base_url=base_url)

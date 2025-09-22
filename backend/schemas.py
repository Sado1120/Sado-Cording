"""Pydantic schemas shared by the FastAPI service."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, validator


class CandlePayload(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class SimulationRequest(BaseModel):
    fast_period: int = Field(12, ge=2, le=60)
    slow_period: int = Field(26, ge=3, le=200)
    initial_capital: float = Field(5_000_000, gt=0)
    fee_rate: float = Field(0.0005, ge=0, le=0.01)
    seed: Optional[int] = Field(None, description="Seed for reproducible synthetic data")
    prices: Optional[List[CandlePayload]] = None

    @validator("slow_period")
    def _validate_periods(cls, slow_period: int, values):
        fast_period = values.get("fast_period")
        if fast_period and slow_period <= fast_period:
            raise ValueError("slow_period must be greater than fast_period")
        return slow_period


class TradePayload(BaseModel):
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    return_pct: float


class SimulationResponse(BaseModel):
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    trades: List[TradePayload]
    equity_curve: List[float]
    trade_summary: dict


class RebalanceRequest(BaseModel):
    current_positions: dict
    target_allocations: dict
    portfolio_value: float


class RebalanceResponse(BaseModel):
    orders: dict

"""Adaptive learning helpers for the auto-trading loop."""

from __future__ import annotations

import json
import math
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Optional

from .execution import BalanceSnapshot


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


@dataclass
class AdaptiveProfile:
    """Snapshot of the learner's most recent adjustments."""

    min_confidence_pct: float
    max_position_pct: float
    risk_appetite: float
    rolling_return_pct: float
    rolling_volatility_pct: float
    sharpe_like: float
    trades_tracked: int
    last_updated_at: datetime

    def as_dict(self) -> dict:
        return {
            "min_confidence_pct": self.min_confidence_pct,
            "max_position_pct": self.max_position_pct,
            "risk_appetite": self.risk_appetite,
            "rolling_return_pct": self.rolling_return_pct,
            "rolling_volatility_pct": self.rolling_volatility_pct,
            "sharpe_like": self.sharpe_like,
            "trades_tracked": self.trades_tracked,
            "last_updated_at": self.last_updated_at.isoformat(),
        }


class AdaptiveLearner:
    """Lightweight learner that nudges auto-trading parameters over time."""

    def __init__(self, *, path: Optional[str | Path] = None, history: int = 60) -> None:
        self._path = Path(path or os.getenv("ADAPTIVE_STATE_PATH", "data/adaptive_state.json"))
        self._history: Deque[float] = deque(maxlen=max(10, history))
        self._last_equity: Optional[float] = None
        self._profile = AdaptiveProfile(
            min_confidence_pct=50.0,
            max_position_pct=0.25,
            risk_appetite=0.5,
            rolling_return_pct=0.0,
            rolling_volatility_pct=0.0,
            sharpe_like=0.0,
            trades_tracked=0,
            last_updated_at=datetime.now(timezone.utc),
        )
        self._session_observations = 0
        self._load()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # pragma: no cover - corrupt files
            return

        profile_data = data.get("profile")
        if isinstance(profile_data, dict):
            self._profile = AdaptiveProfile(
                min_confidence_pct=float(profile_data.get("min_confidence_pct", 50.0)),
                max_position_pct=float(profile_data.get("max_position_pct", 0.25)),
                risk_appetite=float(profile_data.get("risk_appetite", 0.5)),
                rolling_return_pct=float(profile_data.get("rolling_return_pct", 0.0)),
                rolling_volatility_pct=float(profile_data.get("rolling_volatility_pct", 0.0)),
                sharpe_like=float(profile_data.get("sharpe_like", 0.0)),
                trades_tracked=int(profile_data.get("trades_tracked", 0)),
                last_updated_at=self._parse_timestamp(profile_data.get("last_updated_at")),
            )

        history = data.get("history", [])
        if isinstance(history, list):
            for value in history[-self._history.maxlen :]:
                try:
                    self._history.append(float(value))
                except (TypeError, ValueError):
                    continue

        last_equity = data.get("last_equity")
        try:
            self._last_equity = float(last_equity) if last_equity is not None else None
        except (TypeError, ValueError):
            self._last_equity = None

    @staticmethod
    def _parse_timestamp(raw: object) -> datetime:
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw)
            except ValueError:  # pragma: no cover - defensive
                pass
        return datetime.now(timezone.utc)

    def _persist(self) -> None:
        payload = {
            "profile": self._profile.as_dict(),
            "history": list(self._history),
            "last_equity": self._last_equity,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def profile(self) -> AdaptiveProfile:
        return self._profile

    def reset(self, *, initial_equity: float, config) -> AdaptiveProfile:
        """Reset baseline metrics when the auto-pilot starts."""

        if initial_equity > 0:
            self._last_equity = float(initial_equity)
        self._profile.min_confidence_pct = float(config.min_confidence_pct)
        self._profile.max_position_pct = float(config.max_position_pct)
        self._profile.risk_appetite = float(config.risk_appetite)
        self._profile.last_updated_at = datetime.now(timezone.utc)
        self._session_observations = 0
        self._persist()
        return self._profile

    def apply(self, *, config) -> AdaptiveProfile:
        """Apply the latest learned parameters to the config."""

        config.min_confidence_pct = _clamp(self._profile.min_confidence_pct, 35.0, 70.0)
        config.max_position_pct = _clamp(self._profile.max_position_pct, 0.05, 0.5)
        config.risk_appetite = _clamp(self._profile.risk_appetite, 0.1, 0.95)
        self._profile.min_confidence_pct = config.min_confidence_pct
        self._profile.max_position_pct = config.max_position_pct
        self._profile.risk_appetite = config.risk_appetite
        self._profile.last_updated_at = datetime.now(timezone.utc)
        self._persist()
        return self._profile

    def observe(
        self,
        *,
        config,
        before: BalanceSnapshot,
        after: BalanceSnapshot,
        executed: bool,
    ) -> AdaptiveProfile:
        """Update the learner with the result of a cycle."""

        equity_before = max(before.portfolio_value, 1e-6)
        equity_after = max(after.portfolio_value, 1e-6)
        self._last_equity = equity_after

        realised_return = (equity_after - equity_before) / equity_before
        self._history.append(realised_return)
        self._session_observations += 1

        avg_return = sum(self._history) / len(self._history)
        variance = 0.0
        if len(self._history) >= 2:
            variance = sum((value - avg_return) ** 2 for value in self._history) / len(self._history)
        volatility = math.sqrt(max(0.0, variance))
        sharpe_like = avg_return / (volatility + 1e-6)

        intensity = min(1.0, len(self._history) / 8.0)
        adjustment_base = avg_return * 100.0 * 1.5
        if executed:
            adjustment_base *= 1.25
        adjustment_base = _clamp(adjustment_base * intensity, -3.0, 3.0)

        if self._session_observations < 1 or abs(avg_return) < 0.001:
            adjustment_base = 0.0

        if adjustment_base > 0:
            config.min_confidence_pct = _clamp(
                config.min_confidence_pct - adjustment_base / 2.0,
                35.0,
                70.0,
            )
            config.risk_appetite = _clamp(
                config.risk_appetite + adjustment_base / 150.0,
                0.1,
                0.95,
            )
            config.max_position_pct = _clamp(
                config.max_position_pct + adjustment_base / 220.0,
                0.05,
                0.5,
            )
        elif adjustment_base < 0:
            magnitude = abs(adjustment_base)
            config.min_confidence_pct = _clamp(
                config.min_confidence_pct + magnitude / 1.4,
                35.0,
                70.0,
            )
            config.risk_appetite = _clamp(
                config.risk_appetite - magnitude / 160.0,
                0.1,
                0.95,
            )
            config.max_position_pct = _clamp(
                config.max_position_pct - magnitude / 240.0,
                0.05,
                0.5,
            )

        trades_tracked = self._profile.trades_tracked + (1 if executed else 0)
        self._profile = AdaptiveProfile(
            min_confidence_pct=float(config.min_confidence_pct),
            max_position_pct=float(config.max_position_pct),
            risk_appetite=float(config.risk_appetite),
            rolling_return_pct=avg_return * 100.0,
            rolling_volatility_pct=volatility * 100.0,
            sharpe_like=sharpe_like,
            trades_tracked=trades_tracked,
            last_updated_at=datetime.now(timezone.utc),
        )
        self._persist()
        return self._profile


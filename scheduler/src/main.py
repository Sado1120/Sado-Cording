"""Scheduler service for recurring data ingestion and indexing.

Run: python -m src.main
Test: python -m src.main --dry-run
Deploy: docker compose up --build scheduler
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable

import httpx
import pandas as pd
import schedule
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    api_base: str = "http://api:8000"
    data_root: str = "/app/data"
    index_interval_minutes: int = 60

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)


settings = Settings()
heartbeat_path = Path("/tmp/scheduler_heartbeat")


def fetch_signals(endpoint: str) -> Dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        response = client.get(f"{settings.api_base}{endpoint}")
        response.raise_for_status()
        return response.json()


def persist_dataframe(name: str, payload: Any) -> None:
    rows: Iterable[Dict[str, Any]]
    if isinstance(payload, dict):
        rows_list: list[Dict[str, Any]] = []
        for bucket, items in payload.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    rows_list.append({"bucket": bucket, **item})
        rows = rows_list
    elif isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]
    else:
        return

    df = pd.DataFrame(list(rows))
    if df.empty:
        return
    path = Path(settings.data_root) / f"{name}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def update_portfolio_cache() -> None:
    for risk in ("conservative", "balanced", "aggressive"):
        portfolio = fetch_signals(f"/portfolio/suggestion?risk_level={risk}")
        persist_dataframe(f"portfolio_{risk}", portfolio)
    _touch_heartbeat()


def update_maintenance_logs() -> None:
    logs = fetch_signals("/maintenance/insights")
    persist_dataframe("maintenance_snapshot", logs.get("insights", []))
    _touch_heartbeat()


def run_index_cycle() -> None:
    update_portfolio_cache()
    update_maintenance_logs()


def _touch_heartbeat() -> None:
    heartbeat_path.write_text(datetime.utcnow().isoformat())


def main(dry_run: bool = False) -> None:
    if dry_run:
        run_index_cycle()
        return

    schedule.every(settings.index_interval_minutes).minutes.do(run_index_cycle)
    run_index_cycle()

    while True:
        schedule.run_pending()
        time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="execute one cycle and exit")
    args = parser.parse_args()
    try:
        main(dry_run=args.dry_run)
    except httpx.HTTPError as exc:
        print(json.dumps({"error": str(exc)}))
        os._exit(1)

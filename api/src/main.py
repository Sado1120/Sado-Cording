"""FastAPI service exposing maintenance copilot endpoints.

Run: uvicorn src.main:app --host 0.0.0.0 --port 8000
Test: python -m httpx get http://127.0.0.1:8000/healthz
Deploy: docker compose up --build api
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd
from fastapi import FastAPI, HTTPException

from .config import get_settings
from .vector import VectorClient

app = FastAPI(title="Sado Maintenance Copilot API", version="0.1.0")

settings = get_settings()
vector_client = VectorClient(settings.vector_host, settings.vector_port)


def _resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def load_dataframe(name: str) -> pd.DataFrame:
    data_path = _resolve(settings.data_root) / f"{name}.csv"
    if not data_path.exists():
        return pd.DataFrame()
    return pd.read_csv(data_path)


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/portfolio/suggestion")
def portfolio_suggestion(risk_level: str = "balanced") -> Dict[str, List[Dict[str, float]]]:
    risk_level = risk_level.lower()
    if risk_level not in {"conservative", "balanced", "aggressive"}:
        raise HTTPException(status_code=400, detail="risk_level must be conservative|balanced|aggressive")

    etf_df = load_dataframe("etf_signals")
    coin_df = load_dataframe("coin_signals")

    allocations: Dict[str, List[Dict[str, float]]] = {"etf": [], "coin": []}

    if not etf_df.empty:
        allocations["etf"] = _build_allocations(etf_df, risk_level)
    if not coin_df.empty:
        allocations["coin"] = _build_allocations(coin_df, risk_level)

    return allocations


@app.get("/maintenance/insights")
def maintenance_insights(equipment_id: str | None = None) -> Dict[str, List[Dict[str, str]]]:
    df = load_dataframe("maintenance_logs")
    if df.empty:
        return {"insights": []}

    if equipment_id:
        df = df[df["equipment_id"] == equipment_id]

    df = df.sort_values("timestamp", ascending=False).head(50)
    insights = [
        {
            "equipment_id": row.equipment_id,
            "issue": row.issue,
            "recommendation": row.recommendation,
            "timestamp": row.timestamp,
        }
        for row in df.itertuples()
    ]
    return {"insights": insights}


@app.post("/vector/upsert")
def vector_upsert(items: List[Dict[str, str]]):
    if not items:
        raise HTTPException(status_code=400, detail="no items provided")
    result = vector_client.upsert(items)
    return {"upserted": result}


@app.post("/vector/query")
def vector_query(query: Dict[str, str]):
    if "text" not in query:
        raise HTTPException(status_code=400, detail="text field required")
    return {"matches": vector_client.query(query["text"], top_k=int(query.get("top_k", 5)))}


def _build_allocations(df: pd.DataFrame, risk_level: str) -> List[Dict[str, float]]:
    weight_column = {
        "conservative": "weight_low",
        "balanced": "weight_mid",
        "aggressive": "weight_high",
    }[risk_level]
    if weight_column not in df.columns:
        raise HTTPException(status_code=500, detail=f"missing column {weight_column}")

    allocations = [
        {
            "symbol": row.symbol,
            "weight": float(row[weight_column]),
            "score": float(row.signal_score) if "signal_score" in df.columns else 0.0,
        }
        for _, row in df.iterrows()
    ]
    return allocations

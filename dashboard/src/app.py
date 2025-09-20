"""Dashboard FastAPI serving a static maintenance cockpit.

Run: uvicorn src.app:app --host 0.0.0.0 --port 8080
Test: python -m httpx get http://127.0.0.1:8080/healthz
Deploy: docker compose up --build dashboard
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

API_BASE = "http://api:8000"

app = FastAPI(title="Sado Maintenance Dashboard", version="0.1.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent), name="static")


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def index() -> HTMLResponse:
    index_path = Path(__file__).parent / "index.html"
    html = index_path.read_text(encoding="utf-8")
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            portfolio = await _fetch(client, "/portfolio/suggestion?risk_level=balanced")
            maintenance = await _fetch(client, "/maintenance/insights")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    html = html.replace("{{PORTFOLIO_JSON}}", portfolio).replace("{{MAINTENANCE_JSON}}", maintenance)
    return HTMLResponse(content=html)


async def _fetch(client: httpx.AsyncClient, endpoint: str) -> str:
    response = await client.get(f"{API_BASE}{endpoint}")
    response.raise_for_status()
    return response.text

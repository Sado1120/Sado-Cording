"""Utility for interacting with optional FAISS vector store service.

Run: python -m src.vector
Test: python -m unittest discover -s src -p 'test_*.py'
Deploy: docker compose --profile faiss up --build vectorstore
"""
from __future__ import annotations

import json
from typing import Dict, List

import httpx


class VectorClient:
    def __init__(self, host: str, port: int) -> None:
        self.base_url = f"http://{host}:{port}"

    def upsert(self, items: List[Dict[str, str]]) -> int:
        try:
            response = httpx.post(f"{self.base_url}/upsert", json={"items": items}, timeout=10)
            response.raise_for_status()
            data = response.json()
            return int(data.get("upserted", 0))
        except httpx.HTTPError:
            return 0

    def query(self, text: str, top_k: int = 5) -> List[Dict[str, str]]:
        try:
            response = httpx.post(f"{self.base_url}/query", json={"text": text, "top_k": top_k}, timeout=10)
            response.raise_for_status()
            return response.json().get("matches", [])
        except httpx.HTTPError:
            return []


if __name__ == "__main__":
    client = VectorClient("127.0.0.1", 9000)
    sample = [{"id": "demo", "text": "Synology predictive maintenance"}]
    print(json.dumps({"upserted": client.upsert(sample)}, indent=2))

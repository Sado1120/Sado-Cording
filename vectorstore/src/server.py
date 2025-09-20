"""Minimal FAISS-backed vector microservice.

Run: uvicorn src.server:app --host 0.0.0.0 --port 9000
Test: python -m httpx post http://127.0.0.1:9000/upsert -j '{"items":[]}'
Deploy: docker compose --profile faiss up --build vectorstore
"""
from __future__ import annotations

import threading
from typing import Dict, List

import faiss
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DIMENSION = 128


class UpsertPayload(BaseModel):
    items: List[Dict[str, str]]


class QueryPayload(BaseModel):
    text: str
    top_k: int = 5


class VectorStore:
    def __init__(self, dimension: int = DIMENSION) -> None:
        self.dimension = dimension
        self.index = faiss.IndexFlatIP(dimension)
        self.lock = threading.Lock()
        self.store: Dict[str, np.ndarray] = {}
        self.metadata: Dict[str, Dict[str, str]] = {}

    def _rebuild(self) -> None:
        self.index.reset()
        if not self.store:
            return
        matrix = np.stack(list(self.store.values())).astype("float32")
        faiss.normalize_L2(matrix)
        self.index.add(matrix)

    def upsert(self, items: List[Dict[str, str]]) -> int:
        changed = 0
        with self.lock:
            for item in items:
                if "id" not in item or "text" not in item:
                    raise HTTPException(status_code=400, detail="Each item requires id and text")
                vector = self._embed(item["text"])
                self.store[item["id"]] = vector
                self.metadata[item["id"]] = item
                changed += 1
            self._rebuild()
        return changed

    def query(self, text: str, top_k: int = 5) -> List[Dict[str, str]]:
        with self.lock:
            if not self.store:
                return []
            vector = self._embed(text)
            vector = vector.reshape(1, -1).astype("float32")
            faiss.normalize_L2(vector)
            distances, indices = self.index.search(vector, min(top_k, len(self.store)))
            ids = list(self.store.keys())
            results = []
            for score, idx in zip(distances[0], indices[0]):
                if idx == -1:
                    continue
                meta = self.metadata[ids[idx]].copy()
                meta["score"] = float(score)
                results.append(meta)
            return results

    def _embed(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimension, dtype="float32")
        for token in text.lower().split():
            slot = hash(token) % self.dimension
            vector[slot] += 1.0
        if np.linalg.norm(vector) == 0.0:
            vector[0] = 1.0
        return vector


store = VectorStore()
app = FastAPI(title="Sado Vector Store", version="0.1.0")


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/upsert")
def upsert(payload: UpsertPayload) -> Dict[str, int]:
    return {"upserted": store.upsert(payload.items)}


@app.post("/query")
def query(payload: QueryPayload) -> Dict[str, List[Dict[str, str]]]:
    return {"matches": store.query(payload.text, payload.top_k)}

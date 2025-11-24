"""Assistant history persistence utilities."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List


@dataclass
class AssistantHistoryEntry:
    """Lightweight snapshot of an assistant exchange for dashboard history."""

    generated_at: datetime
    question: str
    answer: str
    insights: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    risk_notices: List[str] = field(default_factory=list)
    pushed_to_chat: bool = False

    def serialise(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "question": self.question,
            "answer": self.answer,
            "insights": list(self.insights or []),
            "next_steps": list(self.next_steps or []),
            "risk_notices": list(self.risk_notices or []),
            "pushed_to_chat": bool(self.pushed_to_chat),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "AssistantHistoryEntry":
        timestamp = payload.get("generated_at")
        if isinstance(timestamp, datetime):
            generated_at = timestamp
        else:
            try:
                generated_at = datetime.fromisoformat(str(timestamp))
            except Exception:  # pragma: no cover - defensive fallback
                generated_at = datetime.utcnow().replace(tzinfo=timezone.utc)
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)

        return cls(
            generated_at=generated_at,
            question=str(payload.get("question") or ""),
            answer=str(payload.get("answer") or ""),
            insights=list(payload.get("insights") or []),
            next_steps=list(payload.get("next_steps") or []),
            risk_notices=list(payload.get("risk_notices") or []),
            pushed_to_chat=bool(payload.get("pushed_to_chat")),
        )


class AssistantHistoryStore:
    """Thread-safe persistence layer for the assistant Q&A history."""

    def __init__(self, path: Path, *, max_entries: int = 24) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._max_entries = max_entries
        self._entries: List[AssistantHistoryEntry] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            content = self._path.read_text(encoding="utf-8")
        except OSError:
            return
        if not content.strip():
            return
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, list):
            return
        entries = []
        for item in payload[: self._max_entries]:
            if isinstance(item, dict):
                try:
                    entries.append(AssistantHistoryEntry.from_dict(item))
                except Exception:
                    continue
        with self._lock:
            self._entries = entries

    def _flush(self) -> None:
        serialised = [entry.serialise() for entry in self._entries[: self._max_entries]]
        try:
            self._path.write_text(json.dumps(serialised, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            # persistence is best-effort; swallow filesystem issues
            pass

    def list(self) -> List[AssistantHistoryEntry]:
        with self._lock:
            return list(self._entries)

    def replace(self, entries: Iterable[AssistantHistoryEntry]) -> None:
        with self._lock:
            self._entries = list(entries)[: self._max_entries]
            self._flush()

    def append(self, entry: AssistantHistoryEntry) -> None:
        with self._lock:
            self._entries.insert(0, entry)
            self._entries = self._entries[: self._max_entries]
            self._flush()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._flush()

"""Logging utilities for the Sado Trade Bot backend."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOGGER_INITIALISED = False
_LOG_FILE: Optional[Path] = None


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    return value


def _log_level_from_env(default: str = "INFO") -> int:
    level_name = os.getenv("SADO_LOG_LEVEL", default).upper()
    return getattr(logging, level_name, logging.INFO)


def _resolve_log_file() -> Path:
    base_dir = Path(os.getenv("SADO_LOG_DIR", "./logs")).expanduser()
    file_name = os.getenv("SADO_LOG_FILE", "sado-trade-bot.log")
    return base_dir / file_name


def _ensure_logger() -> logging.Logger:
    global _LOGGER_INITIALISED, _LOG_FILE
    if _LOGGER_INITIALISED:
        return logging.getLogger("sado")

    log_path = _resolve_log_file()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger("sado")
    root.setLevel(_log_level_from_env())
    root.addHandler(handler)
    root.propagate = False

    _LOGGER_INITIALISED = True
    _LOG_FILE = log_path
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a logger namespaced under the sado root."""

    _ensure_logger()
    if name.startswith("sado"):
        return logging.getLogger(name)
    return logging.getLogger(f"sado.{name}")


def log_event(event: str, *, level: str = "INFO", **fields: Any) -> None:
    """Record a structured audit event to the shared log."""

    logger = get_logger("events")
    level_name = level.upper()
    level_value = getattr(logging, level_name, logging.INFO)
    payload: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": event,
        "level": level_name,
    }
    if fields:
        for key, value in fields.items():
            try:
                json.dumps(value, default=_json_default)
            except TypeError:
                value = _json_default(value)
            payload[key] = value
    logger.log(level_value, json.dumps(payload, ensure_ascii=False, default=_json_default))


def log_exception(event: str, exc: BaseException, **fields: Any) -> None:
    """Helper that records an exception with traceback details."""

    details = dict(fields)
    details.setdefault("error", str(exc))
    details.setdefault("exception", exc.__class__.__name__)
    log_event(event, level="ERROR", **details)


def read_log_tail(max_lines: int = 200) -> List[Dict[str, Any]]:
    """Return the most recent structured log entries."""

    _ensure_logger()
    if _LOG_FILE is None or not _LOG_FILE.exists():
        return []

    if max_lines <= 0:
        return []

    with _LOG_FILE.open("r", encoding="utf-8") as handle:
        lines = handle.readlines()

    selected = lines[-max_lines:]
    entries: List[Dict[str, Any]] = []
    for raw in selected:
        raw_line = raw.strip()
        if not raw_line:
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            payload = {
                "timestamp": None,
                "event": "unparsed",
                "level": "INFO",
                "message": raw_line,
            }
        entries.append(payload)
    return entries


def get_log_file_path() -> Optional[Path]:
    """Expose the absolute path to the structured log file."""

    _ensure_logger()
    return _LOG_FILE


def reset_logging_for_tests() -> None:
    """Reset the logging configuration (used in unit tests)."""

    global _LOGGER_INITIALISED, _LOG_FILE
    root = logging.getLogger("sado")
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    _LOGGER_INITIALISED = False
    _LOG_FILE = None

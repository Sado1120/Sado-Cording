"""Application version helpers for Sado Trade Bot."""
from __future__ import annotations

import os
from pathlib import Path


def _read_version_file() -> str:
    version_path = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        raw = version_path.read_text("utf-8").strip()
    except OSError:
        return "dev"
    return raw or "dev"


APP_VERSION: str = os.getenv("SADO_BOT_VERSION", _read_version_file())


__all__ = ["APP_VERSION"]

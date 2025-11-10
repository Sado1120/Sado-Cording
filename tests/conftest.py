import os
import sys
from pathlib import Path

import pytest

# Provide deterministic dashboard credentials for tests without exposing them in
# production defaults. These values mirror the historical fixtures used by the
# integration suite and are loaded before the application modules import the
# credential store.
os.environ.setdefault("DASHBOARD_USERNAME", "sado0809@example.com")
os.environ.setdefault("DASHBOARD_PASSWORD", "honges08!!")
os.environ.setdefault("DASHBOARD_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("DASHBOARD_EMAIL_VERIFIED", "1")

from backend.security import auth_manager


@pytest.fixture(autouse=True)
def reset_security_store():
    """Ensure each test starts with a fresh credential store."""

    store_path = Path(os.getenv("SECURITY_STORE_PATH", "./data/security.json"))
    if store_path.exists():
        store_path.unlink()

    auth_manager._store._data = None  # type: ignore[attr-defined]
    auth_manager._store._load()  # type: ignore[attr-defined]
    yield


# Ensure project root is importable when running pytest from any working directory
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

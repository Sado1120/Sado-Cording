import os
import sys
import tempfile
from pathlib import Path

# Ensure project root is importable when running pytest from any working directory
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DASHBOARD_USERNAME", "sado0809@example.com")
os.environ.setdefault("DASHBOARD_PASSWORD", "honges08!!")
os.environ.setdefault("DASHBOARD_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
os.environ.setdefault("DASHBOARD_TOTP_ENABLED", "true")
os.environ.setdefault("DASHBOARD_EMAIL_VERIFIED", "true")
default_store_path = Path(tempfile.gettempdir()) / "sado_cording_security.json"
os.environ.setdefault("SECURITY_STORE_PATH", str(default_store_path))
default_store_path.parent.mkdir(parents=True, exist_ok=True)
if default_store_path.exists():
    default_store_path.unlink()

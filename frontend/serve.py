"""Static asset server with API reverse proxy support for the dashboard."""

from __future__ import annotations

import json
import mimetypes
import os
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from io import BytesIO
from pathlib import Path
from typing import Dict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent


def _backend_candidates() -> list[str]:
    """Return backend URL candidates in priority order."""

    candidates: list[str] = []
    env_url = os.environ.get("BACKEND_URL")
    if env_url:
        candidates.append(env_url)
    else:
        candidates.extend(
            [
                "http://backend:8000",  # Docker Compose 기본 네트워크 이름
                "http://127.0.0.1:8000",  # 로컬 단독 실행 시 기본 루프백
                "http://localhost:8000",
            ]
        )

    normalised: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        url = url.rstrip("/")
        if not url or url in seen:
            continue
        normalised.append(url)
        seen.add(url)
    return normalised or ["http://127.0.0.1:8000"]


BACKEND_CANDIDATES = _backend_candidates()
BACKEND_URL = BACKEND_CANDIDATES[0]

# Align JavaScript MIME type with modern browsers so charset configuration is applied.
mimetypes.add_type("application/javascript", ".js")


def _filtered_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Return headers that are safe to forward to the FastAPI backend."""

    blocked = {"host", "accept-encoding", "content-length", "connection"}
    return {key: value for key, value in headers.items() if key.lower() not in blocked}


def resolve_backend_url(path: str, base_url: str | None = None) -> str:
    """Translate an `/api` dashboard path to the upstream backend URL."""

    base = (base_url or BACKEND_URL).rstrip("/")
    suffix = path[len("/api") :]
    if not suffix:
        suffix = "/"
    elif not suffix.startswith("/"):
        suffix = f"/{suffix}"
    return f"{base}{suffix}"


class UTF8RequestHandler(SimpleHTTPRequestHandler):
    """Serve static assets with explicit UTF-8 content types."""

    text_like_types = {
        "application/javascript",
        "application/json",
        "image/svg+xml",
    }

    def guess_type(self, path: str) -> str:  # noqa: D401 - consistent with base class
        """Return UTF-8 aware MIME types for text assets."""

        mime, _ = mimetypes.guess_type(path)
        if not mime:
            return "application/octet-stream"

        if mime.startswith("text/") or mime in self.text_like_types:
            if "charset" not in mime:
                return f"{mime}; charset=utf-8"
        return mime

    def log_message(self, format: str, *args) -> None:  # noqa: A003 - inherited signature
        # Suppress default stdout logging to keep Synology logs clean.
        return


class DashboardRequestHandler(UTF8RequestHandler):
    """Serve dashboard assets and transparently proxy API calls."""

    def _proxy_request(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else None
        request_headers = _filtered_headers(dict(self.headers.items()))

        errors: list[str] = []

        for base in BACKEND_CANDIDATES:
            target_url = resolve_backend_url(self.path, base)
            proxy_request = Request(
                target_url,
                data=body,
                headers=request_headers,
                method=self.command,
            )

            try:
                response = urlopen(proxy_request, timeout=15)
            except HTTPError as exc:  # pragma: no cover - surfaced as HTTP response
                payload = exc.read() or b""
                self.send_response(exc.code)
                for header, value in exc.headers.items():
                    if header.lower() == "transfer-encoding":
                        continue
                    self.send_header(header, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if payload:
                    self.wfile.write(payload)
                return
            except (URLError, TimeoutError, OSError) as exc:
                errors.append(f"{base}: {exc}")
                continue

            global BACKEND_URL
            BACKEND_URL = base

            with response:
                payload = response.read()
                self.send_response(response.getcode())
                for header, value in response.getheaders():
                    header_lower = header.lower()
                    if header_lower in {"transfer-encoding", "content-encoding"}:
                        continue
                    self.send_header(header, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if payload:
                    self.wfile.write(payload)
                return

        # 모든 후보가 실패한 경우 502 응답을 반환한다.
        message = "백엔드에 연결할 수 없습니다. 시도한 주소: " + ", ".join(errors)
        payload = json.dumps({"detail": message}).encode("utf-8")
        self.send_response(502)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _maybe_proxy(self) -> bool:
        if self.path.startswith("/api/") or self.path in {"/api", "/api/"}:
            if not hasattr(self, "rfile") or self.rfile is None:
                self.rfile = BytesIO()
            if not hasattr(self, "wfile") or self.wfile is None:
                self.wfile = BytesIO()
            self._proxy_request()
            return True
        return False

    def do_GET(self) -> None:  # noqa: D401 - align with base implementation
        if self._maybe_proxy():
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: D401 - align with base implementation
        if self._maybe_proxy():
            return
        super().do_POST()

    def do_PUT(self) -> None:  # noqa: D401 - align with base implementation
        if self._maybe_proxy():
            return
        super().do_PUT()

    def do_DELETE(self) -> None:  # noqa: D401 - align with base implementation
        if self._maybe_proxy():
            return
        super().do_DELETE()

    def do_PATCH(self) -> None:  # noqa: D401 - align with base implementation
        if self._maybe_proxy():
            return
        super().do_PATCH()


if __name__ == "__main__":
    handler = partial(DashboardRequestHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(("0.0.0.0", 8501), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - manual shutdown
        pass

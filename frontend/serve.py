from __future__ import annotations

import mimetypes
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path


ROOT = Path(__file__).resolve().parent


# Align JavaScript MIME type with modern browsers so charset configuration is applied.
mimetypes.add_type("application/javascript", ".js")


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


if __name__ == "__main__":
    handler = partial(UTF8RequestHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(("0.0.0.0", 8501), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - manual shutdown
        pass

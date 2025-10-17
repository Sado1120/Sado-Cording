from __future__ import annotations

from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class UTF8RequestHandler(SimpleHTTPRequestHandler):
    extensions_map = SimpleHTTPRequestHandler.extensions_map.copy()
    for ext in ["", ".html", ".css", ".js", ".json", ".svg", ".txt"]:
        mime = extensions_map.get(ext, "text/plain")
        if "charset" not in mime and mime.startswith("text"):
            extensions_map[ext] = f"{mime}; charset=utf-8"

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

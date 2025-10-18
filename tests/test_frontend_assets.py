from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import URLError

from frontend import serve
from frontend.serve import DashboardRequestHandler, UTF8RequestHandler, resolve_backend_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_frontend_html_declares_utf8_and_korean_fonts():
    html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "charset=UTF-8" in html, "index.html must explicitly declare UTF-8 charset"
    assert "Noto+Sans+KR" in html, "index.html should load Noto Sans KR to render Hangul"
    required_metrics = {
        "metric-omega",
        "metric-kelly",
        "metric-streak-win",
        "metric-streak-loss",
        "metric-skewness",
        "metric-kurtosis",
        "metric-avg-drawdown",
        "metric-pain",
        "metric-runup",
    }
    for metric_id in required_metrics:
        assert metric_id in html, f"Dashboard should expose advanced metric card {metric_id}"

    assert "paper-heartbeat" in html, "Paper heartbeat indicator should be present in the dashboard"
    assert "copilot-form" in html, "Copilot form should be present to submit AI questions"
    assert "autopilot-bias" in html, "Autopilot badge should be visible for trading plans"
    assert "equity-note" in html, "Equity summary note should guide users through the chart interpretation"
    assert "window.__SADO_API_BASE__" in html, "Dashboard should expose the API base bootstrap script"
    assert "strategy-market" in html, "Strategy form should expose a market selector"
    assert "paper-status-market" in html, "Paper status form should expose a market input"
    assert "chat-test-btn" in html, "Chat webhook test button must be available"
    assert "market-options" in html, "Market datalist should be present for coin selection"
    assert "paper-price-source" in html, "Paper summary should expose a price source indicator"
    assert "chat-status-detail" in html, "Chat status indicator should be rendered"
    assert "market-search" in html, "Market explorer search box must be available"
    assert "market-results" in html, "Market explorer results grid should exist"
    assert "market-groups" in html, "Market explorer group filter container must exist"


def test_stylesheet_contains_korean_font_stack():
    css = (PROJECT_ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

    assert "Noto Sans KR" in css
    assert "Malgun Gothic" in css


def test_utf8_request_handler_sets_html_utf8_content_type():
    handler = UTF8RequestHandler.__new__(UTF8RequestHandler)

    assert handler.guess_type("index.html") == "text/html; charset=utf-8"
    assert handler.guess_type("styles.css") == "text/css; charset=utf-8"
    assert handler.guess_type("app.js") == "application/javascript; charset=utf-8"


def test_resolve_backend_url_maps_api_prefix():
    assert resolve_backend_url("/api/health").endswith("/health")
    assert resolve_backend_url("/api").endswith("/")


def test_dashboard_handler_proxies_api_requests(monkeypatch):
    captured = {}

    class FakeResponse:
        def __init__(self):
            self._headers = {"Content-Type": "application/json"}

        def read(self):
            return b"{\"status\": \"ok\"}"

        def getcode(self):
            return 200

        def getheaders(self):
            return list(self._headers.items())

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout=15):  # noqa: ARG001 - pytest hook
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["data"] = request.data
        return FakeResponse()

    monkeypatch.setattr(serve, "urlopen", fake_urlopen)

    handler = DashboardRequestHandler.__new__(DashboardRequestHandler)
    handler.path = "/api/health"
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /api/health HTTP/1.1"
    handler.client_address = ("127.0.0.1", 0)
    handler.server = None
    handler.headers = Message()
    handler.rfile = BytesIO()
    handler.wfile = BytesIO()

    handler.do_GET()

    assert captured["url"] == f"{serve.BACKEND_URL}/health"
    assert captured["method"] == "GET"
    assert handler.wfile.getvalue().endswith(b"{\"status\": \"ok\"}")


def test_dashboard_handler_returns_json_on_backend_failure(monkeypatch):
    def fake_urlopen(request, timeout=15):  # noqa: ARG001
        raise URLError("down")

    monkeypatch.setattr(serve, "urlopen", fake_urlopen)

    handler = DashboardRequestHandler.__new__(DashboardRequestHandler)
    handler.path = "/api/health"
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /api/health HTTP/1.1"
    handler.client_address = ("127.0.0.1", 0)
    handler.server = None
    handler.headers = Message()
    handler.rfile = BytesIO()
    handler.wfile = BytesIO()

    handler.do_GET()

    payload = handler.wfile.getvalue()
    assert payload.startswith(b"HTTP/1.0 502"), payload
    _, body = payload.split(b"\r\n\r\n", 1)
    assert body.startswith(b"{\"detail\"")

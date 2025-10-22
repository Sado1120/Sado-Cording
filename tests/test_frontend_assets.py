from email.message import Message
import re
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from urllib.error import URLError

import pytest

from frontend import serve
from frontend.serve import DashboardRequestHandler, UTF8RequestHandler, resolve_backend_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_app_js_has_valid_syntax():
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("Node.js is not available for syntax validation")

    result = subprocess.run(
        [node_path, "--check", str(PROJECT_ROOT / "frontend" / "app.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


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
    assert "autopilot-auto-market" in html, "Autopilot form should expose auto-select toggle"
    assert "autopilot-recommendations" in html, "Autopilot status should list AI recommendations"
    assert "autopilot-hold-reason" in html, "Autopilot 관망 사유 표시가 누락되었습니다."
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
    assert "global-nav" in html, "Global navigation bar should be rendered"
    assert "back-to-top" in html, "Back-to-top control should exist for long dashboards"
    assert "topline-autopilot-countdown" in html, "Autopilot countdown indicator should be visible"
    assert "autopilot-next-countdown" in html, "Autopilot status block should show next cycle countdown"


def test_dashboard_portfolio_textareas_have_defaults():
    html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert '"symbol": "BND"' in html, "Stable bucket defaults should include representative ETF"
    assert '"symbol": "BTC"' in html, "Aggressive bucket defaults should include BTC"
    assert '"SPY": 0.35' in html, "Target allocation textarea should pre-fill diversified weights"
    assert '"QQQ": 1500000' in html, "Current positions textarea should surface sample holdings"


def test_capital_inputs_share_same_default_value():
    html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    expected = "20000000"

    selectors = {
        "paper": r'id="paper-initial-cash"[^>]*value="(\d+)"',
        "strategy": r'name="initial_capital"[^>]*value="(\d+)"',
        "ai": r'id="ai-capital"[^>]*value="(\d+)"',
        "autopilot": r'id="autopilot-capital"[^>]*value="(\d+)"',
        "copilot": r'id="copilot-capital"[^>]*value="(\d+)"',
        "blueprint": r'id="blueprint-capital"[^>]*value="(\d+)"',
        "portfolio": r'id="portfolio-value"[^>]*value="(\d+)"',
    }

    for label, pattern in selectors.items():
        match = re.search(pattern, html)
        assert match, f"Expected to find default capital value for {label} input"
        assert (
            match.group(1) == expected
        ), f"Default capital for {label} should be {expected}, got {match.group(1)}"


def test_collapsible_blocks_are_tagged():
    html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    targets = [
        ("class", "recommendations-footnote"),
        ("id", "ai-summary"),
        ("id", "ai-risk-notes"),
        ("id", "ai-briefings"),
        ("id", "copilot-summary"),
        ("id", "alpha-briefing"),
    ]

    for attr, value in targets:
        if attr == "class":
            pattern = rf'<[^>]*class="[^"]*\b{value}\b[^"]*"[^>]*data-collapsible'
        else:
            pattern = rf'<[^>]*{attr}="{value}"[^>]*data-collapsible'
        assert re.search(
            pattern, html
        ), f"Expected {value} block to declare data-collapsible attribute"


def test_stylesheet_contains_korean_font_stack():
    css = (PROJECT_ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

    assert "Noto Sans KR" in css
    assert "Malgun Gothic" in css


def test_utf8_request_handler_sets_html_utf8_content_type():
    handler = UTF8RequestHandler.__new__(UTF8RequestHandler)

    assert handler.guess_type("index.html") == "text/html; charset=utf-8"
    assert handler.guess_type("styles.css") == "text/css; charset=utf-8"
    assert handler.guess_type("app.js") == "application/javascript; charset=utf-8"


def test_resolve_backend_url_maps_api_prefix(monkeypatch):
    monkeypatch.setattr(serve, "BACKEND_URL", "http://example.com")
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
    monkeypatch.setattr(serve, "BACKEND_CANDIDATES", ["http://127.0.0.1:8000"])
    monkeypatch.setattr(serve, "BACKEND_URL", "http://127.0.0.1:8000")

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

    assert captured["url"] == "http://127.0.0.1:8000/health"
    assert captured["method"] == "GET"
    assert handler.wfile.getvalue().endswith(b"{\"status\": \"ok\"}")


def test_dashboard_handler_returns_json_on_backend_failure(monkeypatch):
    def fake_urlopen(request, timeout=15):  # noqa: ARG001
        raise URLError("down")

    monkeypatch.setattr(serve, "urlopen", fake_urlopen)
    monkeypatch.setattr(serve, "BACKEND_CANDIDATES", ["http://127.0.0.1:8000"])

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


def test_dashboard_handler_falls_back_to_localhost(monkeypatch):
    attempts = []

    class FakeResponse:
        def __init__(self, url):
            self._url = url
            self._headers = {"Content-Type": "application/json"}

        def read(self):
            return b"{}"

        def getcode(self):
            return 200

        def getheaders(self):
            return list(self._headers.items())

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout=15):  # noqa: ARG001
        attempts.append(request.full_url)
        if request.full_url.startswith("http://backend:8000"):
            raise URLError("unreachable")
        return FakeResponse(request.full_url)

    monkeypatch.setattr(serve, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        serve,
        "BACKEND_CANDIDATES",
        ["http://backend:8000", "http://127.0.0.1:8000"],
    )
    monkeypatch.setattr(serve, "BACKEND_URL", "http://backend:8000")

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

    assert attempts == ["http://backend:8000/health", "http://127.0.0.1:8000/health"]
    assert handler.wfile.getvalue().endswith(b"{}")


def test_app_js_falls_back_to_default_api_base():
    js = (PROJECT_ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    assert "deriveAlternateBase(base)" in js, "requestApi should derive alternate API candidates when failures occur"
    assert "pickNextApiCandidate(attempted)" in js, "requestApi should iterate through fallback candidates"
    assert "API 연결에 실패했습니다" in js, "User-facing error message must remain in Korean"

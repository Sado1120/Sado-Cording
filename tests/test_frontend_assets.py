from pathlib import Path

from frontend.serve import UTF8RequestHandler


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


def test_stylesheet_contains_korean_font_stack():
    css = (PROJECT_ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

    assert "Noto Sans KR" in css
    assert "Malgun Gothic" in css


def test_utf8_request_handler_sets_html_utf8_content_type():
    handler = UTF8RequestHandler.__new__(UTF8RequestHandler)

    assert handler.guess_type("index.html") == "text/html; charset=utf-8"
    assert handler.guess_type("styles.css") == "text/css; charset=utf-8"
    assert handler.guess_type("app.js") == "application/javascript; charset=utf-8"

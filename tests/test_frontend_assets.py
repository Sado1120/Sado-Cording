from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_frontend_html_declares_utf8_and_korean_fonts():
    html = (PROJECT_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "charset=UTF-8" in html, "index.html must explicitly declare UTF-8 charset"
    assert "Noto+Sans+KR" in html, "index.html should load Noto Sans KR to render Hangul"


def test_stylesheet_contains_korean_font_stack():
    css = (PROJECT_ROOT / "frontend" / "styles.css").read_text(encoding="utf-8")

    assert "Noto Sans KR" in css
    assert "Malgun Gothic" in css

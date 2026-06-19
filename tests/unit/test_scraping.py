"""Unit tests for verity_core.scraping (boundary-mocked, offline)."""
import sys

from verity_core import scraping


def test_importing_scraping_does_not_import_nodriver():
    # Merely importing the module must NOT pull the heavy headless-browser dep.
    assert "nodriver" not in sys.modules


def test_simple_scrape_extracts_and_caps_text(monkeypatch):
    html = (
        b"<html><head><title>T</title><style>.x{}</style>"
        b"<script>x=1</script></head>"
        b"<body><h1>Hello</h1><p>World of news</p></body></html>"
    )

    class _Resp:
        content = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr(scraping.requests, "get", lambda *a, **k: _Resp())
    text = scraping.get_content_simple("http://example.test")
    assert "Hello" in text and "World of news" in text
    # script/style stripped
    assert "x=1" not in text
    assert len(text) <= 8000


def test_simple_scrape_returns_none_on_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(scraping.requests, "get", boom)
    assert scraping.get_content_simple("http://example.test") is None

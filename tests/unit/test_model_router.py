"""Unit tests for verity_core.model_router (requests mocked, offline)."""
import pytest

from verity_core import model_router as mr


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def raise_for_status(self):
        if not self.ok:
            raise mr.requests.exceptions.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


# --- call_gemini (Verity) ---------------------------------------------------
def test_call_gemini_walks_fallback_and_skips_404(monkeypatch):
    monkeypatch.setattr(mr, "PREFERRED_GEMINI_MODELS", ["m404", "m200"])
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "k")
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        if "m404" in url:
            calls.append("m404")
            return _Resp(status_code=404)
        calls.append("m200")
        return _Resp(status_code=200, payload={"candidates": [1]})

    monkeypatch.setattr(mr.requests, "post", fake_post)
    result = mr.call_gemini({"contents": []})
    assert result == {"candidates": [1]}
    assert calls == ["m404", "m200"]  # 404 skipped, next tried


def test_call_gemini_raises_last_error_when_all_fail(monkeypatch):
    monkeypatch.setattr(mr, "PREFERRED_GEMINI_MODELS", ["a", "b"])
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "k")

    def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp(status_code=404)

    monkeypatch.setattr(mr.requests, "post", fake_post)
    with pytest.raises(ValueError):
        mr.call_gemini({"contents": []})


def test_call_gemini_requires_key(monkeypatch):
    monkeypatch.setattr(mr, "GEMINI_API_KEY", None)
    with pytest.raises(ValueError):
        mr.call_gemini({"contents": []})


# --- call_gemini_structured (PhishFinder) -----------------------------------
def test_structured_retries_once_on_503_then_succeeds(monkeypatch):
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "k")
    monkeypatch.setattr(mr.time, "sleep", lambda *_: None)
    seq = [503, 200]

    def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp(status_code=seq.pop(0), payload={"candidates": [{"x": 1}]})

    monkeypatch.setattr(mr.requests, "post", fake_post)
    result = mr.call_gemini_structured({"contents": []}, model_type="flash")
    assert result == {"candidates": [{"x": 1}]}
    assert seq == []  # both attempts consumed


def test_structured_selects_pro_vs_flash(monkeypatch):
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "k")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        return _Resp(status_code=200, payload={"candidates": []})

    monkeypatch.setattr(mr.requests, "post", fake_post)

    mr.call_gemini_structured({}, model_type="pro")
    assert "gemini-1.5-pro-latest" in seen["url"]

    mr.call_gemini_structured({}, model_type="flash")
    assert "gemini-1.5-flash-latest" in seen["url"]

    mr.call_gemini_structured({}, model_name="custom-model")
    assert "custom-model" in seen["url"]


def test_structured_passes_body_through(monkeypatch):
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "k")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["body"] = json
        return _Resp(status_code=200, payload={"candidates": []})

    monkeypatch.setattr(mr.requests, "post", fake_post)
    body = {"generationConfig": {"response_schema": {"type": "object"}}}
    mr.call_gemini_structured(body, model_type="flash")
    assert captured["body"] is body

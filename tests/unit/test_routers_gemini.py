"""Unit tests for verity_core.routers.gemini (call_gemini mocked, offline)."""
from verity_core import model_router as mr
from verity_core.routers.gemini import GeminiRouter


def test_gemini_router_ok(monkeypatch):
    def fake_call(body, headers=None, timeout=60):
        assert body["contents"][0]["parts"][0]["text"] == "hello"
        return {"candidates": [{"content": {"parts": [{"text": "world"}]}}]}
    monkeypatch.setattr(mr, "call_gemini", fake_call)
    out = GeminiRouter().generate("hello")
    assert out["ok"] and out["response"] == "world" and out["provider"] == "gemini"


def test_gemini_router_transport_failure_is_soft(monkeypatch):
    def boom(body, headers=None, timeout=60):
        raise RuntimeError("all models failed")
    monkeypatch.setattr(mr, "call_gemini", boom)
    out = GeminiRouter().generate("x")
    assert out["ok"] is False and "transport failed" in out["error"]


def test_gemini_router_bad_shape_is_soft(monkeypatch):
    monkeypatch.setattr(mr, "call_gemini",
                        lambda body, headers=None, timeout=60: {"candidates": []})
    out = GeminiRouter().generate("x")
    assert out["ok"] is False and "shape" in out["error"]

"""
S3 (approved D5): multimodal router contract, verified EARLY before frame
and audio extraction integrate. Mocked here; the live transport test is
tests/integration/test_gemini_multimodal_live.py (runs when
GEMINI_API_KEY is set — operator-run; see scripts/gemini_multimodal_smoke.py).
"""
import base64

import pytest

from verity_core import model_router as mr
from verity_core.routers import gemini as g
from verity_core.routers.base import CallableRouter, ModelRouter

TINY_PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)


@pytest.fixture
def capture(monkeypatch):
    calls = {}

    def fake(body, headers=None, timeout=60):
        calls["body"] = body
        return {"candidates": [{"content": {"parts":
                                            [{"text": "ok-response"}]}}]}
    monkeypatch.setattr(mr, "call_gemini", fake)
    return calls


def test_base_router_declines_media_honestly():
    r = ModelRouter()
    assert r.supports_media is False
    out = r.generate_multimodal("p", [{"mime": "image/png",
                                       "data": TINY_PNG}])
    assert out["ok"] is False and "does not support media" in out["error"]


def test_callable_router_keeps_text_only_default():
    r = CallableRouter(lambda p: {"ok": True, "response": "x"})
    assert r.supports_media is False    # local runtimes unchanged


def test_gemini_declares_media_support():
    assert g.GeminiRouter.supports_media is True


def test_multimodal_body_shape(capture):
    r = g.GeminiRouter()
    out = r.generate_multimodal(
        "describe the frames",
        [{"mime": "image/png", "data": TINY_PNG},
         {"mime": "audio/mp3", "data": b"\xff\xfb\x90\x00" * 4}])
    assert out["ok"] and out["response"] == "ok-response"
    parts = capture["body"]["contents"][0]["parts"]
    assert parts[0] == {"text": "describe the frames"}
    assert parts[1]["inline_data"]["mime_type"] == "image/png"
    assert base64.b64decode(parts[1]["inline_data"]["data"]) == TINY_PNG
    assert parts[2]["inline_data"]["mime_type"] == "audio/mp3"


def test_multimodal_reads_from_path(capture, tmp_path):
    p = tmp_path / "f.png"
    p.write_bytes(TINY_PNG)
    out = g.GeminiRouter().generate_multimodal(
        "x", [{"mime": "image/png", "path": str(p)}])
    assert out["ok"]
    parts = capture["body"]["contents"][0]["parts"]
    assert base64.b64decode(parts[1]["inline_data"]["data"]) == TINY_PNG


def test_multimodal_payload_budget_fails_fast(monkeypatch):
    called = []
    monkeypatch.setattr(mr, "call_gemini",
                        lambda *a, **k: called.append(1))
    r = g.GeminiRouter()
    big = b"\x00" * (g.MAX_INLINE_BYTES + 1)
    out = r.generate_multimodal("x", [{"mime": "video/mp4", "data": big}])
    assert out["ok"] is False and "inline budget" in out["error"]
    assert not called                      # transport never touched


def test_multimodal_empty_part_rejected(capture):
    out = g.GeminiRouter().generate_multimodal(
        "x", [{"mime": "image/png", "data": b""}])
    assert out["ok"] is False and "empty media part" in out["error"]


def test_multimodal_unreadable_path_fail_soft():
    out = g.GeminiRouter().generate_multimodal(
        "x", [{"mime": "image/png", "path": "/nonexistent/f.png"}])
    assert out["ok"] is False and "could not read" in out["error"]


def test_multimodal_transport_failure_fail_soft(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("socket reset")
    monkeypatch.setattr(mr, "call_gemini", boom)
    out = g.GeminiRouter().generate_multimodal(
        "x", [{"mime": "image/png", "data": TINY_PNG}])
    assert out["ok"] is False and "transport failed" in out["error"]


def test_text_generate_unchanged(capture):
    out = g.GeminiRouter().generate("plain prompt")
    assert out["ok"]
    assert capture["body"]["contents"][0]["parts"] == [
        {"text": "plain prompt"}]

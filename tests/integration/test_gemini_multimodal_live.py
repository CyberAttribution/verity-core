"""
LIVE Gemini multimodal transport test (approved D5: verify the transport
and fallback chain early in S3). Skipped unless GEMINI_API_KEY is set —
run from a credentialed machine:

    GEMINI_API_KEY=... python3 -m pytest tests/integration -q

or use scripts/gemini_multimodal_smoke.py for a human-readable check.
Sends one tiny image and one tiny audio clip; asserts the fallback chain
returns text for both. Costs a fraction of a cent.
"""
import os
import struct
import wave
import io
import zlib

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="GEMINI_API_KEY not set (live transport test is operator-run)")


def _png_bytes():
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + \
            struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes([255, 0, 0] * 8) for _ in range(8))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def _wav_bytes():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x40" * 4000)
    return buf.getvalue()


def test_live_image_part():
    from verity_core.routers.gemini import GeminiRouter
    out = GeminiRouter(timeout=60).generate_multimodal(
        "Reply with one short sentence describing this image's dominant "
        "color.", [{"mime": "image/png", "data": _png_bytes()}])
    assert out["ok"], out.get("error")
    assert out["response"].strip()


def test_live_audio_part():
    from verity_core.routers.gemini import GeminiRouter
    out = GeminiRouter(timeout=60).generate_multimodal(
        "Reply with one short sentence: does this audio contain speech "
        "or a tone?", [{"mime": "audio/wav", "data": _wav_bytes()}])
    assert out["ok"], out.get("error")
    assert out["response"].strip()

#!/usr/bin/env python3
"""
Operator smoke check for the Gemini multimodal transport (S3, approved
D5). Run from a machine where GEMINI_API_KEY is set (never paste the key
anywhere else):

    cd verity-core && GEMINI_API_KEY=... python3 scripts/gemini_multimodal_smoke.py

Sends one tiny generated image and one tiny generated audio clip through
GeminiRouter.generate_multimodal (the same fallback chain production
uses) and prints PASS/FAIL per part. Cost: fractions of a cent.
"""
import io
import os
import struct
import sys
import wave
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if not os.environ.get("GEMINI_API_KEY"):
    print("FAIL: GEMINI_API_KEY is not set in this shell.")
    sys.exit(2)

from verity_core.routers.gemini import GeminiRouter  # noqa: E402


def png_bytes():
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + \
            struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes([0, 0, 255] * 8) for _ in range(8))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def wav_bytes():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x40" * 4000)
    return buf.getvalue()


router = GeminiRouter(timeout=60)
checks = [
    ("image/png part", "One short sentence: what is this image's "
     "dominant color?", {"mime": "image/png", "data": png_bytes()}),
    ("audio/wav part", "One short sentence: tone or speech?",
     {"mime": "audio/wav", "data": wav_bytes()}),
]
failures = 0
for label, prompt, part in checks:
    out = router.generate_multimodal(prompt, [part])
    if out.get("ok") and (out.get("response") or "").strip():
        print(f"PASS  {label}: {out['response'].strip()[:80]!r}")
    else:
        failures += 1
        print(f"FAIL  {label}: {out.get('error')}")
print("\nRESULT:", "PASS — multimodal transport verified"
      if not failures else f"FAIL ({failures} part(s))")
sys.exit(1 if failures else 0)

"""
verity_core.routers.gemini — cloud transport adapter (v0.3.0).

Wraps the existing model_router.call_gemini (preferred-model fallback
chain, moved verbatim in Sprint 1) onto the ModelRouter protocol used by
verity_core.artifacts.pipeline. Fail-soft: transport errors come back as
ok=False so the pipeline can degrade honestly instead of raising.

No ledger exists on the hosted path, so event_id is absent by design —
the pipeline reports those stages as "not recorded".
"""
import base64

from .. import model_router
from .base import ModelRouter

# Gemini inline_data requests are capped by the API (~20MB request size).
# We enforce a conservative payload budget so oversized media fails fast
# with an honest local error instead of an opaque transport 4xx.
MAX_INLINE_BYTES = 15 * 1024 * 1024


class GeminiRouter(ModelRouter):
    supports_media = True

    def __init__(self, timeout: int = 120):
        self.timeout = timeout

    def _call(self, body):
        try:
            data = model_router.call_gemini(body, timeout=self.timeout)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return {"ok": True, "response": text, "provider": "gemini",
                    "raw": data}
        except (KeyError, IndexError, TypeError) as e:
            return {"ok": False, "response": "",
                    "error": f"gemini response shape unexpected: {e}"}
        except Exception as e:
            return {"ok": False, "response": "",
                    "error": f"gemini transport failed: {e}"}

    def generate(self, prompt: str) -> dict:
        return self._call({"contents": [{"parts": [{"text": prompt}]}]})

    def generate_multimodal(self, prompt: str, parts: list) -> dict:
        """parts: [{"mime": "image/jpeg"|"audio/mp3"|..., "data": bytes}
        or {"mime": ..., "path": "/tmp/..."}]. Media rides as inline_data
        (base64) through the same call_gemini fallback chain — no second
        transport, no provider lock-in above the router boundary."""
        content_parts = [{"text": prompt}]
        total = 0
        try:
            for p in parts or []:
                blob = p.get("data")
                if blob is None and p.get("path"):
                    with open(p["path"], "rb") as fh:
                        blob = fh.read()
                if not blob:
                    return {"ok": False, "response": "",
                            "error": "empty media part"}
                total += len(blob)
                if total > MAX_INLINE_BYTES:
                    return {"ok": False, "response": "",
                            "error": f"media payload exceeds the "
                                     f"{MAX_INLINE_BYTES // (1024*1024)}MB "
                                     "inline budget"}
                content_parts.append({"inline_data": {
                    "mime_type": p.get("mime") or
                    "application/octet-stream",
                    "data": base64.b64encode(blob).decode("ascii")}})
        except OSError as e:
            return {"ok": False, "response": "",
                    "error": f"could not read media part: "
                             f"{e.__class__.__name__}"}
        return self._call({"contents": [{"parts": content_parts}]})

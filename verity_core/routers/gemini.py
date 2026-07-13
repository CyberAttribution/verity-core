"""
verity_core.routers.gemini — cloud transport adapter (v0.3.0).

Wraps the existing model_router.call_gemini (preferred-model fallback
chain, moved verbatim in Sprint 1) onto the ModelRouter protocol used by
verity_core.artifacts.pipeline. Fail-soft: transport errors come back as
ok=False so the pipeline can degrade honestly instead of raising.

No ledger exists on the hosted path, so event_id is absent by design —
the pipeline reports those stages as "not recorded".
"""
from .. import model_router
from .base import ModelRouter


class GeminiRouter(ModelRouter):
    def __init__(self, timeout: int = 120):
        self.timeout = timeout

    def generate(self, prompt: str) -> dict:
        body = {"contents": [{"parts": [{"text": prompt}]}]}
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

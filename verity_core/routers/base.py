"""
verity_core.routers.base — minimal model-transport protocol.

The artifacts pipeline depends only on this shape. Adapters:
- routers.openclaw (here): governed local runtime via injected callable
- routers.gemini (b1, in the live verity-core repo): ~15-line wrapper
  around the existing model_router.call_gemini fallback chain.
"""


class ModelRouter:
    """Duck-typed protocol. generate(prompt) -> dict with at least:
    ok: bool, response: str, and optionally event_id, error, model,
    provider. Implementations must not raise on transport failure."""

    def generate(self, prompt: str) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


class CallableRouter(ModelRouter):
    """Wrap any generate_fn(prompt)->dict. Used for OpenClaw (inject the
    Node UI's governed client) and for deterministic test mocks."""

    def __init__(self, generate_fn):
        self._fn = generate_fn

    def generate(self, prompt: str) -> dict:
        try:
            out = self._fn(prompt)
            return out if isinstance(out, dict) else \
                {"ok": False, "error": "router returned non-dict"}
        except Exception as e:  # fail soft — pipeline records the failure
            return {"ok": False, "error": f"router exception: {e}",
                    "response": ""}

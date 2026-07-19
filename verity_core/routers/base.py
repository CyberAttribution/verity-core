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
    provider. Implementations must not raise on transport failure.

    v0.5.0 additive — media capability (approved D1/D5): routers that can
    accept image/audio parts set supports_media = True and implement
    generate_multimodal. The default honestly declines, so pipelines
    running on text-only runtimes (local 7B via OpenClaw, older cores)
    record 'signal-level review not performed by this runtime' instead of
    failing — no provider is ever hard-coded."""

    supports_media = False

    def generate(self, prompt: str) -> dict:  # pragma: no cover - interface
        raise NotImplementedError

    def generate_multimodal(self, prompt: str, parts: list) -> dict:
        """parts: [{"mime": str, "data": bytes} or {"mime": str,
        "path": str}]. Default: declared unsupported (fail-soft)."""
        return {"ok": False, "response": "",
                "error": "this runtime does not support media input"}


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

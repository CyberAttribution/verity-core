"""
verity_core.routers.vertex — Vertex AI transport adapter (v0.4.0).

Wraps model_router.call_vertex onto the ModelRouter protocol. Same
generateContent body contract as the AI Studio path, so the artifacts
pipeline is unchanged above the router boundary. Authentication is
Application Default Credentials (no api key). Fail-soft: transport
errors come back as ok=False with sanitized attempts + latency, never a
raised exception whose string could carry a credential.
"""
import base64

from .. import model_router
from .base import ModelRouter

MAX_INLINE_BYTES = 15 * 1024 * 1024


def _multimodal_body(prompt, parts, system=None):
    """Build a valid Vertex generateContent body carrying prompt + inline
    media on a single `role: user` message (multimodal parts stay
    attached to that user turn). System text, if any, goes to
    systemInstruction via the shared normalizer. Returns (body, error)."""
    media_parts = []
    total = 0
    for p in parts or []:
        blob = p.get("data")
        if blob is None and p.get("path"):
            with open(p["path"], "rb") as fh:
                blob = fh.read()
        if not blob:
            return None, "empty media part"
        total += len(blob)
        if total > MAX_INLINE_BYTES:
            return None, (f"media payload exceeds the "
                          f"{MAX_INLINE_BYTES // (1024*1024)}MB inline "
                          "budget")
        media_parts.append({"inline_data": {
            "mime_type": p.get("mime") or "application/octet-stream",
            "data": base64.b64encode(blob).decode("ascii")}})
    body = model_router.normalize_vertex_body(
        prompt=prompt, parts=media_parts, system=system)
    return body, None


class VertexRouter(ModelRouter):
    supports_media = True
    provider = "vertex"

    def __init__(self, project, location, models, timeout=120):
        self.project = project
        self.location = location
        self.models = list(models or [])
        self.timeout = timeout

    def _meta(self, extra=None):
        d = {"provider": "vertex", "project": self.project,
             "region": self.location,
             "attempted_models": list(self.models)}
        if extra:
            d.update(extra)
        return d

    def _call(self, body):
        try:
            data = model_router.call_vertex(
                body, self.models, self.project, self.location,
                timeout=self.timeout)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return {"ok": True, "response": text,
                    **self._meta({"model_used": data.get("_model_used"),
                                  "latency_ms": data.get("_latency_ms"),
                                  "attempts": data.get("_attempts") or []}),
                    "raw": data}
        except model_router.ProviderError as e:
            return {"ok": False, "response": "",
                    "error": model_router.sanitize_provider_text(str(e)),
                    "provider_status": e.status,
                    "attempts": e.attempts,
                    **self._meta({"attempted_models":
                                  e.attempted_models or list(self.models)})}
        except (KeyError, IndexError, TypeError) as e:
            return {"ok": False, "response": "",
                    "error": f"vertex response shape unexpected: {e}",
                    **self._meta()}
        except Exception as e:
            return {"ok": False, "response": "",
                    "error": model_router.sanitize_provider_text(
                        f"vertex transport failed: {e.__class__.__name__}"),
                    **self._meta()}

    def generate(self, prompt: str) -> dict:
        # text-only turn -> a single role:user message (valid Vertex body)
        return self._call(
            model_router.normalize_vertex_body(prompt=prompt))

    def generate_multimodal(self, prompt: str, parts: list) -> dict:
        body, err = _multimodal_body(prompt, parts)
        if err:
            return {"ok": False, "response": "", "error": err,
                    **self._meta()}
        return self._call(body)

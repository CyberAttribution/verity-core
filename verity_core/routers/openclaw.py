"""
verity_core.routers.openclaw — governed local runtime adapter.

Thin by design: the Node UI already owns a governed OpenClaw client
(Keel policy lock, loopback-only, ledger event ids). This adapter maps
that client's response shape onto the ModelRouter protocol without
importing Node UI code (the callable is injected at the boundary).

Usage in SwarmTracker Node:
    from verity_core.routers.openclaw import OpenClawRouter
    router = OpenClawRouter(lambda p: oc.generate(p))   # oc = node client
"""
from .base import CallableRouter


class OpenClawRouter(CallableRouter):
    def generate(self, prompt: str) -> dict:
        out = super().generate(prompt)
        # Normalize the Node client's field names onto the protocol.
        if "event_id" not in out and "openclaw_event_id" in out:
            out["event_id"] = out.get("openclaw_event_id")
        out.setdefault("ok", bool(out.get("response")))
        out.setdefault("response", "")
        return out

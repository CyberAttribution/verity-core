"""
providers — pluggable governed-cloud provider registry (v0.4.0).

The Media Evidence pipeline never talks to a provider directly: it asks
this module for a router. Today the active provider is Vertex AI; adding
another (or a local vision runtime) is a new entry here, not a change to
media_runner / app / UI.

Order of governed inference:  Local (default)  ->  Vertex AI  ->  future.

Nothing here holds credentials. Vertex uses Application Default
Credentials via the transport layer; readiness is a capability check.
"""
import os

from . import core_bridge

# ---- Vertex configuration (env-overridable; model IDs verified against
#      Vertex Model Garden — see INSTALL). Cyber is optional/unverified:
#      offered only if explicitly enabled with a confirmed id. ----
DEFAULT_PROVIDER = os.environ.get("NODE_CLOUD_PROVIDER", "vertex")

VERTEX_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
# Default to the `global` endpoint: Gemini 3.x models are served from
# `global`, not from regional hosts (a regional call 404s "model not
# found" even with correct auth). Operators can pin a region via
# GOOGLE_CLOUD_LOCATION / VERTEX_LOCATION for regional-only models.
VERTEX_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION",
                                 os.environ.get("VERTEX_LOCATION",
                                                "global"))

_MODEL_PRIMARY = os.environ.get("VERTEX_MODEL_PRIMARY", "gemini-3.6-flash")
_MODEL_FALLBACK = os.environ.get("VERTEX_MODEL_FALLBACK",
                                 "gemini-3.5-flash-lite")
# Optional partner model. Unset by default (not a published GA id in
# public docs at build time); operators set the confirmed id to enable.
_MODEL_CYBER = os.environ.get("VERTEX_MODEL_CYBER", "").strip()

_LABELS = {
    _MODEL_PRIMARY: "Gemini 3.6 Flash",
    _MODEL_FALLBACK: "Gemini 3.5 Flash Lite",
}
if _MODEL_CYBER:
    _LABELS[_MODEL_CYBER] = "Gemini 3.5 Flash Cyber"


def model_label(model_id):
    return _LABELS.get(model_id, model_id or "—")


def selectable_models():
    """[(id, label)] offered in the UI. Primary first; cyber last if
    enabled."""
    out = [(_MODEL_PRIMARY, _LABELS[_MODEL_PRIMARY]),
           (_MODEL_FALLBACK, _LABELS[_MODEL_FALLBACK])]
    if _MODEL_CYBER:
        out.append((_MODEL_CYBER, _LABELS[_MODEL_CYBER]))
    return out


def _vertex_chain(selected_model=None):
    """Ordered model list to try. A valid explicit selection goes first,
    then the standard primary->fallback chain (deduped)."""
    chain = []
    valid = {m for m, _ in selectable_models()}
    if selected_model and selected_model in valid:
        chain.append(selected_model)
    for m in (_MODEL_PRIMARY, _MODEL_FALLBACK):
        if m and m not in chain:
            chain.append(m)
    return chain


def provider_config():
    """Display + readiness for the active provider. `ready` means an
    escalation could actually run (library present, project set, ADC
    resolvable is checked lazily at call time)."""
    if DEFAULT_PROVIDER != "vertex":
        return {"provider": DEFAULT_PROVIDER, "name": DEFAULT_PROVIDER,
                "ready": False, "reason": "unknown provider configured",
                "project": "", "region": "", "models": [],
                "default_model_id": "", "default_model_label": ""}
    adc = core_bridge.HAS_VERTEX_ROUTER and \
        core_bridge.model_router.adc_available()
    ready = bool(adc and VERTEX_PROJECT)
    if not core_bridge.HAS_VERTEX_ROUTER:
        reason = "Vertex router not present in this build"
    elif not core_bridge.model_router.adc_available():
        reason = ("google-auth not installed / ADC not configured — run "
                  "`gcloud auth application-default login`")
    elif not VERTEX_PROJECT:
        reason = "GOOGLE_CLOUD_PROJECT not set"
    else:
        reason = ("ready — Application Default Credentials; no API key")
    return {
        "provider": "vertex", "name": "Vertex AI",
        "ready": ready, "reason": reason,
        "project": VERTEX_PROJECT, "region": VERTEX_LOCATION,
        "models": selectable_models(),
        "default_model_id": _MODEL_PRIMARY,
        "default_model_label": _LABELS[_MODEL_PRIMARY],
    }


def list_available_models():
    """Diagnostic passthrough: exact publisher model IDs available to the
    configured project/location. Ground truth for setting VERTEX_MODEL_*
    without guessing."""
    if not core_bridge.HAS_VERTEX_ROUTER:
        return {"ok": False, "error": "Vertex router not present",
                "models": []}
    return core_bridge.model_router.list_vertex_models(
        VERTEX_PROJECT, VERTEX_LOCATION)


def build_cloud_router(selected_model=None):
    """(router | None, reason). The router carries the ordered model
    chain; the transport tries them and reports which was used."""
    cfg = provider_config()
    if cfg["provider"] != "vertex":
        return None, "no supported cloud provider configured"
    if not core_bridge.HAS_VERTEX_ROUTER:
        return None, "Vertex router not present in this build"
    if not VERTEX_PROJECT:
        return None, "GOOGLE_CLOUD_PROJECT not set"
    if not core_bridge.model_router.adc_available():
        return None, ("google-auth/ADC unavailable — run "
                      "`gcloud auth application-default login`")
    chain = _vertex_chain(selected_model)
    return core_bridge.VertexRouter(VERTEX_PROJECT, VERTEX_LOCATION,
                                    chain), ""

"""
core_bridge — SwarmTracker Node's boundary to the shared Verity Core
artifacts layer (v0.1.5 a2).

Import order: a properly installed `verity_core` package (the private
CyberAttribution/verity-core repo, once the staged layer is merged) wins;
otherwise the vendored `verity_core_staging` copy deployed beside this
package is used. The Node UI never talks to the pipeline except through
this module, so swapping to the installed package later is a no-op here.
"""
import sys
from pathlib import Path

# Ensure the deploy root (parent of this package) is importable when the
# vendored staging copy sits beside swarmtracker_node_ui/.
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # merged/installed real package first
    from verity_core.artifacts import normalize, pipeline  # type: ignore
    from verity_core.routers import OpenClawRouter  # type: ignore
    CORE_SOURCE = "verity_core (installed package)"
except ImportError:
    from verity_core_staging.artifacts import normalize, pipeline
    from verity_core_staging.routers import OpenClawRouter
    CORE_SOURCE = "verity_core_staging (vendored, pending merge)"

# The media_ingest package arrived with the 0.5.0 core (import-guarded so
# a partial deployment degrades honestly instead of crashing the app).
try:
    from verity_core.media_ingest import (audio, exif, frames,  # type: ignore
                                          package as media_package, probe,
                                          remote as media_remote,
                                          tools as media_tools)
    from verity_core.artifacts import media_assessment  # type: ignore
    HAS_MEDIA_INGEST = True
except ImportError:
    try:
        from verity_core_staging.media_ingest import (  # noqa: F401
            audio, exif, frames, package as media_package, probe,
            remote as media_remote, tools as media_tools)
        from verity_core_staging.artifacts import media_assessment
        HAS_MEDIA_INGEST = True
    except ImportError:
        HAS_MEDIA_INGEST = False

# Optional Gemini (AI Studio) transport — retained for back-compat.
try:
    from verity_core.routers.gemini import GeminiRouter  # type: ignore
    HAS_GEMINI_ROUTER = True
except ImportError:
    try:
        from verity_core_staging.routers.gemini import GeminiRouter
        HAS_GEMINI_ROUTER = True
    except ImportError:
        GeminiRouter = None
        HAS_GEMINI_ROUTER = False

# Vertex AI transport (v0.4.0 primary governed provider) + the shared
# model_router (for ADC availability checks).
try:
    from verity_core.routers.vertex import VertexRouter  # type: ignore
    from verity_core import model_router  # type: ignore
    HAS_VERTEX_ROUTER = True
except ImportError:
    try:
        from verity_core_staging.routers.vertex import VertexRouter
        from verity_core_staging import model_router
        HAS_VERTEX_ROUTER = True
    except ImportError:
        VertexRouter = None
        model_router = None
        HAS_VERTEX_ROUTER = False


class EscalatingRouter(OpenClawRouter):
    """Hybrid router for Keel-governed multimodal escalation.

    Text stages (artifact analysis without media, fusion) run on the
    LOCAL governed runtime exactly as in Local Only mode. Only
    `generate_multimodal` — the single call that carries the extracted
    artifacts — goes to the authorized cloud provider. The pipeline
    already fail-softs: if the escalated call fails, it degrades to the
    local text path and the report says so honestly.

    `multimodal_ok` records whether the escalated call actually
    succeeded, so callers never claim media was examined when it
    was not."""

    supports_media = True

    def __init__(self, generate_fn, cloud_router):
        super().__init__(generate_fn)
        self._cloud = cloud_router
        self.provider_attempted = False
        self.multimodal_ok = False
        self.multimodal_error = None
        self.multimodal_attempts = []
        self.multimodal_status = None
        self.attempted_models = []
        self.provider = getattr(cloud_router, "provider", "cloud")
        self.model_used = None
        self.latency_ms = None
        self.project = getattr(cloud_router, "project", None)
        self.region = getattr(cloud_router, "location", None)

    def generate_multimodal(self, prompt: str, parts: list) -> dict:
        self.provider_attempted = True  # SDK invocation, not proof of delivery
        try:
            out = self._cloud.generate_multimodal(prompt, parts) or {}
        except Exception as e:   # never raise into the pipeline
            out = {"ok": False, "response": "",
                   "error": f"escalation transport: {e.__class__.__name__}"}
        out.setdefault("provider", self.provider)
        self.multimodal_ok = bool(out.get("ok"))
        self.multimodal_error = out.get("error")
        self.multimodal_attempts = out.get("attempts") or []
        self.multimodal_status = out.get("provider_status")
        self.attempted_models = out.get("attempted_models") or []
        self.provider = out.get("provider") or self.provider
        self.model_used = out.get("model_used")
        self.latency_ms = out.get("latency_ms")
        self.project = out.get("project") or self.project
        self.region = out.get("region") or self.region
        return out

MAX_ARTIFACTS = pipeline.MAX_ARTIFACTS


def build_artifacts(items: list) -> list:
    """Form/JSON dicts -> EvidenceArtifact list (empty-text items dropped)."""
    arts = []
    for i in items[:MAX_ARTIFACTS]:
        if not isinstance(i, dict) or not str(i.get("text", "")).strip():
            continue
        arts.append(normalize.make_artifact(
            str(i.get("text", "")),
            label=str(i.get("label", ""))[:80],
            evidence_type=str(i.get("evidence_type", "freeform_text")),
            event_category=str(i.get("event_category", "")),
            claimed_sender=str(i.get("claimed_sender", ""))[:120],
            claimed_org=str(i.get("claimed_org", ""))[:120],
            requested_action=str(i.get("requested_action", ""))[:200],
            source=str(i.get("source", "manual")),
        ))
    return arts


def run_fused(items: list, profile_mode: str, generate_fn,
              status_cb=None, workflow="general_evidence",
              case_context="", attachments=None):
    """Run the shared two-stage pipeline through the governed local runtime.

    generate_fn is the Node's Keel-locked OpenClaw client call; every stage
    therefore writes a real ledger event and the report carries the ids.
    `workflow` selects specialized prompt behavior (e.g. media_authenticity).

    v0.3.0 (0.5.0 core): `case_context` carries investigation framing into
    the prompts; `attachments` ({artifact_id: [parts]}) rides the router
    abstraction — on the text-only local runtime the 0.5.0 base router
    declines media honestly, so passing attachments is always safe.
    """
    artifacts = build_artifacts(items)
    router = OpenClawRouter(generate_fn)
    return pipeline.analyze_artifacts(artifacts, router,
                                      profile_mode=profile_mode,
                                      status_cb=status_cb,
                                      case_context=case_context,
                                      workflow=workflow,
                                      attachments=attachments)


def run_media(digest_text: str, generate_fn, *, label="Media evidence",
              case_context="", parts=None, status_cb=None,
              profile_mode="smb", router=None):
    """Media-workflow pipeline run: one evidence-digest artifact, with the
    extracted media parts riding the router abstraction as attachments.

    Returns (report, router) — the router so callers can read
    `supports_media` and set has_frames/has_audio honestly (on the local
    text-only runtime the parts are declined and never reach the model).
    Pass `router` (e.g. EscalatingRouter) for governed multimodal
    escalation; default is the Local Only OpenClawRouter.
    """
    art = normalize.make_artifact(digest_text, label=label,
                                  evidence_type="media_evidence_package",
                                  source="media_runner")
    router = router or OpenClawRouter(generate_fn)
    attachments = {art.id: list(parts or [])} if parts else None
    report = pipeline.analyze_artifacts([art], router,
                                        profile_mode=profile_mode,
                                        status_cb=status_cb,
                                        case_context=case_context,
                                        workflow="media_authenticity",
                                        attachments=attachments)
    return report, router

"""
verity_core.artifacts.pipeline — two-stage analytical orchestration.

Stage 1: one structured analysis per artifact (parallel-safe, sequential
here). Stage 2: deterministic correlation + one model fusion pass over the
structured outputs. Two stages by design: a 7B local model cannot reliably
emit one giant fused JSON over many raw artifacts, and the same
orchestration runs unchanged on larger cloud models.

The router is injected (see routers.base) so this module knows nothing
about OpenClaw, Ollama, or Gemini. Every router call may carry a governed
event id; the pipeline preserves them for the Keel trace.
"""
import json

from . import fuse, normalize, parse, prompts
from .schemas import FusedReport

MAX_ARTIFACTS = 6

_LIMITATIONS_BASE = [
    "Correlation and campaign clustering are within this submission only; "
    "longitudinal campaign tracking requires an evidence store (Origin).",
    "No organization profile, mailbox access, or live network lookups were "
    "used; findings derive from submitted evidence text alone.",
    "Attribution hypotheses describe campaign types and infrastructure "
    "patterns with explicit uncertainty; no actor naming is attempted.",
]


def analyze_artifacts(artifacts: list, router, profile_mode: str = "smb",
                      status_cb=None, case_context: str = "") -> FusedReport:
    """Run the full pipeline. Never raises on model/parse failures —
    degradation is recorded honestly in the report."""
    def status(msg):
        if status_cb:
            try:
                status_cb(msg)
            except Exception:
                pass

    artifacts = list(artifacts)[:MAX_ARTIFACTS]
    limitations = list(_LIMITATIONS_BASE)
    if not artifacts:
        rpt = FusedReport(profile_mode=profile_mode,
                          fused_verdict="No artifacts submitted.",
                          limitations=limitations)
        return rpt

    # ---- Stage 1: per-artifact structured analysis ----
    analyses = []
    for i, art in enumerate(artifacts, 1):
        status(f"Analyzing artifact {i}/{len(artifacts)}: {art.label}")
        indicators = normalize.extract_indicators(art.text,
                                                  art.claimed_sender)
        result = router.generate(prompts.build_artifact_prompt(
            art, profile_mode)) or {}
        analyses.append(parse.parse_artifact_analysis(
            result.get("response") or "",
            artifact_id=art.id,
            indicators=indicators,
            event_id=str(result.get("event_id") or ""),
        ))
        if not result.get("ok", True):
            limitations.append(
                f"Artifact {art.id} analysis failed at the runtime "
                f"({result.get('error', 'unknown error')}); deterministic "
                "indicators were still extracted.")

    # ---- Stage 2a: deterministic correlation ----
    status("Correlating artifacts (deterministic)")
    campaigns = fuse.cluster_campaigns(analyses)
    entities, relationships = fuse.build_graph(artifacts, analyses,
                                               campaigns)
    corr = fuse.correlation_summary(analyses, campaigns)

    # ---- Stage 2b: model fusion pass ----
    status("Fusing assessment")
    compact = [{
        "artifact_id": a.artifact_id, "risk_level": a.risk_level,
        "confidence": a.confidence, "verdict": a.verdict,
        "signals": a.signals[:6],
        "indicators": a.indicators.to_dict(),
    } for a in analyses]
    fusion_result = router.generate(prompts.build_fusion_prompt(
        json.dumps(compact, indent=1),
        json.dumps(corr, indent=1),
        len(analyses), profile_mode,
        case_context=case_context)) or {}
    fusion = parse.parse_fusion(fusion_result.get("response") or "")
    if not fusion_result.get("ok", True):
        limitations.append(
            "Fusion pass failed at the runtime; report contains "
            "deterministic correlation only.")

    overall_conf = fuse.aggregate_confidence(
        analyses, fusion["overall_confidence"], fusion["parsed"])

    return FusedReport(
        profile_mode=profile_mode,
        overall_risk=fusion["overall_risk"] if fusion["parsed"] else
        _worst_risk(analyses),
        overall_confidence=overall_conf,
        fused_verdict=fusion["fused_verdict"],
        corroborations=fusion["corroborations"],
        contradictions=fusion["contradictions"],
        missing_evidence=fusion["missing_evidence"],
        recommendations=fusion["recommendations"],
        attribution=fusion["attribution"],
        campaigns=campaigns,
        entities=entities,
        relationships=relationships,
        artifact_analyses=analyses,
        limitations=limitations,
        fusion_event_id=str(fusion_result.get("event_id") or ""),
        fusion_parsed=fusion["parsed"],
    )


def _worst_risk(analyses: list) -> str:
    order = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    worst = max((a.risk_level for a in analyses),
                key=lambda r: order.get(r, 0), default="unknown")
    return worst

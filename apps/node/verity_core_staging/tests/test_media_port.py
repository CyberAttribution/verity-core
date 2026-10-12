"""
Regression tests for the 0.5.0 media port into the vendored staging core.

Locks the two guarantees the YC-sprint handoff flags as non-negotiable:
1. `case_context` AND `workflow` coexist on the pipeline (the historic
   staging/live divergence must never be reintroduced).
2. Text-only routers degrade honestly: attachments never crash the
   pipeline, and anti-fabrication strips visual/audio findings the model
   could not have made.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from verity_core_staging.artifacts import (media_assessment, normalize,  # noqa: E402
                                           pipeline, prompts)
from verity_core_staging.media_ingest import package  # noqa: E402
from verity_core_staging.routers.base import CallableRouter, ModelRouter  # noqa: E402

ARTIFACT_OUT = json.dumps({
    "risk_level": "medium", "confidence": 50, "verdict": "v",
    "signals": [], "recommended_action": "r"})
FUSION_OUT = json.dumps({
    "fused_verdict": "f", "overall_risk": "medium",
    "overall_confidence": 55, "attribution_hypotheses": []})


def make_capturing_router():
    seen = []

    def gen(prompt):
        seen.append(prompt)
        out = FUSION_OUT if "fusion engine" in prompt else ARTIFACT_OUT
        return {"ok": True, "response": out, "event_id": f"evt-{len(seen)}"}
    return CallableRouter(gen), seen


# ---- 1. case_context + workflow union (divergence regression) ----

def test_case_context_and_workflow_coexist():
    arts = [normalize.make_artifact("evidence text", label="A")]
    router, seen = make_capturing_router()
    rpt = pipeline.analyze_artifacts(
        arts, router, profile_mode="smb",
        case_context="CASE-CONTEXT-SENTINEL-9Z",
        workflow="media_authenticity")
    assert rpt is not None
    assert any("CASE-CONTEXT-SENTINEL-9Z" in p for p in seen), \
        "case_context must reach the model prompts"


def test_fusion_prompt_accepts_case_context():
    p = prompts.build_fusion_prompt("[]", "{}", 1, "smb",
                                    case_context="SENTINEL-CTX")
    assert "SENTINEL-CTX" in p


# ---- 2. honest degradation on a text-only router ----

def test_base_router_declines_media_honestly():
    r = ModelRouter()
    assert r.supports_media is False
    out = r.generate_multimodal("p", [{"mime": "image/jpeg", "data": b"x"}])
    assert out["ok"] is False and "does not support" in out["error"]


def test_attachments_do_not_crash_text_only_pipeline():
    arts = [normalize.make_artifact("media evidence digest text",
                                    label="Media")]
    router, seen = make_capturing_router()
    atts = {arts[0].id: [{"mime": "image/jpeg", "data": b"\xff\xd8fake"}]}
    rpt = pipeline.analyze_artifacts(arts, router, attachments=atts,
                                     workflow="media_authenticity")
    assert rpt is not None and len(rpt.artifact_analyses) == 1
    assert len(seen) >= 1          # text path was used, no crash


def test_anti_fabrication_strips_unexamined_findings():
    ma = media_assessment.normalize(
        {"assessment_conclusion": "likely_authentic",
         "visual_findings": {"frame_observations": ["fabricated claim"]},
         "audio_findings": {"transcript": "fabricated transcript"}},
        has_frames=False, has_audio=False)
    assert ma["visual_findings"]["status"] == "no_visual_evidence"
    assert ma["visual_findings"]["frame_observations"] == []
    assert ma["audio_findings"]["status"] == "no_audio_evidence"
    assert ma["audio_findings"]["transcript"] == ""


def test_fallback_assessment_is_marked():
    ma = media_assessment.fallback_assessment(reason="runtime down")
    assert ma.get("fallback") is True


# ---- 3. evidence package lifecycle (stdlib-only, no media tools) ----

def test_package_lifecycle_and_validate():
    pkg = package.new_package("upload", "specimen.mp4")
    aid = package.add_artifact(pkg, "source_file", sha256="a" * 64,
                               size_bytes=10)
    package.record_event(pkg, "intake", "ok", outputs=[aid])
    assert package.validate(pkg) == []
    digest = package.evidence_digest(pkg)
    assert isinstance(digest, str) and digest
    rec = package.derive_recommended_corroboration(pkg)
    assert isinstance(rec, list)
    package.finalize(pkg, "deleted", deleted_artifact_ids=[aid])
    assert package.validate(pkg) == []
    # broken parent ref must be caught
    package.add_artifact(pkg, "frame", sha256="b" * 64,
                         parent="art-missing")
    assert package.validate(pkg)

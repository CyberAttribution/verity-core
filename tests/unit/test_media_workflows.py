"""v0.4.0: media evidence bundle + workflow registry + workflow-param prompts."""
import json
from verity_core.artifacts import media, workflows, prompts, pipeline, normalize
from verity_core.routers.base import CallableRouter


def test_media_bundle_roundtrip_and_summary():
    b = media.MediaEvidenceBundle.from_dict({
        "transcript": "hello",
        "media_url": {"kind": "url", "value": "https://x.example/p"},
        "claimed_place": "NYC"})
    s = media.summarize_bundle(b)
    assert s["has_transcript"] and s["has_url"] and not s["has_frames"]
    assert s["claimed_place"] == "NYC"
    b2 = media.MediaEvidenceBundle.from_dict(b.to_dict())
    assert b2.transcript == "hello" and b2.media_url.value.endswith("/p")


def test_workflow_registry_declares_available_vs_future():
    wf = workflows.get("media_authenticity")
    caps = {c.key for c in wf.available_capabilities()}
    assert "transcripts" in caps and "incentives" in caps
    assert "visual_forensics" not in caps      # declared future, not available
    assert wf.display_name == "Media Evidence"
    assert workflows.get("nonsense").id == "general_evidence"


def test_media_workflow_prompt_extension():
    a = normalize.make_artifact("transcript", label="Clip",
                                evidence_type="media_bundle")
    p = prompts.build_artifact_prompt(a, "smb", workflow="media_authenticity")
    assert '"media_assessment"' in p and "pending an approved vision runtime" in p
    assert '"media_assessment"' not in prompts.build_artifact_prompt(a, "smb")
    f = prompts.build_fusion_prompt("[]", "{}", 1, "smb",
                                    workflow="media_authenticity")
    assert "never imply it ran" in f and "consistent with" in f


def test_case_context_still_flows():   # regression: case_context preserved
    f = prompts.build_fusion_prompt("[]", "{}", 1, "smb",
                                    case_context="Ticket 4411")
    assert "Ticket 4411" in f and "untrusted operator input" in f


def test_pipeline_threads_workflow():
    a = normalize.make_artifact("clip transcript", label="A",
                                evidence_type="media_bundle")
    seen = {}

    def gen(prompt):
        if "fusion engine" in prompt:
            seen["fusion"] = prompt
        else:
            seen["artifact"] = prompt
        return {"ok": True, "response": json.dumps(
            {"risk_level": "medium", "confidence": 40, "verdict": "v",
             "recommended_action": "r"})}
    pipeline.analyze_artifacts([a], CallableRouter(gen),
                               workflow="media_authenticity")
    assert '"media_assessment"' in seen["artifact"]
    assert "never imply it ran" in seen["fusion"]

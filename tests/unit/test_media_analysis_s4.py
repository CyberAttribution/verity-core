"""
S4 core tests: graded conclusions, confidence-by-domain, anti-fabrication
normalization, fallback assessment, deterministic recommended
corroboration, evidence digest, and pipeline attachments plumbing behind
the router abstraction. Offline; routers are fakes.
"""
import json

import pytest

from verity_core.artifacts import media_assessment as mass
from verity_core.artifacts import normalize as vnorm
from verity_core.artifacts import pipeline, prompts
from verity_core.media_ingest import package


# ---------------- graded conclusions + domains ----------------
def test_conclusion_vocabulary_clamped():
    assert mass.normalize_conclusion("suspicious") == "suspicious"
    assert mass.normalize_conclusion("FAKE") == "inconclusive"
    assert mass.normalize_conclusion("real") == "inconclusive"
    assert mass.normalize_conclusion(None) == "inconclusive"
    for c in prompts.MEDIA_CONCLUSIONS:
        assert mass.normalize_conclusion(c) == c


def test_domains_forced_to_stable_shape():
    out = mass.normalize_domains({"technical": {"confidence": 250,
                                                "basis": "x"},
                                  "bogus_domain": {"confidence": 50}})
    assert set(out) == set(prompts.MEDIA_DOMAINS)
    assert out["technical"]["confidence"] == 100
    assert out["provenance"] == {"confidence": 0, "basis": "not assessed"}
    assert "bogus_domain" not in out


def test_normalize_full_assessment_epistemic_shape():
    ma = mass.normalize({
        "assessment_conclusion": "suspicious",
        "conclusion_rationale": "r",
        "visual_findings": {
            "frame_observations": [
                {"observation": "text overlay reads URGENT",
                 "interpretation": "possible added caption",
                 "competing_explanations": ["platform captioning"]},
                "plain string observation"],
            "visible_text": ["URGENT"]},
        "audio_findings": {"transcript": "hello",
                           "speaker_observations": []},
        "recommended_corroboration": ["original file",
                                      {"item": "weather records",
                                       "rationale": "verify claimed "
                                                    "storm", "bearing":
                                       "claimed-context hypothesis"}],
    }, has_frames=True, has_audio=True)
    assert ma["assessment_conclusion"] == "suspicious"
    obs = ma["visual_findings"]["frame_observations"]
    assert obs[0]["competing_explanations"] == ["platform captioning"]
    assert obs[1]["observation"] == "plain string observation"
    assert "not validated pixel-level" in ma["visual_findings"][
        "limitations"]
    assert "no biometric" in ma["audio_findings"]["limitations"]
    assert ma["recommended_corroboration"][0]["item"] == "original file"
    assert ma["recommended_corroboration"][1]["bearing"]


def test_anti_fabrication_strips_unattached_modalities():
    ma = mass.normalize({
        "visual_findings": {"status": "reviewed", "frame_observations":
                            [{"observation": "a face is visible"}]},
        "audio_findings": {"status": "reviewed",
                           "transcript": "fabricated words"},
    }, has_frames=False, has_audio=False)
    assert ma["visual_findings"]["status"] == "no_visual_evidence"
    assert ma["visual_findings"]["frame_observations"] == []
    assert ma["audio_findings"]["transcript"] == ""
    assert "not performed" in ma["audio_findings"]["limitations"]


def test_fallback_assessment_honest():
    pkg = package.new_package("upload", "x.mp4")
    package.add_artifact(pkg, "source_media", sha256="a" * 64,
                         size_bytes=1, step="intake")
    package.derive_recommended_corroboration(pkg)
    ma = mass.fallback_assessment(pkg, reason="runtime unreachable")
    assert ma["fallback"] is True
    assert ma["assessment_conclusion"] == "inconclusive"
    assert "did not run" in ma["conclusion_rationale"]
    assert all(v["confidence"] == 0
               for v in ma["confidence_by_domain"].values())
    assert ma["recommended_corroboration"]      # carried from the package
    empty = mass.fallback_assessment({}, reason="x")
    assert empty["assessment_conclusion"] == "insufficient_media_evidence"


# ---------------- deterministic recommended corroboration ----------------
def test_corroboration_guidance_covers_gaps():
    pkg = package.new_package("youtube", "https://youtu.be/x",
                              declared={"claimed_date": "2026-07-05",
                                        "claimed_place": "Harbor"})
    recs = package.derive_recommended_corroboration(pkg)
    items = " | ".join(r["item"] for r in recs)
    assert "original camera/source file" in items       # youtube re-encode
    assert "Earlier or alternate uploads" in items
    assert "capture/upload time" in items               # no embedded ts
    assert "Authoritative records" in items             # claimed date/place
    assert all(r["origin"] == "deterministic" and r["bearing"]
               for r in recs)
    # guidance, not conclusions
    assert not any("manipulated" in r["item"].lower() for r in recs)


def test_corroboration_upload_variant():
    pkg = package.new_package("upload", "clip.mp4")
    recs = package.derive_recommended_corroboration(pkg)
    assert any("original, un-re-encoded capture" in r["item"]
               for r in recs)


# ---------------- evidence digest ----------------
def test_evidence_digest_deterministic_and_bounded():
    pkg = package.new_package("upload", "clip.mp4",
                              declared={"original_filename": "clip.mp4"})
    package.add_artifact(pkg, "source_media", sha256="b" * 64,
                         size_bytes=9, step="intake")
    package.attach_probe(pkg, {
        "ok": True, "tool": {"name": "ffprobe", "version": "6"},
        "container": {"format_name": "mp4", "duration_s": 3.0,
                      "size_bytes": 9, "bit_rate": 1, "stream_count": 1},
        "streams": [{"type": "video", "codec": "h264"}],
        "embedded_tags": {"encoder": "Lavf58"}})
    package.derive_observations(pkg)
    digest = package.evidence_digest(pkg, max_chars=2000)
    assert "MEDIA EVIDENCE PACKAGE" in digest
    assert "sha256=" + "b" * 64 in digest
    assert "UNTRUSTED CLAIMS" in digest
    assert "[DIRECT_OBSERVATION/metadata]" in digest
    assert len(digest) <= 2000
    tiny = package.evidence_digest(pkg, max_chars=300)
    assert len(tiny) <= 300 and "[digest truncated]" in tiny


def test_digest_labels_retrieval_metadata_not_original():
    pkg = package.new_package("youtube", "https://youtu.be/x")
    pkg["metadata_by_origin"]["retrieval_generated"]["title"] = "Clip"
    digest = package.evidence_digest(pkg)
    assert "NOT the uploader's original" in digest


# ---------------- pipeline attachments plumbing ----------------
MEDIA_MODEL_OUT = json.dumps({
    "risk_level": "medium", "confidence": 60, "verdict": "v",
    "signals": [], "organization_context_questions": [],
    "recommended_action": "a",
    "media_assessment": {
        "assessment_conclusion": "suspicious",
        "conclusion_rationale": "r",
        "visual_findings": {"status": "reviewed", "frame_observations":
                            [{"observation": "overlay text",
                              "competing_explanations": ["captioning"]}]},
        "audio_findings": {"status": "reviewed", "transcript": "words"},
    }})


class MediaFakeRouter:
    supports_media = True

    def __init__(self):
        self.multimodal_calls = []
        self.text_calls = []

    def generate(self, prompt):
        self.text_calls.append(prompt)
        return {"ok": True, "response": MEDIA_MODEL_OUT}

    def generate_multimodal(self, prompt, parts):
        self.multimodal_calls.append((prompt, parts))
        return {"ok": True, "response": MEDIA_MODEL_OUT}


class TextOnlyRouter:
    def generate(self, prompt):
        return {"ok": True, "response": MEDIA_MODEL_OUT}


def _bundle_artifact():
    return vnorm.make_artifact("digest text", label="Media bundle",
                               evidence_type="media_bundle")


def test_attachments_reach_media_router_and_prompt_describes_them():
    art = _bundle_artifact()
    router = MediaFakeRouter()
    atts = {art.id: [{"mime": "image/jpeg", "data": b"\xff\xd8\xff"},
                     {"mime": "audio/mp3", "data": b"ID3"}]}
    rpt = pipeline.analyze_artifacts([art], router,
                                     workflow="media_authenticity",
                                     attachments=atts)
    assert len(router.multimodal_calls) == 1
    prompt, parts = router.multimodal_calls[0]
    assert len(parts) == 2
    assert "1 sampled video frame(s)" in prompt
    assert "one extracted mono audio track" in prompt
    ma = rpt.artifact_analyses[0].extra["media_assessment"]
    assert ma["visual_findings"]["status"] == "reviewed"
    assert ma["audio_findings"]["status"] == "reviewed"


def test_text_only_runtime_degrades_honestly():
    art = _bundle_artifact()
    rpt = pipeline.analyze_artifacts(
        [art], TextOnlyRouter(), workflow="media_authenticity",
        attachments={art.id: [{"mime": "image/jpeg", "data": b"x"}]})
    assert any("does not support media input" in lim
               for lim in rpt.limitations)
    ma = rpt.artifact_analyses[0].extra["media_assessment"]
    # model claimed reviewed; normalization strips it — nothing attached
    assert ma["visual_findings"]["status"] == "no_visual_evidence"
    assert ma["audio_findings"]["transcript"] == ""


def test_multimodal_failure_falls_back_to_text():
    art = _bundle_artifact()

    class FailingMedia(MediaFakeRouter):
        def generate_multimodal(self, prompt, parts):
            return {"ok": False, "error": "payload too large"}
    router = FailingMedia()
    rpt = pipeline.analyze_artifacts(
        [art], router, workflow="media_authenticity",
        attachments={art.id: [{"mime": "image/jpeg", "data": b"x"}]})
    # two text calls: artifact-stage retry + the fusion pass
    assert len(router.text_calls) == 2
    assert any("degraded to text-only" in lim for lim in rpt.limitations)
    ma = rpt.artifact_analyses[0].extra["media_assessment"]
    assert ma["visual_findings"]["status"] == "no_visual_evidence"
    assert "no frames attached" in router.text_calls[0]


def test_no_attachments_no_change_for_general_workflow():
    art = vnorm.make_artifact("hello", evidence_type="freeform_text")
    router = MediaFakeRouter()
    rpt = pipeline.analyze_artifacts([art], router)
    assert router.multimodal_calls == []
    assert rpt.artifact_analyses[0].parsed

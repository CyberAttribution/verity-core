"""
Node media pipeline tests (v0.3.0): Keel media policy lock, intake
verification, honest degradation, YouTube URL validation, demo synth.

Run:  python -m pytest swarmtracker_node_ui/tests_media.py -q
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp()
os.environ.setdefault("NODE_UI_MEDIA_LEDGER",
                      os.path.join(_TMP, "kme.jsonl"))
os.environ.setdefault("NODE_UI_DATA_DIR", _TMP)
os.environ.setdefault("NODE_UI_DB", os.path.join(_TMP, "tests.db"))

from swarmtracker_node_ui import keel_policy, media_runner  # noqa: E402


# ---------- Keel media policy ----------

def test_media_policy_active_and_scoped():
    mp = keel_policy.media_policy()
    assert mp["enabled"] is True
    assert set(mp["local_tools_allowed"]) <= {"ffmpeg", "ffprobe",
                                              "exiftool", "yt-dlp"}
    assert mp["temp_dir_only"] and mp["delete_media_after_analysis"]
    allowed, reason = keel_policy.media_retrieval_allowed()
    assert allowed and "youtube" in reason


def test_self_test_includes_media_checks_and_passes():
    st = keel_policy.self_test()
    assert st["media_tools_scoped"]
    assert st["media_temp_dir_and_cleanup_locked"]
    assert st["media_egress_scoped_or_disabled"]
    assert st["general_network_still_disabled"]
    assert st["all_passed"]


def test_widened_policy_fails_closed(tmp_path, monkeypatch):
    raw = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    raw["media_evidence"]["governed_egress"]["media_retrieval"][
        "adapters"] = ["youtube", "everything"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(raw))
    monkeypatch.setattr(keel_policy, "POLICY_PATH", bad)
    policy, ok, err = keel_policy.load_policy()
    assert not ok and "subset" in err
    assert keel_policy.media_retrieval_allowed()[0] is False


def test_general_network_still_denied_by_policy():
    policy, ok, _ = keel_policy.load_policy()
    assert ok and policy["network_allowed"] is False


# ---------- intake verification ----------

def _gen(prompt):
    return {"ok": True, "event_id": "evt-t", "response": json.dumps(
        {"risk_level": "low", "confidence": 10, "verdict": "v",
         "signals": [], "recommended_action": "r"})}


def test_mislabeled_file_rejected(tmp_path):
    p = tmp_path / "fake.mp4"
    p.write_bytes(b"plain text pretending to be a video file")
    r = media_runner.run_media_job(str(p), "fake.mp4", generate_fn=_gen)
    assert not r["ok"] and "does not match" in r["error"]
    assert not p.exists()                     # cleanup on failure too


def test_unsupported_extension_rejected(tmp_path):
    p = tmp_path / "evil.exe"
    p.write_bytes(b"MZ" + b"\x00" * 64)
    r = media_runner.run_media_job(str(p), "evil.exe", generate_fn=_gen)
    assert not r["ok"] and "Unsupported format" in r["error"]


# ---------- YouTube URL validation (no network) ----------

@pytest.mark.parametrize("url,frag", [
    ("https://vimeo.com/1234", "Only single YouTube"),
    ("http://www.youtube.com/watch?v=dQw4w9WgXcQ", "https"),
    ("https://www.youtube.com/playlist?list=PLxyz", "Playlists"),
    ("https://www.youtube.com/@somechannel", "Channel"),
    ("https://www.youtube.com/watch?v=short", "valid video id"),
])
def test_youtube_url_validation_rejects(url, frag):
    r = media_runner.run_youtube_job(url, generate_fn=_gen)
    assert not r["ok"]
    assert frag.lower() in r["error"].lower()


def test_youtube_job_respects_policy_gate(monkeypatch):
    monkeypatch.setattr(keel_policy, "media_retrieval_allowed",
                        lambda: (False, "disabled for test"))
    r = media_runner.run_youtube_job(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ", generate_fn=_gen)
    assert not r["ok"] and "Keel policy lock" in r["error"]
    assert r["retrieval_category"] == "policy_denied"


# ---------- multimodal escalation (Keel-governed, opt-in) ----------

def test_escalation_policy_scoped_and_operator_gated():
    mp = keel_policy.media_policy()
    assert mp["escalation_enabled"] and \
        mp["escalation_provider"] == "vertex"
    st = keel_policy.self_test()
    assert st["media_escalation_scoped_or_disabled"]
    assert st["media_escalation_operator_gated_local_default"]
    assert st["all_passed"]
    allowed, reason = keel_policy.multimodal_escalation_allowed()
    assert allowed and "operator" in reason


def test_widened_escalation_fails_closed(tmp_path, monkeypatch):
    raw = json.loads(Path(keel_policy.POLICY_PATH).read_text())
    esc = raw["media_evidence"]["governed_egress"]["multimodal_escalation"]
    esc["provider"] = "anycloud"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(raw))
    monkeypatch.setattr(keel_policy, "POLICY_PATH", bad)
    _, ok, err = keel_policy.load_policy()
    assert not ok and "provider" in err
    assert keel_policy.multimodal_escalation_allowed()[0] is False


ESC_MA = {"assessment_conclusion": "no_material_indicators_observed",
          "conclusion_rationale": "r",
          "confidence_by_domain": {"technical": {"confidence": 60,
                                                 "basis": "b"}},
          "visual_findings": {"frame_observations": [
              {"observation": "test pattern grid visible"}]},
          "audio_findings": {"transcript": "sine tone"}}


class _FakeCloud:
    """Stands in for GeminiRouter; records what it was sent."""

    def __init__(self, ok=True):
        self.ok = ok
        self.sent_parts = None

    def generate_multimodal(self, prompt, parts):
        self.sent_parts = list(parts or [])
        if not self.ok:
            return {"ok": False, "response": "", "error": "quota"}
        return {"ok": True, "provider": "gemini", "response": json.dumps(
            {"risk_level": "low", "confidence": 40, "verdict": "v",
             "signals": [], "recommended_action": "r",
             "media_assessment": ESC_MA})}


def _video(tmp_path):
    path, err = media_runner.synthesize_demo_media(str(tmp_path))
    assert err is None
    return path


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_escalation_default_off_is_local_only(tmp_path, monkeypatch):
    r = media_runner.run_media_job(_video(tmp_path), "v.mp4",
                                   generate_fn=_gen)
    assert r["ok"] and not r["router_meta"]["escalated"]
    assert "not requested" in r["analysis_provenance"]["escalation"]
    assert r["analysis_provenance"]["sent_artifacts"] == []
    assert "local model" in r["analysis_provenance"]["artifact_analysis"]


# ---------- truthful modality states (five distinguishable conditions) ---

@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_local_only_video_states_extracted_not_examined(tmp_path):
    r = media_runner.run_media_job(_video(tmp_path), "v.mp4",
                                   generate_fn=_gen)
    vf = r["assessment"]["visual_findings"]
    af = r["assessment"]["audio_findings"]
    assert vf["status"] == "extracted_not_examined"
    assert "preserved as evidence" in vf["limitations"]
    assert "did not semantically examine" in vf["limitations"]
    assert af["status"] in ("extracted_not_examined", "not_extracted")
    # NEVER "no visual/audio evidence" language for extracted media
    assert "no frames were attached" not in vf["limitations"]
    assert vf["status"] != "no_visual_evidence"


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_image_has_no_audio_stream_not_missing_audio(tmp_path):
    import subprocess
    img = str(tmp_path / "i.jpg")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=duration=1:size=320x180:rate=1",
                    "-frames:v", "1", "-y", img], check=True)
    r = media_runner.run_media_job(img, "i.jpg", generate_fn=_gen)
    assert r["assessment"]["visual_findings"]["status"] == \
        "extracted_not_examined"
    assert r["assessment"]["audio_findings"]["status"] == \
        "no_audio_stream"
    assert "carries no audio stream" in \
        r["assessment"]["audio_findings"]["limitations"]


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_escalated_review_labels_gemini_in_limitations(tmp_path,
                                                       monkeypatch):
    fake = _FakeCloud(ok=True)
    monkeypatch.setattr(
        media_runner, "_escalation_router",
        lambda gen, sel=None: (
            media_runner.core_bridge.EscalatingRouter(gen, fake), ""))
    r = media_runner.run_media_job(_video(tmp_path), "v.mp4",
                                   generate_fn=_gen, escalate=True)
    vf = r["assessment"]["visual_findings"]
    assert vf["status"] == "reviewed"
    assert "authorized Gemini escalation" in vf["limitations"]
    assert "not by the local runtime" in vf["limitations"]


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_escalation_unavailable_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = media_runner.run_media_job(_video(tmp_path), "v.mp4",
                                   generate_fn=_gen, escalate=True)
    assert r["ok"] and not r["router_meta"]["escalated"]
    assert "unavailable" in r["analysis_provenance"]["escalation"]
    # honest signal-level line preserved
    ta = r["assessment"]["technical_authenticity"]
    assert "not performed by this runtime" in ta["signal_level_analysis"]


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_escalation_authorized_success(tmp_path, monkeypatch):
    fake = _FakeCloud(ok=True)
    monkeypatch.setattr(
        media_runner, "_escalation_router",
        lambda gen, sel=None: (
            media_runner.core_bridge.EscalatingRouter(gen, fake), ""))
    r = media_runner.run_media_job(_video(tmp_path), "v.mp4",
                                   generate_fn=_gen, escalate=True)
    assert r["ok"] and r["router_meta"]["escalated"]
    assert r["router_meta"]["escalation_ok"]
    # only extracted artifacts were sent, and they were recorded
    assert fake.sent_parts and all(p.get("path") or p.get("data")
                                   for p in fake.sent_parts)
    manifest = r["analysis_provenance"]["sent_artifacts"]
    assert manifest and all(m["kind"] in ("frame", "audio_track")
                            for m in manifest)
    # escalated findings retained (media WAS examined) + labeled
    assert r["assessment"]["visual_findings"]["status"] == "reviewed"
    assert r["assessment"]["audio_findings"]["transcript"] == "sine tone"
    assert "gemini" in r["analysis_provenance"]["artifact_analysis"]
    assert "local model" in r["analysis_provenance"]["fusion"]
    # evidence package records the escalation event
    steps = [e["step"] for e in r["package"]["processing_events"]]
    assert "multimodal_escalation" in steps


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_escalation_failure_degrades_honestly(tmp_path, monkeypatch):
    fake = _FakeCloud(ok=False)
    monkeypatch.setattr(
        media_runner, "_escalation_router",
        lambda gen, sel=None: (
            media_runner.core_bridge.EscalatingRouter(gen, fake), ""))
    r = media_runner.run_media_job(_video(tmp_path), "v.mp4",
                                   generate_fn=_gen, escalate=True)
    assert r["ok"] and r["router_meta"]["escalated"]
    assert r["router_meta"]["escalation_ok"] is False
    assert "failed" in r["analysis_provenance"]["escalation"].lower()
    # anti-fabrication + truthful state: cloud never reviewed it, and
    # the report says the frames were extracted but not examined —
    # never that visual content was absent
    assert r["assessment"]["visual_findings"]["status"] == \
        "extracted_not_examined"
    assert r["assessment"]["visual_findings"]["frame_observations"] == []
    # v0.3.4: provider failure suppresses the authenticity conclusion
    assert r["assessment"]["assessment_conclusion"] == \
        "multimodal_interpretation_unavailable"


# ---------- UI/backend wiring regression (v0.3.3) ------------------------
#
# These lock the escalation UI edge that unit tests of run_media_job did
# not cover: checkbox -> payload -> backend flag, mode/overlay/button
# state transitions, and "no cloud call without authorization".

import re as _re


def _mk_client(monkeypatch, tmp_path, gemini_key="x", cloud=None,
               spy=None):
    """A TestClient with the local model mocked and (optionally) a fake
    cloud escalation router. `spy` (a list) records whether the
    escalation router was constructed for a job."""
    import json as _json
    import os as _os
    d = tmp_path / "cli"
    d.mkdir(exist_ok=True)
    _os.environ.update(NODE_UI_DATA_DIR=str(d),
                       NODE_UI_DB=str(d / "t.db"),
                       NODE_UI_MEDIA_LEDGER=str(d / "k.jsonl"))
    if gemini_key is None:
        _os.environ.pop("GEMINI_API_KEY", None)
    else:
        _os.environ["GEMINI_API_KEY"] = gemini_key
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as app_mod
    ma = {"assessment_conclusion": "no_material_indicators_observed",
          "conclusion_rationale": "r",
          "confidence_by_domain": {"technical": {"confidence": 60,
                                                 "basis": "b"}},
          "visual_findings": {"frame_observations": [
              {"observation": "grid"}]},
          "audio_findings": {"transcript": "tone"}}
    art = _json.dumps({"risk_level": "low", "confidence": 40,
                       "verdict": "v", "signals": [],
                       "recommended_action": "r", "media_assessment": ma})
    fus = _json.dumps({"fused_verdict": "f", "overall_risk": "low",
                       "overall_confidence": 40,
                       "attribution_hypotheses": []})
    app_mod.oc.generate = lambda p, **kw: {
        "ok": True, "response": fus if "fusion engine" in p else art,
        "openclaw_event_id": "e"}

    def fake_router(gen, sel=None):
        if spy is not None:
            spy.append(True)
        if cloud is None:
            return None, "no cloud configured for test"
        return media_runner.core_bridge.EscalatingRouter(gen, cloud), ""
    monkeypatch.setattr(media_runner, "_escalation_router", fake_router)
    return TestClient(app_mod.app)


def _vis(html):
    return _re.sub(r"<script.*?</script>", "", html, flags=_re.S)


class _OKCloud:
    def generate_multimodal(self, prompt, parts):
        import json as _json
        return {"ok": True, "provider": "gemini",
                "attempted_models": ["gemini-2.0-flash"],
                "response": _json.dumps(
            {"risk_level": "low", "confidence": 40, "verdict": "v",
             "signals": [], "recommended_action": "r",
             "media_assessment": {
                 "assessment_conclusion": "no_material_indicators_observed",
                 "conclusion_rationale": "r",
                 "confidence_by_domain": {"technical": {"confidence": 60,
                                                       "basis": "b"}},
                 "visual_findings": {"frame_observations": [
                     {"observation": "grid"}]},
                 "audio_findings": {"transcript": "tone"}}})}


class _FailCloud:
    def generate_multimodal(self, prompt, parts):
        return {"ok": False, "response": "", "error": "quota exceeded"}


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_ui_unchecked_local_job_no_cloud(tmp_path, monkeypatch):
    spy = []
    cli = _mk_client(monkeypatch, tmp_path, cloud=_OKCloud(), spy=spy)
    v = _video(tmp_path)
    with open(v, "rb") as f:
        r = _vis(cli.post("/media/upload",
                          files={"media_file": ("v.mp4", f, "video/mp4")},
                          data={}).text)
    assert ">Local Only<" in r and ">No<" in r and ">Disabled<" in r
    assert spy == [], "cloud router must NOT be built without authorization"


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_ui_checked_authorized_reaches_backend(tmp_path, monkeypatch):
    spy = []
    cli = _mk_client(monkeypatch, tmp_path, cloud=_OKCloud(), spy=spy)
    v = _video(tmp_path)
    with open(v, "rb") as f:
        r = _vis(cli.post("/media/upload",
                          files={"media_file": ("v.mp4", f, "video/mp4")},
                          data={"escalate_multimodal": "1"}).text)
    assert spy == [True], "checkbox must reach the backend escalation path"
    assert ">Governed Hybrid<" in r and "Yes — extracted artifacts only" in r


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_ui_checked_provider_failure_preserves_local(tmp_path, monkeypatch):
    cli = _mk_client(monkeypatch, tmp_path, cloud=_FailCloud())
    v = _video(tmp_path)
    with open(v, "rb") as f:
        r = _vis(cli.post("/media/upload",
                          files={"media_file": ("v.mp4", f, "video/mp4")},
                          data={"escalate_multimodal": "1"}).text)
    assert ">Governed Hybrid<" in r
    assert "Failed — local findings preserved" in r
    # local findings preserved + truthful (not "no visual content")
    assert "extracted not examined" in r


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_ui_checked_missing_key_stays_local(tmp_path, monkeypatch):
    # No fake router: use the REAL _escalation_router with the key
    # removed, so the missing-key gate (not a mock) is what's exercised.
    v = _video(tmp_path)
    import os as _os
    d = tmp_path / "nokey"
    d.mkdir()
    _os.environ.update(NODE_UI_DATA_DIR=str(d),
                       NODE_UI_DB=str(d / "t.db"),
                       NODE_UI_MEDIA_LEDGER=str(d / "k.jsonl"))
    _os.environ.pop("GEMINI_API_KEY", None)
    import json as _json
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as app_mod
    art = _json.dumps({"risk_level": "low", "confidence": 40,
                       "verdict": "v", "signals": [],
                       "recommended_action": "r"})
    app_mod.oc.generate = lambda p, **kw: {"ok": True, "response": art,
                                           "openclaw_event_id": "e"}
    cli = TestClient(app_mod.app)
    with open(v, "rb") as f:
        r = _vis(cli.post("/media/upload",
                          files={"media_file": ("v.mp4", f, "video/mp4")},
                          data={"escalate_multimodal": "1"}).text)
    # authorized but unavailable -> honest degrade to Local Only
    assert ">Local Only<" in r and "Governed Hybrid" not in r


def test_ui_media_page_has_transition_controls(tmp_path, monkeypatch):
    cli = _mk_client(monkeypatch, tmp_path, cloud=_OKCloud())
    html = cli.get("/media").text
    # button labels (dynamic)
    assert "Analyze with Governed Hybrid" in html
    assert "Retrieve &amp; Analyze with Governed Hybrid" in html
    # overlay copy differs by mode
    assert 'id="ov-local"' in html and 'id="ov-hybrid"' in html
    assert "No network egress" in html
    assert "Governed egress authorized" in html
    assert "Only extracted artifacts leave this node" in html
    # escalation-aware overlay steps
    assert 'id="rs-selecting"' in html and 'id="rs-escalating"' in html \
        and 'id="rs-fusing"' in html
    # pre-run indicator uses "pending" (JS), never a fake "yes"
    assert "Cloud used: <strong>pending</strong>" in html
    # forms pass their own element so per-form escalation is detected
    assert "startRun(true, this)" in html and "startRun(false, this)" \
        in html


def test_ui_analyze_page_not_stale(tmp_path, monkeypatch):
    cli = _mk_client(monkeypatch, tmp_path, cloud=_OKCloud())
    a = cli.get("/analyze").text
    assert "v0.1.4" not in a
    assert "Governed per-job cloud escalation is available now in the " \
           "Media Evidence" in a


# ---------- v0.3.4: provider failure, truthfulness, exports ----------

def test_gemini_403_sanitized_attempts_no_key_leak(monkeypatch):
    import os as _os
    from verity_core_staging import model_router as mr
    monkeypatch.setattr(mr, "GEMINI_API_KEY", "AItest-SECRET-KEY-abc")

    class _Resp:
        status_code = 403
        text = ('{"error":{"code":403,"message":"PERMISSION_DENIED: '
                'API generativelanguage is blocked","status":'
                '"PERMISSION_DENIED"}}')

        def json(self):
            return {}

    def fake_post(url, headers=None, json=None, timeout=None):
        assert "AItest-SECRET-KEY-abc" in url    # key IS in the URL
        return _Resp()
    monkeypatch.setattr(mr.requests, "post", fake_post)
    try:
        mr.call_gemini({"contents": []})
        assert False, "should have raised"
    except mr.GeminiError as e:
        assert e.status == 403
        assert len(e.attempted_models) == 3       # full fallback chain
        assert "AItest-SECRET-KEY-abc" not in str(e)
        for a in e.attempts:
            assert "AItest-SECRET-KEY-abc" not in a["error"]
            assert "PERMISSION_DENIED" in a["error"]   # body surfaced


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_provider_failure_suppresses_conclusion_truthfully(tmp_path,
                                                           monkeypatch):
    class _Fail:
        def generate_multimodal(self, prompt, parts):
            return {"ok": False, "response": "",
                    "error": "HTTP 403: PERMISSION_DENIED",
                    "attempted_models": ["gemini-2.0-flash",
                                         "gemini-2.0-flash-lite",
                                         "gemini-2.0-pro"],
                    "provider_status": 403}
    monkeypatch.setattr(
        media_runner, "_escalation_router",
        lambda g, sel=None: (
            media_runner.core_bridge.EscalatingRouter(g, _Fail()), ""))

    def gen(p):
        import json as _json
        # local text model even (wrongly) claims absence — must be
        # overridden, not surfaced:
        return {"ok": True, "event_id": "e", "response": _json.dumps(
            {"risk_level": "low", "confidence": 30, "verdict": "v",
             "signals": [], "recommended_action": "r",
             "media_assessment": {
                 "assessment_conclusion": "no_material_indicators_observed",
                 "conclusion_rationale": "The package contains no "
                                         "attached visual or audio "
                                         "content.",
                 "confidence_by_domain": {}}})}
    r = media_runner.run_media_job(_video(tmp_path), "v.mp4",
                                   generate_fn=gen, escalate=True)
    a = r["assessment"]
    assert a["assessment_conclusion"] == \
        "multimodal_interpretation_unavailable"
    assert a["multimodal_unavailable"] is True
    assert a["attempted_models"] == ["gemini-2.0-flash",
                                     "gemini-2.0-flash-lite",
                                     "gemini-2.0-pro"]
    assert a["provider_status"] == 403
    # the false "no ... content" claim must be gone
    assert "no attached visual or audio content" not in \
        a["conclusion_rationale"]
    # artifacts exist -> extracted-but-unexamined, never "absent"
    assert a["visual_findings"]["status"] == "extracted_not_examined"
    assert a["audio_findings"]["status"] == "extracted_not_examined"
    # custody: sent-but-failed recorded; source retained/deleted locally
    cust = r["router_meta"]["custody"]
    assert cust["sent_but_provider_failed"] and cust["retained_local"]
    assert cust["sent_to_provider"] == []


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_stix_export_valid_and_no_key_leak(tmp_path, monkeypatch):
    _os_key = "AItest-SECRET-KEY-xyz"
    import os as _os
    _os.environ["GEMINI_API_KEY"] = _os_key
    cli = _mk_client(monkeypatch, tmp_path, cloud=_OKCloud())
    with open(_video(tmp_path), "rb") as f:
        r = cli.post("/media/upload",
                     files={"media_file": ("v.mp4", f, "video/mp4")},
                     data={"escalate_multimodal": "1"})
    tok = _re.search(r'/media/report/([^/]+)/json', r.text).group(1)
    # JSON export: valid + no key
    j = cli.get(f"/media/report/{tok}/json")
    assert j.status_code == 200 and _os_key not in j.text
    # STIX 2.1: structurally valid
    s = cli.get(f"/media/report/{tok}/stix")
    assert s.status_code == 200 and _os_key not in s.text
    bundle = json.loads(s.text)
    assert bundle["type"] == "bundle" and bundle["id"].startswith("bundle--")
    types = {o["type"] for o in bundle["objects"]}
    assert {"identity", "artifact", "note"} <= types
    art = next(o for o in bundle["objects"] if o["type"] == "artifact")
    note = next(o for o in bundle["objects"] if o["type"] == "note")
    assert len(art["hashes"]["SHA-256"]) == 64
    assert note["object_refs"] == [art["id"]]
    for o in bundle["objects"]:
        assert o["id"].startswith(o["type"] + "--")
        if o["type"] != "artifact":            # SCOs carry no timestamps
            assert o["spec_version"] == "2.1"
    # Markdown report
    rep = cli.get(f"/media/report/{tok}/report")
    assert rep.status_code == 200 and "Media Authenticity Report" in rep.text


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_canonical_case_persists_escalation(tmp_path, monkeypatch):
    cli = _mk_client(monkeypatch, tmp_path, cloud=_OKCloud())
    with open(_video(tmp_path), "rb") as f:
        cli.post("/media/upload",
                 files={"media_file": ("v.mp4", f, "video/mp4")},
                 data={"escalate_multimodal": "1",
                       "new_investigation": "PersistEsc"})
    from swarmtracker_node_ui.app import STORE
    pid = next(p["id"] for p in STORE.list_projects()
               if p["name"] == "PersistEsc")
    cases = STORE.list_canonical_cases(pid)
    assert len(cases) == 1
    # reopen (simulated restart uses same DB path env)
    case = STORE.get_canonical_case(cases[0]["id"])["case"]
    esc = case["escalation"]
    assert esc["authorized"] is True and esc["provider"] == "gemini"
    assert esc["state"] == "used"
    assert esc["attempted_models"]                     # models recorded
    assert esc["artifact_custody"]["sent_to_provider"]
    assert esc["artifact_custody"]["retained_local"]


def test_media_page_mode_cards_replace_checkbox(tmp_path, monkeypatch):
    cli = _mk_client(monkeypatch, tmp_path, cloud=_OKCloud())
    html = cli.get("/media").text
    # two mode cards, three forms
    assert html.count("Analysis Mode") >= 3
    assert "Local Analysis" in html and "Governed Multimodal Analysis" \
        in html
    # old floating checkbox copy is gone
    assert "Authorize cloud multimodal analysis (Gemini) for this" not in \
        html
    # radios drive the same backend field, local default
    assert 'name="escalate_multimodal" value=""' in html
    assert 'name="escalate_multimodal" value="1"' in html
    assert "checked" in html and "escMode(this)" in html


# ---------- v0.5.0 Part 1: Vertex request-schema (roles) ----------

def test_vertex_body_no_role_defaults_user():
    from verity_core_staging import model_router as mr
    body = mr.normalize_vertex_body(prompt="evidence text")
    assert body["contents"][0]["role"] == "user"
    assert body["contents"][0]["parts"][0]["text"] == "evidence text"
    assert "systemInstruction" not in body


def test_vertex_system_goes_to_systemInstruction():
    from verity_core_staging import model_router as mr
    body = mr.normalize_vertex_body(prompt="analyze this",
                                    system="You are a governed analyst.")
    assert body["systemInstruction"]["parts"][0]["text"] == \
        "You are a governed analyst."
    # system must NOT appear as a content role
    for c in body["contents"]:
        assert c["role"] in ("user", "model")


def test_vertex_prior_output_serializes_as_model():
    from verity_core_staging import model_router as mr
    body = mr.normalize_vertex_body(
        prompt="follow-up",
        history=[{"role": "assistant", "text": "prior model output"}])
    roles = [c["role"] for c in body["contents"]]
    assert roles == ["model", "user"]     # assistant -> model, then user


def test_vertex_unsupported_role_rejected():
    from verity_core_staging import model_router as mr
    for bad in ("system-as-content", "tool", "function", "developer-x"):
        # only exact known roles map; unknowns raise (never emit invalid)
        try:
            mr.normalize_vertex_body(
                prompt="x", history=[{"role": bad, "text": "y"}])
            # developer/system fold into systemInstruction; others raise
            body = mr.normalize_vertex_body(
                prompt="x", history=[{"role": bad, "text": "y"}])
            for c in body["contents"]:
                assert c["role"] in ("user", "model")
        except ValueError:
            pass  # acceptable: unknown role rejected


def test_vertex_multimodal_parts_on_user_message():
    from verity_core_staging import model_router as mr
    parts = [{"inline_data": {"mime_type": "image/jpeg", "data": "aGk="}}]
    body = mr.normalize_vertex_body(prompt="assess", parts=parts)
    user = [c for c in body["contents"] if c["role"] == "user"][0]
    kinds = [("inline_data" in p) for p in user["parts"]]
    assert kinds.count(True) == 1          # image attached to user turn
    assert user["parts"][0]["text"] == "assess"


def test_vertex_media_request_shape_passes(monkeypatch, tmp_path):
    # the actual Media Evidence multimodal body builds a valid request
    from verity_core_staging.routers import vertex as vx
    img = tmp_path / "f.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"0" * 32)
    body, err = vx._multimodal_body("assess this frame",
                                    [{"mime": "image/jpeg",
                                      "path": str(img)}])
    assert err is None
    assert body["contents"][0]["role"] == "user"
    assert any("inline_data" in p for p in body["contents"][0]["parts"])
    # every content role is valid
    assert all(c["role"] in ("user", "model") for c in body["contents"])


def test_vertex_error_sanitization_intact_after_role_fix(monkeypatch):
    import types
    from verity_core_staging import model_router as mr

    class _R:
        status_code = 400
        text = ('{"error":{"code":400,"message":"INVALID_ARGUMENT ya29.'
                'LEAK"}}')

        def json(self):
            return {}
    monkeypatch.setattr(mr, "requests",
                        types.SimpleNamespace(post=lambda *a, **k: _R()))
    monkeypatch.setattr(mr, "_adc_bearer_token", lambda: ("ya29.LEAK", "p"))
    try:
        mr.call_vertex(mr.normalize_vertex_body(prompt="x"),
                       ["gemini-3.6-flash"], "proj", "global")
        assert False
    except mr.ProviderError as e:
        assert "ya29" not in str(e)
        assert all("ya29" not in a["error"] for a in e.attempts)


# ---------- v0.4.0: Vertex AI provider migration ----------

def test_vertex_url_auth_no_key_leak(monkeypatch):
    import types
    from verity_core_staging import model_router as mr
    cap = {}

    class _R:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts":
                    [{"text": "ok"}]}}]}
        text = "{}"

    def _post(url, headers=None, json=None, timeout=None):
        cap["url"] = url
        cap["auth"] = headers.get("Authorization")
        return _R()
    monkeypatch.setattr(mr, "requests", types.SimpleNamespace(post=_post))
    monkeypatch.setattr(mr, "_adc_bearer_token",
                        lambda: ("ya29.SECRET-TOKEN", "adc-proj"))
    data = mr.call_vertex({"contents": []}, ["gemini-3.6-flash"],
                          "proj", "us-central1")
    # Vertex endpoint, no api key in URL, bearer in header only
    assert "aiplatform.googleapis.com" in cap["url"]
    assert "key=" not in cap["url"]
    assert cap["auth"].startswith("Bearer ")
    assert data["_model_used"] == "gemini-3.6-flash"
    assert data["_latency_ms"] >= 0


def test_vertex_global_endpoint_host_and_path(monkeypatch):
    import types
    from verity_core_staging import model_router as mr
    cap = {}

    class _R:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts":
                    [{"text": "ok"}]}}]}
        text = "{}"

    def _post(url, headers=None, json=None, timeout=None):
        cap["url"] = url
        return _R()
    monkeypatch.setattr(mr, "requests", types.SimpleNamespace(post=_post))
    monkeypatch.setattr(mr, "_adc_bearer_token", lambda: ("ya29.x", "p"))
    mr.call_vertex({"contents": []}, ["gemini-3.6-flash"], "proj",
                   "global")
    # global endpoint: NO region prefix on host, locations/global in path
    assert cap["url"].startswith(
        "https://aiplatform.googleapis.com/v1/projects/proj/"
        "locations/global/publishers/google/models/gemini-3.6-flash")
    # regional keeps the prefix
    mr.call_vertex({"contents": []}, ["gemini-3.6-flash"], "proj",
                   "us-central1")
    assert cap["url"].startswith(
        "https://us-central1-aiplatform.googleapis.com/")


def test_vertex_403_fallback_attempts_no_token_leak(monkeypatch):
    import types
    from verity_core_staging import model_router as mr

    class _R:
        status_code = 403
        text = ('{"error":{"code":403,"message":"PERMISSION_DENIED: '
                'caller lacks aiplatform.endpoints.predict"}}')

        def json(self):
            return {}

    monkeypatch.setattr(mr, "requests",
                        types.SimpleNamespace(
                            post=lambda *a, **k: _R()))
    monkeypatch.setattr(mr, "_adc_bearer_token",
                        lambda: ("ya29.SECRET-TOKEN", "p"))
    try:
        mr.call_vertex({"contents": []},
                       ["gemini-3.6-flash", "gemini-3.5-flash-lite"],
                       "proj", "us-central1")
        assert False
    except mr.ProviderError as e:
        assert e.status == 403
        assert len(e.attempted_models) == 2       # full fallback chain
        blob = str(e) + " ".join(a["error"] for a in e.attempts)
        assert "ya29" not in blob and "SECRET-TOKEN" not in blob
        assert "PERMISSION_DENIED" in blob        # body surfaced


def test_sanitize_strips_bearer_and_token():
    from verity_core_staging import model_router as mr
    s = mr.sanitize_provider_text(
        "Bearer ya29.LEAK for ...?key=SECRET9&x=1")
    assert "ya29" not in s and "LEAK" not in s and "SECRET9" not in s


def test_no_adc_no_project_fails_closed_local_default(monkeypatch):
    from swarmtracker_node_ui import providers, core_bridge
    # ADC missing -> not ready, no router, honest reason
    monkeypatch.setattr(core_bridge.model_router, "adc_available",
                        lambda: False)
    cfg = providers.provider_config()
    assert cfg["ready"] is False and "ADC" in cfg["reason"] or \
        "google-auth" in cfg["reason"]
    r, why = providers.build_cloud_router()
    assert r is None and why


def test_provider_chain_selection_and_labels(monkeypatch):
    from swarmtracker_node_ui import providers, core_bridge
    monkeypatch.setattr(core_bridge.model_router, "adc_available",
                        lambda: True)
    monkeypatch.setattr(providers, "VERTEX_PROJECT", "proj")
    monkeypatch.setattr(providers, "VERTEX_LOCATION", "global")
    cfg = providers.provider_config()
    assert cfg["provider"] == "vertex" and cfg["name"] == "Vertex AI"
    assert cfg["default_model_id"] == "gemini-3.6-flash"
    assert cfg["region"] == "global"        # v0.4.1: default endpoint
    labels = [l for _, l in cfg["models"]]
    assert labels[:2] == ["Gemini 3.6 Flash", "Gemini 3.5 Flash Lite"]
    r, _ = providers.build_cloud_router("gemini-3.5-flash-lite")
    assert r.models[0] == "gemini-3.5-flash-lite"   # selection leads
    assert "gemini-3.6-flash" in r.models           # chain preserved


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_vertex_escalation_records_provider_metadata(tmp_path,
                                                     monkeypatch):
    class _VOK:
        provider = "vertex"
        project = "demo-proj"
        location = "us-central1"
        models = ["gemini-3.6-flash"]

        def generate_multimodal(self, prompt, parts):
            import json as _json
            return {"ok": True, "provider": "vertex", "response": _json.dumps(
                {"risk_level": "low", "confidence": 40, "verdict": "v",
                 "signals": [], "recommended_action": "r",
                 "media_assessment": {
                     "assessment_conclusion":
                         "no_material_indicators_observed",
                     "conclusion_rationale": "x",
                     "confidence_by_domain": {},
                     "visual_findings": {"frame_observations":
                                         [{"observation": "g"}]},
                     "audio_findings": {"transcript": "t"}}}),
                    "model_used": "gemini-3.6-flash", "latency_ms": 812,
                    "project": "demo-proj", "region": "us-central1",
                    "attempted_models": ["gemini-3.6-flash"],
                    "attempts": [{"model": "gemini-3.6-flash",
                                  "status": 200, "latency_ms": 812}]}
    monkeypatch.setattr(
        media_runner, "_escalation_router",
        lambda g, sel=None: (media_runner.core_bridge.EscalatingRouter(
            g, _VOK()), ""))
    r = media_runner.run_media_job(_video(tmp_path), "v.mp4",
                                   generate_fn=_gen, escalate=True,
                                   selected_model="gemini-3.6-flash")
    rm = r["router_meta"]
    assert rm["escalation_provider"] == "vertex"
    assert rm["model_used"] == "gemini-3.6-flash"
    assert rm["latency_ms"] == 812
    assert rm["vertex_project"] == "demo-proj"
    assert rm["vertex_region"] == "us-central1"
    assert rm["attempt_history"]


def test_media_page_shows_vertex_and_model_select(tmp_path, monkeypatch):
    import os as _os
    _os.environ["GOOGLE_CLOUD_PROJECT"] = "demo-proj"
    from swarmtracker_node_ui import providers, core_bridge
    monkeypatch.setattr(core_bridge.model_router, "adc_available",
                        lambda: True)
    monkeypatch.setattr(providers, "VERTEX_PROJECT", "demo-proj")
    cli = _mk_client(monkeypatch, tmp_path, cloud=_OKCloud())
    html = cli.get("/media").text
    assert "Vertex AI" in html and "Gemini 3.6 Flash" in html
    assert 'name="vertex_model"' in html
    assert "GEMINI_API_KEY" not in html      # no api-key language remains


# ---------- UI regression: progress overlay hidden on page load ----------

def test_media_page_overlay_hidden_by_default():
    """Regression (v0.3.0): .run-overlay{display:flex} beat the hidden
    attribute's UA display:none, showing the full-screen 'Analyzing on
    this node…' overlay on a fresh GET /media. The overlay must ship
    with the hidden attribute AND a CSS rule that keeps [hidden]
    display:none; it may only appear via startRun() on form submit."""
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as app_mod
    html = TestClient(app_mod.app).get("/media").text
    # the overlay element itself defaults to hidden
    import re
    m = re.search(r'<div[^>]*id="run-overlay"[^>]*>', html)
    assert m, "run-overlay div missing"
    assert "hidden" in m.group(0), "run-overlay must ship hidden"
    # the CSS guard that makes the hidden attribute actually win
    assert ".run-overlay[hidden]" in html and "display:none" in \
        html.split(".run-overlay[hidden]")[1][:40], \
        "missing .run-overlay[hidden]{display:none} guard"
    # the overlay is only revealed inside startRun(), never at init
    body = html.split("</style>")[-1]
    assert body.count("ov.hidden = false") == 1
    assert "startRun" in html and "onsubmit" in html


# ---------- guaranteed demo case ----------

@pytest.mark.skipif(not media_runner.core_bridge.HAS_MEDIA_INGEST or
                    not media_runner.core_bridge.media_tools.available(
                        "ffmpeg"),
                    reason="ffmpeg not available")
def test_demo_media_synthesis(tmp_path):
    path, err = media_runner.synthesize_demo_media(str(tmp_path))
    assert err is None and os.path.getsize(path) > 1000
    with open(path, "rb") as f:
        head = f.read(16)
    assert media_runner._magic_ok(head, "mp4")

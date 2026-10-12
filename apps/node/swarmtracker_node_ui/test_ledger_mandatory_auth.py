"""
Task 3 regressions: the cloud-authorization ledger write is MANDATORY
before any cloud SDK generation, and result rows must correlate with
their authorization rows.

Run: PYTHONPATH=apps/node python -m pytest \
    apps/node/swarmtracker_node_ui/test_ledger_mandatory_auth.py -q

Everything is mocked; no cloud API is ever called. The ledger goes to
temporary per-test paths (a real file, or a directory when we simulate
an unwritable ledger).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarmtracker_node_ui import governed_analysis  # noqa: E402
from swarmtracker_node_ui import keel_events  # noqa: E402

_ITEM = [{"label": "Note", "text": "wire to apex-billing.top",
          "evidence_type": "freeform_text"}]


class _VOK:
    provider = "vertex"
    project = "swarmtracker-main"
    location = "global"
    models = ["gemini-3.6-flash"]

    def __init__(self):
        self.generates = 0

    def generate(self, p):
        self.generates += 1
        return {"ok": True, "provider": "vertex",
                "response": "Bounded cloud assessment.",
                "model_used": "gemini-3.6-flash", "latency_ms": 5,
                "project": "swarmtracker-main", "region": "global",
                "attempted_models": ["gemini-3.6-flash"], "attempts": []}


def _patch_layers(monkeypatch, cloud):
    """Mock policy + provider layers so only the ledger can fail."""
    monkeypatch.setattr(governed_analysis.keel_policy,
                        "multimodal_escalation_allowed",
                        lambda: (True, ""))
    monkeypatch.setattr(governed_analysis.providers, "provider_config",
                        lambda: {"ready": True, "reason": "",
                                 "provider": "vertex",
                                 "project": "swarmtracker-main",
                                 "region": "global"})
    monkeypatch.setattr(governed_analysis.providers,
                        "build_cloud_router", lambda sel=None: (cloud, ""))


def _tmp_ledger(monkeypatch):
    path = Path(tempfile.mkdtemp()) / "keel_ledger.jsonl"
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", path)
    return path


def _read_rows(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(
        encoding="utf-8", errors="replace").splitlines() if x.strip()]


# ---- serialization honesty: bad detail fails without partial bytes ----

def test_mandatory_rejects_cyclic_detail(monkeypatch, tmp_path):
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH",
                        tmp_path / "k.jsonl")
    cyc = {}
    cyc["self"] = cyc
    with pytest.raises(keel_events.LedgerWriteError,
                       match="serializable|cyclic"):
        keel_events.append_event_mandatory("media_escalation_authorized",
                                           {"detail": cyc})
    assert not (tmp_path / "k.jsonl").exists()


def test_mandatory_rejects_unserializable_detail(monkeypatch, tmp_path):
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH",
                        tmp_path / "k.jsonl")
    with pytest.raises(keel_events.LedgerWriteError):
        keel_events.append_event_mandatory("media_escalation_authorized",
                                           {"detail": {1, 2}})
    assert not (tmp_path / "k.jsonl").exists()


def test_mandatory_rejects_bad_event_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH",
                        tmp_path / "k.jsonl")
    for bad in ("", None, 7):
        with pytest.raises(keel_events.LedgerWriteError):
            keel_events.append_event_mandatory(bad, {"x": 1})
    assert not (tmp_path / "k.jsonl").exists()


# ---- durability honesty: fsync / flock / dir-sync failures fail closed ----

def test_mandatory_requires_fcntl(monkeypatch, tmp_path):
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH",
                        tmp_path / "k.jsonl")
    old = keel_events.fcntl
    keel_events.fcntl = None
    try:
        with pytest.raises(keel_events.LedgerWriteError,
                           match="flock|fenc|unavailable"):
            keel_events.append_event_mandatory("e", {"x": 1})
    finally:
        keel_events.fcntl = old
    assert not (tmp_path / "k.jsonl").exists()


def test_mandatory_dir_sync_failure_is_fatal(monkeypatch, tmp_path):
    path = tmp_path / "k.jsonl"
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", path)

    def _boom(_dir):
        raise OSError("simulated dir fsync failure")

    monkeypatch.setattr(keel_events, "_fsync_dir_strict", _boom)
    with pytest.raises(keel_events.LedgerWriteError,
                       match="simulated dir fsync failure"):
        keel_events.append_event_mandatory("e", {"x": 1})


def test_mandatory_fsync_failure_is_fatal(monkeypatch, tmp_path):
    # Use a real opened file so the failure is actually fsync, rather
    # than an unrelated stat/close error masked by a fake handle.
    path = tmp_path / "k.jsonl"
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", path)

    def fsync_failure(fd):
        raise OSError("simulated file fsync failure")

    monkeypatch.setattr(keel_events.os, "fsync", fsync_failure)
    with pytest.raises(keel_events.LedgerWriteError,
                       match="simulated file fsync failure"):
        keel_events.append_event_mandatory("e", {"x": 1})
    cloud = _VOK()
    _patch_layers(monkeypatch, cloud)
    result = governed_analysis.run_text_cloud_assist(
        _ITEM, operator_authorized=True)
    assert cloud.generates == 0
    assert result["audit_write_failed"] is True
    assert result["provider_attempted"] is False
    assert result["authorization_event_id"] is None


def test_mandatory_directory_ledger_path_fails_closed(monkeypatch):
    # NODE_UI_MEDIA_LEDGER pointing at a directory -> IsADirectoryError
    # must surface as LedgerWriteError, never as silent success.
    ledger_dir = Path(tempfile.mkdtemp()) / "ledger_is_dir"
    ledger_dir.mkdir()
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", ledger_dir)
    with pytest.raises(keel_events.LedgerWriteError):
        keel_events.append_event_mandatory("e", {"x": 1})


# ---- end-to-end honesty: no generate call without a persisted auth row ----

def test_pre_call_ledger_failure_blocks_generate(monkeypatch):
    cloud = _VOK()
    _patch_layers(monkeypatch, cloud)
    ledger_dir = Path(tempfile.mkdtemp()) / "ledger_is_dir"
    ledger_dir.mkdir()
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", ledger_dir)

    res = governed_analysis.run_text_cloud_assist(
        _ITEM, operator_authorized=True)
    assert cloud.generates == 0        # ZERO cloud SDK invocations
    assert res["ok"] is False
    assert res["audit_write_failed"] is True
    assert res["provider_attempted"] is False
    assert res["sent_manifest"] == []
    assert res["text"] == ""
    assert res["authorization_event_id"] is None
    assert "ledger write failed" in res["reason"]

    rm = governed_analysis.text_assist_router_meta(res)
    assert rm["escalated"] is False    # fail-closed local-only shaping
    assert rm["audit_write_failed"] is True
    assert rm["escalation_provider"] is None
    assert rm["sent_artifacts"] == []
    assert rm["denied"] is False and rm["unavailable"] is False
    assert rm["custody"]["sent_to_provider"] == []


def test_result_row_references_authorization_row(monkeypatch):
    cloud = _VOK()
    _patch_layers(monkeypatch, cloud)
    path = _tmp_ledger(monkeypatch)

    res = governed_analysis.run_text_cloud_assist(
        _ITEM, operator_authorized=True)
    assert res["ok"] is True and cloud.generates == 1
    assert res["audit_write_failed"] is False

    rows = _read_rows(path)
    auth = [r for r in rows
            if r["event"] == "cloud_escalation_authorized"]
    results = [r for r in rows
               if r["event"] == "cloud_escalation_result"]
    assert len(auth) == 1 and len(results) == 1
    assert results[0]["detail"].get("authorization_event_id") \
        == auth[0]["id"], "result row must correlate with its auth row"
    assert results[0]["detail"].get("provider") \
        == auth[0]["detail"].get("provider")
    assert res["authorization_event_id"] == auth[0]["id"]


def test_post_call_result_write_failure_is_honest(monkeypatch):
    cloud = _VOK()
    _patch_layers(monkeypatch, cloud)
    path = _tmp_ledger(monkeypatch)

    real = governed_analysis.audit_result

    def _fail_result(*a, **k):
        raise keel_events.LedgerWriteError("simulated result-row failure")

    monkeypatch.setattr(governed_analysis, "audit_result", _fail_result)
    res = governed_analysis.run_text_cloud_assist(
        _ITEM, operator_authorized=True)
    # the call already happened; it is not rerun or hidden
    assert cloud.generates == 1
    assert res["ok"] is True
    assert res["text"] == "Bounded cloud assessment."
    assert res["audit_write_failed"] is True
    assert res["authorization_event_id"]

    rows = _read_rows(path)
    assert len([r for r in rows
                if r["event"] == "cloud_escalation_authorized"]) == 1
    assert not [r for r in rows if r["event"] == "cloud_escalation_result"]

    rm = governed_analysis.text_assist_router_meta(res)
    assert rm["escalated"] is True and rm["escalation_ok"] is True
    assert rm["result_write_failed"] is True
    # post-call failure: the AUTH write succeeded, so audit_write_failed
    # (the pre-call flag) stays False and result_write_failed carries it
    assert rm["audit_write_failed"] is False


# ---- raw -> router-meta -> canonical: correlation survives shaping ----

def _canonical(rm):
    from swarmtracker_node_ui.canonical_case import _escalation_record
    return _escalation_record(rm)


def test_canonical_generic_success_preserves_correlation(monkeypatch):
    cloud = _VOK()
    _patch_layers(monkeypatch, cloud)
    path = _tmp_ledger(monkeypatch)

    res = governed_analysis.run_text_cloud_assist(
        _ITEM, operator_authorized=True)
    rm = governed_analysis.text_assist_router_meta(res)
    esc = _canonical(rm)

    rows = _read_rows(path)
    auth = [r for r in rows
            if r["event"] == "cloud_escalation_authorized"][0]
    # the generic key shape must not be silently dropped by the helper
    assert esc["authorization_event_id"] == auth["id"] \
        == res["authorization_event_id"]
    assert esc["state"] == "used"
    assert esc["authorized"] is True
    assert esc["auth_write_failed"] is False
    assert esc["result_write_failed"] is False
    assert esc["provider"] == auth["detail"]["provider"]


def test_canonical_pre_call_audit_failure(monkeypatch):
    cloud = _VOK()
    _patch_layers(monkeypatch, cloud)
    ledger_dir = Path(tempfile.mkdtemp()) / "ledger_is_dir"
    ledger_dir.mkdir()
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", ledger_dir)

    res = governed_analysis.run_text_cloud_assist(
        _ITEM, operator_authorized=True)
    assert cloud.generates == 0
    rm = governed_analysis.text_assist_router_meta(res)
    esc = _canonical(rm)

    # fail closed: not escalated, but the operator WAS authorized and
    # the ledger failure must be visible, not reported as refusal
    assert esc["state"] == "auth_write_failed"
    assert esc["authorized"] is True
    assert esc["auth_write_failed"] is True
    assert esc["authorization_event_id"] is None
    assert esc["provider"] is None
    assert esc["result_write_failed"] is False
    assert esc["artifact_custody"].get("sent_to_provider") == []


def test_canonical_post_call_audit_failure(monkeypatch):
    cloud = _VOK()
    _patch_layers(monkeypatch, cloud)
    path = _tmp_ledger(monkeypatch)

    def _fail_result(*a, **k):
        raise keel_events.LedgerWriteError("simulated result-row failure")

    monkeypatch.setattr(governed_analysis, "audit_result", _fail_result)
    res = governed_analysis.run_text_cloud_assist(
        _ITEM, operator_authorized=True)
    rm = governed_analysis.text_assist_router_meta(res)
    esc = _canonical(rm)

    rows = _read_rows(path)
    auth = [r for r in rows
            if r["event"] == "cloud_escalation_authorized"][0]
    assert esc["state"] == "used"
    assert esc["authorization_event_id"] == auth["id"]
    # the authorization row was written before the call; only the
    # result row failed, so auth_write_failed is False and
    # result_write_failed carries the post-call audit failure
    assert esc["auth_write_failed"] is False
    assert esc["result_write_failed"] is True
    assert esc["authorized"] is True


def test_canonical_media_shape_still_supported():
    # the media router_meta key names must keep working unchanged
    rm = {
        "escalated": True, "escalation_ok": True,
        "escalation_provider": "vertex",
        "escalation_auth_event_id": "media-auth-42",
        "escalation_auth_write_failed": False,
        "escalation_result_write_failed": False,
        "custody": {"sent_to_provider": ["shot.png"]},
    }
    esc = _canonical(rm)
    assert esc["state"] == "used"
    assert esc["authorization_event_id"] == "media-auth-42"
    assert esc["auth_write_failed"] is False
    assert esc["result_write_failed"] is False

    rm["escalation_auth_write_failed"] = True
    rm["escalation_auth_event_id"] = None
    rm["escalation_ok"] = False
    rm["escalated"] = False
    esc = _canonical(rm)
    assert esc["state"] == "auth_write_failed"
    assert esc["auth_write_failed"] is True
    assert esc["authorized"] is True


# ---- exported Markdown/briefing must carry the result-audit warning ----

_WARNING_MARK = "AUDIT WARNING"


def test_generic_markdown_export_warns_on_result_write_failure():
    from swarmtracker_node_ui.app import _generic_report_markdown
    job = {"report_id": "rep-1", "workflow": "wf",
           "executed_mode": "governed", "cloud_used": True,
           "report": {"fused_verdict": "v", "overall_risk": "low",
                      "overall_confidence": 70, "attribution": [],
                      "limitations": [], "recommendations": []},
           "cloud_assist": {"text": "bounded supplement"},
           "router_meta": {"escalated": True, "escalation_ok": True,
                           "escalation_provider": "vertex",
                           "result_write_failed": True,
                           "authorization_event_id": "auth-9"}}
    md = _generic_report_markdown(job)
    assert _WARNING_MARK in md
    assert "auth-9" in md
    assert "not retried to repair the audit record" in md


def test_briefing_exports_warn_on_result_write_failure():
    from swarmtracker_node_ui.app import (_briefing_markdown,
                                          _briefing_html)
    job = {"report_id": "rep-2", "title": "T", "executed_mode": "governed",
           "report": {"fused_verdict": "v", "overall_risk": "low",
                      "overall_confidence": 70, "attribution": [],
                      "limitations": [], "recommendations": []},
           "router_meta": {"escalated": True, "escalation_ok": True,
                           "escalation_provider": "vertex",
                           "result_write_failed": True,
                           "authorization_event_id": "auth-11"}}
    md = _briefing_markdown(job)
    assert _WARNING_MARK in md
    assert "auth-11" in md
    html = _briefing_html(job)
    assert _WARNING_MARK in html
    assert "auth-11" in html


def test_media_markdown_export_warns_on_result_write_failure():
    from swarmtracker_node_ui.app import _report_markdown
    job = {"report_id": "rep-3", "media_label": "clip.mp4",
           "executed_mode": "governed", "cloud_used": True,
           "assessment": {}, "provenance": {},
           "router_meta": {"escalated": True, "escalation_ok": True,
                           "escalation_result_write_failed": True,
                           "escalation_auth_event_id": "media-auth-7"}}
    md = _report_markdown(job)
    assert _WARNING_MARK in md
    assert "media-auth-7" in md
    assert "not retried to repair the audit record" in md


# ---- media path: run_media_job must honor the same mandatory ledger ----

from swarmtracker_node_ui import media_runner  # noqa: E402

_MA = {"assessment_conclusion": "no_material_indicators_observed",
       "conclusion_rationale": "r",
       "confidence_by_domain": {"technical": {"confidence": 60,
                                              "basis": "b"}},
       "visual_findings": {"frame_observations": [
           {"observation": "test pattern grid visible"}]},
       "audio_findings": {"transcript": "sine tone"}}


class _MediaCloud:
    """Stands in for the Gemini escalation target; counts generates."""

    provider = "vertex"

    def __init__(self, ok=True):
        self.ok = ok
        self.generates = 0

    def generate_multimodal(self, prompt, parts):
        self.generates += 1
        if not self.ok:
            return {"ok": False, "response": "", "error": "quota"}
        return {"ok": True, "provider": "vertex", "response": json.dumps(
            {"risk_level": "low", "confidence": 40, "verdict": "v",
             "signals": [], "recommended_action": "r",
             "media_assessment": _MA})}


def _media_video(tmp_path):
    path, err = media_runner.synthesize_demo_media(str(tmp_path))
    assert err is None
    return path


def _fake_router(monkeypatch, cloud):
    monkeypatch.setattr(
        media_runner, "_escalation_router",
        lambda gen, sel=None, *, operator_authorized=False: (
            media_runner.core_bridge.EscalatingRouter(gen, cloud), ""))


def _media_gen(prompt):
    return {"ok": True, "event_id": "evt-m", "response": json.dumps(
        {"risk_level": "low", "confidence": 10, "verdict": "v",
         "signals": [], "recommended_action": "r"})}


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_media_auth_ledger_failure_blocks_generate(tmp_path, monkeypatch):
    """Mandatory auth row write fails BEFORE any cloud generate:
    escalation is not attempted, job continues Local Only with an
    explicit reason — never mislabeled as provider failure or denial."""
    cloud = _MediaCloud()
    _fake_router(monkeypatch, cloud)
    path = _tmp_ledger(monkeypatch)

    def _boom(event, detail=None):
        raise keel_events.LedgerWriteError("simulated auth write failure")

    monkeypatch.setattr(keel_events, "append_event_mandatory", _boom)

    r = media_runner.run_media_job(_media_video(tmp_path), "v.mp4",
                                   generate_fn=_media_gen, escalate=True)
    assert r["ok"] is True            # job continues, local only
    assert cloud.generates == 0       # ZERO cloud SDK invocations
    rm = r["router_meta"]
    assert rm["escalated"] is False   # fail-closed local-only shaping
    assert rm["escalation_auth_event_id"] is None
    assert rm["escalation_auth_write_failed"] is True
    assert rm["sent_artifacts"] == []
    assert rm["custody"]["sent_to_provider"] == []
    assert "authorization ledger write failed" in \
        (rm["escalation_reason"] or "")
    canonical = _canonical(rm)
    assert canonical["auth_write_failed"] is True
    assert "authorization ledger write failed" in canonical["reason"]
    # no persisted auth row, and the failure itself is recorded (fail-soft)
    rows = _read_rows(path)
    assert not [x for x in rows if x["event"] == "media_escalation_authorized"]
    assert [x for x in rows
            if x["event"] == "media_escalation_auth_write_failed"]


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_media_auth_row_precedes_generate_and_result_correlates(
        tmp_path, monkeypatch):
    """Happy path: the media_escalation_authorized row is persisted
    BEFORE the cloud generate runs, and the post-call result row
    correlates back to that auth row's id."""
    cloud = _MediaCloud()
    _fake_router(monkeypatch, cloud)
    path = _tmp_ledger(monkeypatch)

    real = keel_events.append_event_mandatory
    order = []

    def _spy(event, detail=None):
        order.append(("auth", event))
        return real(event, detail)

    monkeypatch.setattr(keel_events, "append_event_mandatory", _spy)

    class _OrderCloud(_MediaCloud):
        def generate_multimodal(self, prompt, parts):
            auth_rows = [row for row in _read_rows(path)
                         if row["event"] == "media_escalation_authorized"]
            assert len(auth_rows) == 1
            assert auth_rows[0]["detail"]["provider"] == "vertex"
            order.append(("cloud", "generate"))
            return super().generate_multimodal(prompt, parts)

    cloud = _OrderCloud()
    _fake_router(monkeypatch, cloud)

    r = media_runner.run_media_job(_media_video(tmp_path), "v.mp4",
                                   generate_fn=_media_gen, escalate=True)
    assert r["ok"] is True and cloud.generates == 1
    # the auth write strictly precedes the first cloud generate
    assert order == [("auth", "media_escalation_authorized"),
                     ("cloud", "generate"),
                     ("auth", "media_escalation_result")]

    rows = _read_rows(path)
    auth = [x for x in rows if x["event"] == "media_escalation_authorized"]
    results = [x for x in rows if x["event"] == "media_escalation_result"]
    assert len(auth) == 1 and len(results) == 1
    assert results[0]["detail"].get("authorization_event_id") \
        == auth[0]["id"], "result row must correlate with its auth row"
    assert results[0]["detail"].get("provider") \
        == auth[0]["detail"].get("provider")
    rm = r["router_meta"]
    assert rm["escalated"] is True and rm["escalation_ok"] is True
    assert rm["escalation_auth_event_id"] == auth[0]["id"]
    assert rm["escalation_auth_write_failed"] is False
    assert rm["escalation_result_write_failed"] is False


@pytest.mark.skipif(not media_runner.core_bridge.media_tools.available(
    "ffmpeg"), reason="ffmpeg not available")
def test_media_result_write_failure_is_honest_no_retry(
        tmp_path, monkeypatch):
    """Post-call result-row write fails: the generate already happened,
    is reported truthfully, and is NEVER rerun to repair the audit."""
    cloud = _MediaCloud()
    _fake_router(monkeypatch, cloud)
    path = _tmp_ledger(monkeypatch)

    real = keel_events.append_event_mandatory
    calls = []

    def _fail_on_result(event, detail=None):
        calls.append(event)
        if event == "media_escalation_result":
            raise keel_events.LedgerWriteError(
                "simulated result-row failure")
        return real(event, detail)

    monkeypatch.setattr(keel_events, "append_event_mandatory",
                        _fail_on_result)

    r = media_runner.run_media_job(_media_video(tmp_path), "v.mp4",
                                   generate_fn=_media_gen, escalate=True)
    # exactly one generate: no retry, no hidden rerun
    assert cloud.generates == 1
    assert calls.count("media_escalation_authorized") == 1
    assert r["ok"] is True
    rm = r["router_meta"]
    assert rm["escalated"] is True and rm["escalation_ok"] is True
    assert rm["escalation_auth_write_failed"] is False
    assert rm["escalation_result_write_failed"] is True
    assert rm["escalation_auth_event_id"]
    # the auth row persisted; no result row exists in the ledger
    rows = _read_rows(path)
    assert len([x for x in rows
                if x["event"] == "media_escalation_authorized"]) == 1
    assert not [x for x in rows if x["event"] == "media_escalation_result"]


# ---- partial-tail regression: a prior failed write must not let the
# ---- NEXT mandatory append falsely "succeed" (unreadable auth row) ----

@pytest.mark.parametrize("tail", [
    '{"id":"partial",',                      # truncated mid-row, no newline
    '{"id":"broken"\n',                      # complete line, invalid JSON
])
def test_partial_tail_fails_closed_before_cloud_generate(
        tmp_path, monkeypatch, tail):
    """Root-reproduced blocker: with an unterminated trailing row from
    a prior short/failed write, append_event_mandatory used to return
    an id after fsync while read_events contained ZERO matching rows
    (the new JSON fused onto the broken line). A mandatory append must
    fail closed instead — and a cloud authorization must never pass on
    a row that is not separately readable."""
    cloud = _MediaCloud()
    _fake_router(monkeypatch, cloud)
    path = tmp_path / "ledger.jsonl"
    path.write_text(tail)
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", path)

    with pytest.raises(keel_events.LedgerWriteError) as ei:
        keel_events.append_event_mandatory(
            "media_escalation_authorized", {"provider": "gemini"})
    msg = str(ei.value)
    assert ("unterminated" in msg.lower()) or ("unreadable" in msg.lower())
    assert ("prior failed write" in msg.lower())
    # no bytes written by the FAILED MANDATORY append, nothing truncated
    assert path.read_text() == tail
    # the poisoned tail did not become a false authorization
    assert cloud.generates == 0
    # the fail-soft path is unchanged by the tail (still fail-soft):
    # it appends unconditionally and returns an id
    soft_id = keel_events.append_event("fail_soft_still_works", {})
    assert soft_id.startswith("kme-")
    assert path.read_text().startswith(tail)


def test_partial_tail_repair_restores_mandatory_appends(tmp_path,
                                                        monkeypatch):
    """After the operator repairs the tail (newline + removable junk),
    the mandatory path works again and the row is separately readable."""
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"id":"partial",')
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", path)
    with pytest.raises(keel_events.LedgerWriteError):
        keel_events.append_event_mandatory("x", None)
    # repair: archive the broken fragment and start clean
    path.write_text("")
    eid = keel_events.append_event_mandatory("media_escalation_authorized",
                                             {"provider": "gemini"})
    rows = [r for r in _read_rows(path) if r["event"] ==
            "media_escalation_authorized"]
    assert len(rows) == 1 and rows[0]["id"] == eid


@pytest.mark.parametrize("fault", ["short_write", "write_error", "flush_error"])
def test_actual_append_io_failure_blocks_sdk(tmp_path, monkeypatch, fault):
    """Exercise real append flow, not an exception stub at its entry."""
    target = tmp_path / "io-failure.jsonl"
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", target)
    cloud = _VOK()
    _patch_layers(monkeypatch, cloud)
    original_open = Path.open

    class FaultyAppend:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, line):
            if fault == "write_error":
                raise OSError("synthetic write failure")
            if fault == "short_write":
                return self.stream.write(line[:-1])
            return self.stream.write(line)

        def flush(self):
            if fault == "flush_error":
                raise OSError("synthetic flush failure")
            return self.stream.flush()

    def open_with_fault(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        if path == target and mode == "a":
            return FaultyAppend(stream)
        return stream

    monkeypatch.setattr(Path, "open", open_with_fault)
    result = governed_analysis.run_text_cloud_assist(
        _ITEM, operator_authorized=True)
    assert cloud.generates == 0
    assert result["audit_write_failed"] is True
    assert result["provider_attempted"] is False
    assert result["authorization_event_id"] is None
    assert result["sent_manifest"] == []
    if fault == "short_write":
        # A genuine prior short write must not poison a later success.
        monkeypatch.setattr(Path, "open", original_open)
        second = governed_analysis.run_text_cloud_assist(
            _ITEM, operator_authorized=True)
        assert cloud.generates == 0
        assert second["audit_write_failed"] is True
        assert "partial" in second["reason"].lower()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_mandatory_nonfinite_data_leaves_ledger_untouched(
        tmp_path, monkeypatch, value):
    target = tmp_path / "nonfinite.jsonl"
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", target)
    with pytest.raises(keel_events.LedgerWriteError):
        keel_events.append_event_mandatory("synthetic", {"metric": value})
    assert not target.exists()


def test_local_events_keep_default_str_and_fail_soft(tmp_path, monkeypatch):
    target = tmp_path / "local-only.jsonl"
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", target)
    event_id = keel_events.append_event("local_processing",
                                       {"path": Path("synthetic")})
    rows = _read_rows(target)
    assert len(rows) == 1 and rows[0]["id"] == event_id
    assert rows[0]["detail"]["path"] == "synthetic"
    original_open = Path.open

    def unwritable(path, mode="r", *args, **kwargs):
        if path == target and mode == "a":
            raise OSError("synthetic local ledger unavailable")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unwritable)
    # Ordinary local-event logging retains its existing best-effort API.
    assert keel_events.append_event("local_processing", {})
    assert len(_read_rows(target)) == 1


def test_complete_large_previous_row_does_not_block_authorization(
        tmp_path, monkeypatch):
    target = tmp_path / "large-valid-row.jsonl"
    previous = {"id": "synthetic-previous", "detail": {"payload": "x" * 100000}}
    target.write_text(json.dumps(previous) + "\n")
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", target)
    event_id = keel_events.append_event_mandatory("synthetic", {})
    rows = _read_rows(target)
    assert len(rows) == 2
    assert rows[0] == previous
    assert rows[1]["id"] == event_id


def test_deeply_malformed_existing_tail_fails_closed(tmp_path, monkeypatch):
    target = tmp_path / "deep-tail.jsonl"
    target.write_text("[" * 1500 + "0" + "]" * 1500 + "\n")
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", target)
    cloud = _VOK()
    _patch_layers(monkeypatch, cloud)
    result = governed_analysis.run_text_cloud_assist(
        _ITEM, operator_authorized=True)
    assert cloud.generates == 0
    assert result["audit_write_failed"] is True
    assert result["provider_attempted"] is False
    assert result["authorization_event_id"] is None
    assert result["sent_manifest"] == []
    assert "unreadable" in result["reason"].lower()


@pytest.mark.parametrize("result_log_fails", [False, True])
def test_raising_provider_still_records_outcome_without_retry(
        tmp_path, monkeypatch, result_log_fails):
    target = tmp_path / "raising-provider.jsonl"
    monkeypatch.setattr(keel_events, "MEDIA_LEDGER_PATH", target)

    class RaisingCloud(_VOK):
        def generate(self, prompt):
            self.generates += 1
            raise RuntimeError("synthetic transport exception")

    cloud = RaisingCloud()
    _patch_layers(monkeypatch, cloud)
    real_append = keel_events.append_event_mandatory

    def append_with_optional_result_failure(event, detail=None):
        if result_log_fails and event == "cloud_escalation_result":
            raise keel_events.LedgerWriteError("synthetic result log failure")
        return real_append(event, detail)

    monkeypatch.setattr(keel_events, "append_event_mandatory",
                        append_with_optional_result_failure)
    result = governed_analysis.run_text_cloud_assist(
        _ITEM, operator_authorized=True)
    assert cloud.generates == 1
    assert result["ok"] is False
    assert result["provider_attempted"] is True
    assert "RuntimeError" in result["error"]
    assert result["audit_write_failed"] is result_log_fails
    rows = _read_rows(target)
    auth = [r for r in rows if r["event"] == "cloud_escalation_authorized"]
    outcomes = [r for r in rows if r["event"] == "cloud_escalation_result"]
    assert len(auth) == 1
    assert len(outcomes) == (0 if result_log_fails else 1)
    if outcomes:
        assert outcomes[0]["detail"]["authorization_event_id"] == auth[0]["id"]
        assert outcomes[0]["detail"]["ok"] is False
    meta = governed_analysis.text_assist_router_meta(result)
    assert meta["result_write_failed"] is result_log_fails
    assert meta["provider_error"] == result["error"]

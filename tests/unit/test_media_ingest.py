"""
Tests for verity_core.media_ingest (v0.5.0 S2): safe tool invocation,
probe/exif parsing (mocked), the attribution-oriented evidence package,
and real-ffprobe integration on synthesized fixtures when available.
Offline: no network; exiftool tests mock the binary (not present in CI).
"""
import json
import os
import struct
import time
import wave
import zlib

import pytest

from verity_core.media_ingest import exif, package, probe, tools


# ---------------- fixture media (synthesized in-test; no binaries in git) ---
def make_wav(path, seconds=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000 * seconds)
    return str(path)


def make_png(path, width=4, height=4):
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + \
            struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80" * (3 * width)
                   for _ in range(height))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height,
                                        8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    return str(path)


HAVE_FFPROBE = tools.available("ffprobe")


# ---------------- tools.py ----------------
def test_run_tool_rejects_non_list():
    assert tools.run_tool("ffprobe -version")["ok"] is False


def test_run_tool_missing_binary_reports_tool_missing():
    out = tools.run_tool(["definitely-not-a-real-tool-xyz", "-v"])
    assert out["ok"] is False and out["tool_missing"] is True


def test_run_tool_timeout(tmp_path):
    out = tools.run_tool(["sleep", "5"], timeout_s=1)
    assert out["ok"] is False and "timed out" in out["error"]


def test_sha256_file(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc")
    assert tools.sha256_file(str(p)) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


# ---------------- probe.py (mocked + integration) ----------------
FFPROBE_JSON = {
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2",
               "format_long_name": "QuickTime / MOV",
               "duration": "12.5", "size": "1048576", "bit_rate": "670000",
               "nb_streams": "2",
               "tags": {"creation_time": "2026-07-01T10:00:00Z",
                        "encoder": "Lavf58.76.100"}},
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1280,
         "height": 720, "avg_frame_rate": "30000/1001", "pix_fmt":
         "yuv420p", "nb_frames": "375"},
        {"codec_type": "audio", "codec_name": "aac", "channels": 2,
         "sample_rate": "44100"},
    ],
}


def test_probe_parses_ffprobe_json(monkeypatch):
    monkeypatch.setattr(tools, "run_json_tool",
                        lambda argv, timeout_s=60: {"ok": True,
                                                    "data": FFPROBE_JSON})
    monkeypatch.setattr(tools, "tool_version", lambda n: "ffprobe test")
    out = probe.probe_media("/nonexistent")
    assert out["ok"]
    assert out["container"]["duration_s"] == 12.5
    assert out["streams"][0]["fps"] == pytest.approx(29.97, abs=0.01)
    assert out["embedded_tags"]["encoder"] == "Lavf58.76.100"


def test_probe_failure_is_honest(monkeypatch):
    monkeypatch.setattr(tools, "run_json_tool",
                        lambda argv, timeout_s=60:
                        {"ok": False, "error": "boom", "returncode": 1})
    out = probe.probe_media("/nonexistent")
    assert out["ok"] is False and "boom" in out["error"]


@pytest.mark.skipif(not HAVE_FFPROBE, reason="ffprobe not installed")
def test_probe_real_wav_and_png(tmp_path):
    wav = make_wav(tmp_path / "s.wav")
    out = probe.probe_media(wav)
    assert out["ok"] and out["streams"][0]["type"] == "audio"
    png = make_png(tmp_path / "s.png")
    out2 = probe.probe_media(png)
    assert out2["ok"] and out2["streams"][0]["width"] == 4


@pytest.mark.skipif(not HAVE_FFPROBE, reason="ffprobe not installed")
def test_probe_malformed_file_fails_honestly(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"\x00" * 64)
    out = probe.probe_media(str(bad))
    assert out["ok"] is False


# ---------------- exif.py (mocked; exiftool absent in CI) ----------------
EXIFTOOL_JSON = [{
    "SourceFile": "x.jpg", "CreateDate": "2026:07:01 10:00:00",
    "ModifyDate": "2026:07:02 09:30:00", "Make": "Acme",
    "Model": "Cam 9", "Software": "EditSuite 3.1",
    "GPSLatitude": "1 deg", "ImageWidth": 1280, "MIMEType": "image/jpeg"}]


def test_exif_parses_and_groups(monkeypatch):
    monkeypatch.setattr(tools, "run_json_tool",
                        lambda argv, timeout_s=60: {"ok": True,
                                                    "data": EXIFTOOL_JSON})
    monkeypatch.setattr(tools, "tool_version", lambda n: "13.0")
    out = exif.extract_exif("/x.jpg")
    assert out["ok"]
    assert out["timestamps"]["CreateDate"] == "2026:07:01 10:00:00"
    assert out["device_software"]["Software"] == "EditSuite 3.1"
    assert out["gps_present"] is True


def test_exif_tool_missing_reported(monkeypatch):
    monkeypatch.setattr(tools, "run_json_tool",
                        lambda argv, timeout_s=60:
                        {"ok": False, "error": "exiftool not available",
                         "tool_missing": True})
    out = exif.extract_exif("/x.jpg")
    assert out["ok"] is False and out["tool_missing"] is True


# ---------------- package.py: evidence-and-attribution orientation ----------
@pytest.fixture
def pkg(tmp_path):
    p = package.new_package(
        "upload", "specimen.mp4",
        declared={"original_filename": "specimen.mp4",
                  "claimed_date": "2026-07-05"})
    src = tmp_path / "source.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\xab" * 100)
    art = package.add_artifact(p, "source_media", path=str(src),
                               size_bytes=116, step="intake",
                               label="uploaded source")
    return p, art, str(src)


def test_package_source_artifact_records_required_fields(pkg):
    p, art_id, src = pkg
    a = p["artifacts"][0]
    assert a["artifact_id"] == art_id and a["role"] == "source"
    assert len(a["sha256"]) == 64
    assert a["created_by_step"] == "intake"
    assert p["source"]["kind"] == "upload"
    assert p["source"]["acquisition_time_utc"].endswith("Z")


def test_derived_artifact_chain_and_hash(pkg, tmp_path):
    p, art_id, _ = pkg
    frame = tmp_path / "f1.jpg"
    frame.write_bytes(b"\xff\xd8\xff" + b"\x01" * 50)
    fid = package.add_artifact(p, "frame", path=str(frame), parent=art_id,
                               step="frame_extraction",
                               tool={"name": "ffmpeg", "version": "6"})
    d = [a for a in p["artifacts"] if a["artifact_id"] == fid][0]
    assert d["role"] == "derived" and d["parent_artifact"] == art_id
    assert len(d["sha256"]) == 64 and d["tool"]["name"] == "ffmpeg"


def test_probe_and_exif_fold_into_source_embedded(pkg, monkeypatch):
    p, art_id, _ = pkg
    monkeypatch.setattr(tools, "tool_version", lambda n: "t")
    package.attach_probe(p, {
        "ok": True, "tool": {"name": "ffprobe", "version": "6"},
        "container": {"format_name": "mp4", "duration_s": 12.5,
                      "size_bytes": 116},
        "streams": [{"type": "video"}],
        "embedded_tags": {"encoder": "Lavf58"}}, art_id)
    package.attach_exif(p, {
        "ok": True, "tool": {"name": "exiftool", "version": "13"},
        "timestamps": {"CreateDate": "2026:07:01 10:00:00",
                       "ModifyDate": "2026:07:02 10:00:00"},
        "device_software": {"Software": "EditSuite"},
        "context": {}, "gps_present": False, "total_tags": 40}, art_id)
    se = p["metadata_by_origin"]["source_embedded"]
    assert se["container"]["format_name"] == "mp4"
    assert se["embedded_timestamps"]["CreateDate"].startswith("2026:07:01")
    assert [e["step"] for e in p["processing_events"]] == [
        "structural_probe", "embedded_metadata_extraction"]
    assert p["processing_events"][0]["tool_version"] == "6"


def test_observations_distinguish_direct_from_inference(pkg, monkeypatch):
    p, art_id, _ = pkg
    package.attach_probe(p, {
        "ok": True, "tool": {"name": "ffprobe", "version": "6"},
        "container": {"format_name": "mp4", "duration_s": 1,
                      "size_bytes": 116},
        "streams": [], "embedded_tags": {"encoder": "Lavf58"}}, art_id)
    package.attach_exif(p, {
        "ok": True, "tool": {"name": "exiftool", "version": "13"},
        "timestamps": {"CreateDate": "2026:07:01 10:00:00",
                       "ModifyDate": "2026:07-02 10:00:00"},
        "device_software": {"Software": "EditSuite"},
        "context": {}, "gps_present": False, "total_tags": 4}, art_id)
    package.derive_observations(p, declared_extension="mp4")
    bases = {o["basis"] for o in p["observations"]}
    assert bases == {"direct_observation", "inference"}
    inf = [o for o in p["observations"] if o["basis"] == "inference"][0]
    # inferences preserve uncertainty; never proof-of-manipulation claims
    assert "does not establish" in inf["statement"]
    for o in p["observations"]:
        assert "proves" not in o["statement"].lower()


def test_claimed_vs_observed_flags_day_plus_divergence(pkg):
    p, art_id, _ = pkg
    package.attach_exif(p, {
        "ok": True, "tool": {"name": "exiftool", "version": "13"},
        "timestamps": {"CreateDate": "2026:07:01 10:00:00"},
        "device_software": {}, "context": {}, "gps_present": False,
        "total_tags": 3}, art_id)
    package.derive_observations(p)   # claimed_date 2026-07-05 in fixture
    cvo = p["attribution_relevance"]["claimed_vs_observed"]
    assert cvo and "forgeable" in cvo[0]


def test_missing_tools_recorded_not_fatal(pkg):
    p, art_id, _ = pkg
    package.attach_probe(p, {"ok": False, "tool": {"name": "ffprobe",
                                                   "version": None},
                             "error": "ffprobe not available",
                             "tool_missing": True}, art_id)
    package.attach_exif(p, {"ok": False, "tool": {"name": "exiftool",
                                                  "version": None},
                            "error": "exiftool not available",
                            "tool_missing": True}, art_id)
    assert p["tooling"]["ffprobe"]["available"] is False
    assert any("not available" in (e["note"] or "")
               for e in p["processing_events"])
    assert any("was not examined" in o["statement"]
               for o in p["observations"])


def test_absent_timestamps_is_evidence_with_alternatives(pkg):
    p, art_id, _ = pkg
    package.attach_exif(p, {
        "ok": True, "tool": {"name": "exiftool", "version": "13"},
        "timestamps": {}, "device_software": {}, "context": {},
        "gps_present": False, "total_tags": 2}, art_id)
    package.derive_observations(p)
    obs = [o for o in p["observations"]
           if "No embedded" in o["statement"]][0]
    assert "also consistent" in obs["attribution_relevance"]


def test_finalize_records_cleanup_everywhere(pkg):
    p, art_id, _ = pkg
    package.finalize(p, "completed", deleted_artifact_ids=[art_id])
    assert p["cleanup"]["status"] == "completed"
    assert p["cleanup"]["deleted_artifacts"] == [art_id]
    assert p["processing_events"][-1]["step"] == "temporary_media_cleanup"
    assert p["metadata_by_origin"]["processing_generated"][
        "cleanup_status"] == "completed"


def test_package_carries_caveats_and_is_json_serializable(pkg):
    p, _, _ = pkg
    assert any("not proof of authorship or manipulation" in c
               for c in p["caveats"])
    json.dumps(p)   # must round-trip for state.json / bundle embedding


def test_bundle_additive_fields_roundtrip():
    from verity_core.artifacts.media import MediaEvidenceBundle, \
        summarize_bundle
    b = MediaEvidenceBundle(extraction={"schema": package.SCHEMA},
                            cleanup_status="completed",
                            source_kind="upload")
    d = b.to_dict()
    b2 = MediaEvidenceBundle.from_dict(d)
    assert b2.extraction["schema"] == package.SCHEMA
    assert summarize_bundle(b2)["has_extraction"] is True
    # pre-0.5.0 dicts (no new keys) still load
    legacy = {k: v for k, v in d.items()
              if k not in ("extraction", "processing_events",
                           "cleanup_status", "source_kind",
                           "limits_applied")}
    assert MediaEvidenceBundle.from_dict(legacy).extraction == {}

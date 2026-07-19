"""
S6 core tests: evidence-package internal-consistency validation and
rejected-artifact provenance retention.
"""
from verity_core.media_ingest import package


def _valid_pkg():
    p = package.new_package("upload", "x.mp4")
    src = package.add_artifact(p, "source_media", sha256="a" * 64,
                               size_bytes=1, step="intake")
    fid = package.add_artifact(p, "frame", sha256="b" * 64, parent=src,
                               step="frame_extraction",
                               tool={"name": "ffmpeg", "version": "6"})
    package.record_event(p, "frame_extraction", "ok",
                         inputs=[src], outputs=[fid])
    package.add_model_observation(p, "obs", model="m", modality="visual",
                                  competing_explanations=["alt"],
                                  artifact_ids=[fid])
    package.finalize(p, "completed", deleted_artifact_ids=[src, fid])
    return p, src, fid


def test_valid_package_passes():
    p, _, _ = _valid_pkg()
    assert package.validate(p) == []


def test_missing_parent_detected():
    p, _, fid = _valid_pkg()
    for a in p["artifacts"]:
        if a["artifact_id"] == fid:
            a["parent_artifact"] = "mart-nonexistent"
    issues = package.validate(p)
    assert any("does not exist" in i for i in issues)


def test_malformed_hash_detected():
    p, src, _ = _valid_pkg()
    p["artifacts"][0]["sha256"] = "zz"
    assert any("malformed sha256" in i for i in package.validate(p))


def test_event_reference_to_missing_artifact_detected():
    p, _, _ = _valid_pkg()
    package.record_event(p, "bogus_step", "ok", inputs=["mart-ghost"])
    assert any("missing artifact mart-ghost" in i
               for i in package.validate(p))


def test_invalid_observation_basis_detected():
    p, _, _ = _valid_pkg()
    p["observations"][0]["basis"] = "vibes"
    assert any("invalid basis" in i for i in package.validate(p))


def test_cleanup_reference_detected():
    p, _, _ = _valid_pkg()
    p["cleanup"]["deleted_artifacts"].append("mart-ghost")
    assert any("cleanup references missing" in i
               for i in package.validate(p))


def test_model_observation_linkage_detected():
    p, _, _ = _valid_pkg()
    p["metadata_by_origin"]["model_observations"]["visual"][0][
        "observation_id"] = "obs-ghost"
    assert any("missing observation" in i for i in package.validate(p))


def test_duplicate_artifact_ids_detected():
    p, _, _ = _valid_pkg()
    p["artifacts"].append(dict(p["artifacts"][0]))
    assert any("duplicate" in i for i in package.validate(p))


def test_validation_never_raises():
    assert package.validate({"artifacts": "not-a-list"})   # issues, no raise


# ---- rejected-artifact provenance ----
def test_rejection_preserves_provenance():
    p = package.new_package("upload", "bad.mp4")
    package.add_artifact(p, "source_media", sha256="c" * 64,
                         size_bytes=9, step="intake")
    package.record_rejection(p, "file could not be parsed as valid media",
                             stage="structural_probe",
                             sha256="c" * 64,
                             tool_outcome="ffprobe rc=1")
    r = p["rejection"]
    assert r["sha256_received"] == "c" * 64
    assert r["reason"].startswith("file could not be parsed")
    assert r["stage"] == "structural_probe"
    assert r["tool_outcome"] == "ffprobe rc=1"
    assert p["processing_events"][-1]["step"] == "rejection"
    assert any("was rejected at structural_probe" in o["statement"]
               for o in p["observations"])
    assert package.validate(p) == []       # rejected package still valid

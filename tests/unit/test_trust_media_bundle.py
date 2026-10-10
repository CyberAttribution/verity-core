"""Unit tests for verity_core.trust.media_bundle (consumer bridge).

All tests are offline: packages are built from real bytes on disk with real
path-based hashes; no network, external binaries, or golden values for
UUIDs/timestamps. Assertions compare relative behavior only. Probe metadata
attached in tests, where present, is synthetic fixture data exercising the
constructor APIs, not real tool output.
"""

import copy
import hashlib
import json
import os

import pytest

from verity_core.media_ingest import package
from verity_core.trust import media_bundle

_SOURCE_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\xab" * 100


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _write_source(tmp_path):
    source_path = tmp_path / "specimen.mp4"
    source_path.write_bytes(_SOURCE_BYTES)
    return str(source_path)


def _build_package(tmp_path):
    """Real media_evidence_package built through the public constructor
    APIs: the artifact sha256 is a real file hash."""
    source_path = _write_source(tmp_path)
    pkg = package.new_package(
        "upload",
        "specimen.mp4",
        declared={"original_filename": "specimen.mp4"},
    )
    artifact_id = package.add_artifact(
        pkg,
        "source_media",
        path=source_path,
        size_bytes=len(_SOURCE_BYTES),
        step="intake",
        label="uploaded source",
    )
    package.attach_probe(
        pkg,
        {
            "ok": True,
            "tool": {"name": "ffprobe", "version": "6"},
            "container": {"format_name": "mp4", "duration_s": 12.5,
                          "size_bytes": len(_SOURCE_BYTES)},
            "streams": [{"type": "video"}],
            "embedded_tags": {"encoder": "Lavf58"},
        },
        artifact_id,
    )
    package.derive_observations(pkg, declared_extension="mp4")
    package.derive_recommended_corroboration(pkg)
    package.finalize(pkg, "completed", deleted_artifact_ids=[artifact_id])
    return pkg


def _bundle_path(tmp_path):
    return str(tmp_path / "specimen.trust.json")


def _bind_and_save(tmp_path, pkg=None):
    pkg = pkg if pkg is not None else _build_package(tmp_path)
    rec = media_bundle.bind_package(pkg)
    path = _bundle_path(tmp_path)
    media_bundle.save_bundle(path, pkg, rec)
    return path, pkg, rec


def _rename_artifact_id(pkg, new_id):
    """Rename the source artifact id everywhere package.validate checks
    consistency, so the renamed package still validates."""
    old_id = pkg["artifacts"][0]["artifact_id"]
    pkg["artifacts"][0]["artifact_id"] = new_id
    for event in pkg["processing_events"]:
        event["input_artifacts"] = [
            new_id if a == old_id else a for a in event.get("input_artifacts", [])
        ]
        event["output_artifacts"] = [
            new_id if a == old_id else a for a in event.get("output_artifacts", [])
        ]
    pkg["cleanup"]["deleted_artifacts"] = [
        new_id if a == old_id else a for a in pkg["cleanup"]["deleted_artifacts"]
    ]


def _bundle_dict(pkg, rec):
    return {"schema": media_bundle.BUNDLE_SCHEMA, "package": pkg, "record": rec}


# --- identity vs digest ------------------------------------------------------


def test_artifact_sha256_is_real_file_hash(tmp_path):
    pkg = _build_package(tmp_path)
    artifact = next(a for a in pkg["artifacts"] if a["role"] == "source")
    assert artifact["sha256"] == _sha256_bytes(_SOURCE_BYTES)


def test_identity_is_full_canonical_json(tmp_path):
    pkg = _build_package(tmp_path)
    expected = hashlib.sha256(
        json.dumps(pkg, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert media_bundle.compute_identity(pkg) == expected


def test_altered_non_rendered_field_changes_identity_not_digest(tmp_path):
    pkg = _build_package(tmp_path)
    before_digest = package.evidence_digest(pkg)
    before_identity = media_bundle.compute_identity(pkg)
    altered = copy.deepcopy(pkg)
    altered["caveats"] = altered["caveats"] + ["added after binding"]
    altered["metadata_by_origin"]["processing_generated"]["package_finalized_utc"] = "2000-01-01T00:00:00Z"
    assert package.evidence_digest(altered) == before_digest
    assert media_bundle.compute_identity(altered) != before_identity


# --- binding construction ----------------------------------------------------


def test_bind_roundtrip(tmp_path):
    path, pkg, rec = _bind_and_save(tmp_path)
    loaded = media_bundle.load_bundle(path)
    assert loaded["package"] == pkg
    assert loaded["record"] == rec
    assert loaded["schema"] == media_bundle.BUNDLE_SCHEMA
    report = media_bundle.verify_bundle(loaded)
    assert report["verified"] is True
    assert report["package_valid"] is True
    assert report["chain_valid"] is True
    assert report["binding_valid"] is True
    assert report["issues"] == {"envelope": [], "package": [], "chain": [],
                                "binding": []}


def test_binding_lives_in_entry_payload(tmp_path):
    _, pkg, rec = _bind_and_save(tmp_path)
    payload = media_bundle._binding_payload(rec)
    assert payload["package_identity_sha256"] == media_bundle.compute_identity(pkg)
    assert payload["schema"] == package.SCHEMA
    source = next(a for a in pkg["artifacts"] if a["role"] == "source")
    assert payload["source_artifact_id"] == source["artifact_id"]
    assert rec["subject"]["source_sha256"] == _sha256_bytes(_SOURCE_BYTES)


def test_bind_refuses_invalid_package(tmp_path):
    pkg = _build_package(tmp_path)
    del pkg["source"]
    with pytest.raises(ValueError, match="cannot bind an invalid package"):
        media_bundle.bind_package(pkg)


def test_bind_refuses_package_without_source_artifact(tmp_path):
    """No fallback that labels a derived artifact as source."""
    pkg = package.new_package("upload", "x.mp4")
    pkg["artifacts"].append({
        "artifact_id": "mart-derive0000", "kind": "thumbnail",
        "role": "derived", "label": "t", "sha256": "c" * 64,
        "size_bytes": 1, "parent_artifact": None, "created_by_step": "s",
        "tool": None, "recorded_utc": "2026-01-01T00:00:00Z",
    })
    with pytest.raises(ValueError, match="no source artifact with a valid sha256"):
        media_bundle.bind_package(pkg)


# --- save/load guards --------------------------------------------------------


def test_save_refuses_altered_package_with_old_record(tmp_path):
    _, pkg, rec = _bind_and_save(tmp_path)
    altered = copy.deepcopy(pkg)
    altered["caveats"] = altered["caveats"] + ["added later"]
    with pytest.raises(ValueError, match="not bound to this package"):
        media_bundle.save_bundle(str(tmp_path / "other.json"), altered, rec)


def test_save_refuses_tampered_record(tmp_path):
    _, pkg, rec = _bind_and_save(tmp_path)
    tampered = copy.deepcopy(rec)
    for entry in tampered["entries"]:
        if entry.get("entry_type") == "evidence_recorded":
            entry["payload"]["package_identity_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        media_bundle.save_bundle(str(tmp_path / "other.json"), pkg, tampered)


def test_save_refuses_mismatched_record_from_other_package(tmp_path):
    _, pkg, _ = _bind_and_save(tmp_path)
    other_pkg = _build_package(tmp_path)
    other_rec = media_bundle.bind_package(other_pkg)
    with pytest.raises(ValueError, match="not bound to this package"):
        media_bundle.save_bundle(str(tmp_path / "other.json"), pkg, other_rec)


def test_save_load_refuse_occupancy_and_clean_temp(tmp_path):
    path, pkg, rec = _bind_and_save(tmp_path)
    before = os.listdir(tmp_path)
    with pytest.raises(FileExistsError):
        media_bundle.save_bundle(path, pkg, rec)
    assert os.listdir(tmp_path) == before
    media_bundle.save_bundle(path, pkg, rec, overwrite=True)
    assert media_bundle.verify_file(path)["verified"] is True
    assert not any(".tmp-" in name for name in os.listdir(tmp_path))


def test_save_never_replaces_concurrent_destination(tmp_path, monkeypatch):
    """Reproduces the exists-check/link race: a destination created between
    validation and publish must never be replaced."""
    pkg = _build_package(tmp_path)
    rec = media_bundle.bind_package(pkg)
    dest = tmp_path / "race.json"
    assert not dest.exists()
    real_link = os.link

    def racing_link(src, dst, **kwargs):
        dst_path = dst if isinstance(dst, str) else os.fspath(dst)
        with open(dst_path, "wb") as fh:
            fh.write(b"CONCURRENT")
        return real_link(src, dst_path, **kwargs)

    monkeypatch.setattr(os, "link", racing_link)
    with pytest.raises(FileExistsError):
        media_bundle.save_bundle(str(dest), pkg, rec)
    assert dest.read_bytes() == b"CONCURRENT"
    assert not any(".tmp-" in name for name in os.listdir(tmp_path))


def test_load_refuses_malformed_json(tmp_path):
    path, _, _ = _bind_and_save(tmp_path)
    cases = {
        "duplicate keys": '{"schema": "a", "schema": "b"}',
        "NaN": '{"schema": NaN}',
        "overflow float": '{"schema": 1e999}',
        "truncated": '{"schema": "trust_media_bund',
    }
    for label, text in cases.items():
        bad = tmp_path / f"bad-{label.replace(' ', '-')}.json"
        bad.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match="malformed JSON bundle"):
            media_bundle.load_bundle(str(bad))
    not_object = tmp_path / "bad-not-an-object.json"
    not_object.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="bundle must be a JSON object"):
        media_bundle.load_bundle(str(not_object))
    with pytest.raises(FileNotFoundError):
        media_bundle.load_bundle(str(tmp_path / "missing.json"))
    wrong_schema = tmp_path / "wrong-schema.json"
    wrong_schema.write_text(
        '{"schema": "other/v1", "package": {}, "record": {}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bundle schema must be"):
        media_bundle.load_bundle(str(wrong_schema))


# --- verify_bundle verdicts --------------------------------------------------


def test_verify_rejects_unsupported_envelope_schema(tmp_path):
    _, pkg, rec = _bind_and_save(tmp_path)
    report = media_bundle.verify_bundle(
        {"schema": "unsupported/v99", "package": pkg, "record": rec})
    assert report["verified"] is False
    assert report["issues"]["envelope"]
    assert report["binding_valid"] is False
    assert report["issues"]["binding"] == [
        "not evaluated because the bundle envelope is invalid"
    ]
    report = media_bundle.verify_bundle(["not", "an", "object"])
    assert report["verified"] is False
    assert report["issues"]["envelope"]


def test_binding_requires_existing_source_artifact(tmp_path):
    _, pkg, rec = _bind_and_save(tmp_path)
    renamed = copy.deepcopy(pkg)
    _rename_artifact_id(renamed, "mart-renamed000")
    report = media_bundle.verify_bundle(_bundle_dict(renamed, rec))
    assert report["chain_valid"] is True
    assert report["binding_valid"] is False
    assert any("does not exist in the package" in issue
               for issue in report["issues"]["binding"])
    derived_role = copy.deepcopy(pkg)
    derived_role["artifacts"][0]["role"] = "derived"
    report = media_bundle.verify_bundle(_bundle_dict(derived_role, rec))
    assert report["chain_valid"] is True
    assert any("not a source artifact" in issue
               for issue in report["issues"]["binding"])
    wrong_sha = copy.deepcopy(pkg)
    wrong_sha["artifacts"][0]["sha256"] = "d" * 64
    report = media_bundle.verify_bundle(_bundle_dict(wrong_sha, rec))
    assert any("subject.source_sha256 does not match" in issue
               for issue in report["issues"]["binding"])


@pytest.mark.parametrize("schema", [None, "media_evidence_package/v9"])
def test_binding_requires_payload_schema(tmp_path, schema):
    from verity_core.trust import record

    _, pkg, bound = _bind_and_save(tmp_path)
    rec = record.new_record(bound["subject"], refs=bound["refs"])
    payload = copy.deepcopy(bound["entries"][0]["payload"])
    if schema is None:
        del payload["schema"]
    else:
        payload["schema"] = schema
    record.append_entry(rec, "evidence_recorded", payload=payload)
    report = media_bundle.verify_bundle(_bundle_dict(pkg, rec))
    assert report["chain_valid"] is True
    assert report["binding_valid"] is False
    assert any("entry payload schema must be" in issue
               for issue in report["issues"]["binding"])


@pytest.mark.parametrize("field,value", [
    ("kind", "unrelated"),
    ("identifier", "another-source.wav"),
    ("source_sha256", "0" * 64),
])
def test_binding_rejects_edited_subject(tmp_path, field, value):
    _, pkg, rec = _bind_and_save(tmp_path)
    rec["subject"][field] = value
    report = media_bundle.verify_bundle(_bundle_dict(pkg, rec))
    assert report["chain_valid"] is True
    assert report["binding_valid"] is False



def test_binding_mismatch_on_altered_package(tmp_path):
    path, _, _ = _bind_and_save(tmp_path)
    loaded = media_bundle.load_bundle(path)
    loaded["package"]["caveats"] = loaded["package"]["caveats"] + ["edited"]
    report = media_bundle.verify_bundle(loaded)
    assert report["package_valid"] is True
    assert report["chain_valid"] is True
    assert report["binding_valid"] is False
    assert any("identity mismatch" in issue
               for issue in report["issues"]["binding"])


def test_refs_only_edit_is_detected(tmp_path):
    path, _, _ = _bind_and_save(tmp_path)
    loaded = media_bundle.load_bundle(path)
    loaded["record"]["refs"]["evidence"]["artifact_sha256"] = "e" * 64
    report = media_bundle.verify_bundle(loaded)
    assert report["binding_valid"] is False
    assert any("refs.evidence.artifact_sha256 diverges" in issue
               for issue in report["issues"]["binding"])


def test_binding_not_evaluated_when_prerequisites_fail(tmp_path):
    path, _, _ = _bind_and_save(tmp_path)
    loaded = media_bundle.load_bundle(path)
    bad_package = copy.deepcopy(loaded)
    bad_package["package"]["artifacts"] = "not a list"
    report = media_bundle.verify_bundle(bad_package)
    assert report["package_valid"] is False
    assert report["binding_valid"] is False
    assert report["issues"]["binding"] == [
        "not evaluated because the package is invalid"
    ]
    bad_chain = copy.deepcopy(loaded)
    bad_chain["record"]["entries"][0]["payload"]["note"] = "tampered"
    report = media_bundle.verify_bundle(bad_chain)
    assert report["chain_valid"] is False
    assert report["binding_valid"] is False
    assert report["issues"]["binding"] == [
        "not evaluated because the record chain does not verify"
    ]


def test_verify_never_raises_on_malformed_content(tmp_path):
    _, pkg, rec = _bind_and_save(tmp_path)
    malformed = [
        {"schema": media_bundle.BUNDLE_SCHEMA, "package": {},
         "record": {}},
        {"schema": media_bundle.BUNDLE_SCHEMA, "package": None,
         "record": None},
        {"schema": media_bundle.BUNDLE_SCHEMA,
         "package": {"schema": package.SCHEMA, "source": "s",
                     "artifacts": 5, "caveats": [1, 2],
                     "metadata_by_origin": [], "cleanup": "x"},
         "record": {"entries": [1, "x", {}]}},
        {"schema": media_bundle.BUNDLE_SCHEMA,
         "package": {**pkg, "source": {**pkg["source"],
                                       "identifier": 42}},
         "record": rec},
    ]
    for bundle in malformed:
        report = media_bundle.verify_bundle(copy.deepcopy(bundle))
        assert report["verified"] is False
        assert report["package_valid"] is False or report["chain_valid"] is False
    nan_pkg = copy.deepcopy(pkg)
    nan_pkg["metadata_by_origin"]["source_embedded"] = {
        "container": {"duration_s": float("nan")}
    }
    report = media_bundle.verify_bundle(_bundle_dict(nan_pkg, rec))
    assert report["package_valid"] is False
    assert any("canonically serializable" in issue
               for issue in report["issues"]["package"])


# --- CLI ---------------------------------------------------------------------


def _run_cli(capsys, *argv):
    from verity_core.trust.__main__ import main

    exit_code = main(list(argv))
    return exit_code, capsys.readouterr().out


def test_cli_demo_and_verify(tmp_path, capsys):
    out = str(tmp_path / "demo.trust.json")
    code, out_text = _run_cli(capsys, "demo", "--out", out)
    assert code == 0
    assert os.path.exists(out)
    assert media_bundle.verify_file(out)["verified"] is True
    code, out_text = _run_cli(capsys, "verify", out)
    assert code == 0
    assert "package binding" in out_text
    assert "VERIFIED" in out_text
    assert media_bundle.DISCLAIMER in out_text
    code, _ = _run_cli(capsys, "verify", str(tmp_path / "missing.json"))
    assert code != 0


def test_cli_verify_fails_on_tampered_bundle(tmp_path, capsys):
    path, _, _ = _bind_and_save(tmp_path)
    with open(path, "r", encoding="utf-8") as fh:
        bundle = json.load(fh)
    bundle["package"]["artifacts"][0]["sha256"] = "f" * 64
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh)
    code, out_text = _run_cli(capsys, "verify", path)
    assert code != 0
    assert "NOT VERIFIED" in out_text
    assert "package binding" in out_text
    assert "identity mismatch" in out_text


def test_cli_demo_corrupt_detects_tampering(tmp_path, capsys):
    code, out_text = _run_cli(capsys, "demo", "--corrupt")
    assert code == 0
    assert "corruption was detected" in out_text
    assert "NOT VERIFIED" in out_text


def test_verify_rejects_cyclic_package_without_raising():
    pkg = {}
    pkg["cycle"] = pkg
    report = media_bundle.verify_bundle(_bundle_dict(pkg, {}))
    assert report["verified"] is False
    assert report["package_valid"] is False
    with pytest.raises(ValueError, match="cyclic or exceeds"):
        media_bundle.compute_identity(pkg)


def test_cli_rejects_excessively_nested_json(tmp_path, capsys):
    bad = tmp_path / "deep.json"
    bad.write_text("[" * 2000 + "0" + "]" * 2000)
    code, output = _run_cli(capsys, "verify", str(bad))
    assert code == 1
    assert "nesting exceeds" in output


def test_binding_helper_rejects_incomplete_package():
    with pytest.raises(ValueError, match="cannot bind an invalid package"):
        media_bundle.binding_for_package({})


def test_finalization_timestamp_shape_checked_at_actual_location(tmp_path):
    pkg = _build_package(tmp_path)
    pkg["metadata_by_origin"]["processing_generated"]["package_finalized_utc"] = 42
    with pytest.raises(ValueError, match="package_finalized_utc must be a string"):
        media_bundle.bind_package(pkg)

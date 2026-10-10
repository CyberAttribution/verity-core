"""Tests for the task-2 CLI slice: `create FILE --out BUNDLE.json` and
`verify BUNDLE.json --source FILE`.

All tests are offline. Fixtures are real files (stdlib `wave` audio and
plain bytes) hashed from disk; no network, external scoring, or UI. Where
optional tools (ffprobe/exiftool) are relevant they are exercised both as
available (if installed) and as explicitly missing via monkeypatched PATH
lookup, asserting truthful representation rather than fabricated metadata.
"""

import hashlib
import math
import os
import shutil
import struct
import wave

import pytest

from verity_core.media_ingest import package as pkg_module
from verity_core.media_ingest import tools
from verity_core.trust import __main__ as trust_main
from verity_core.trust import media_bundle


def _make_wav(path, seconds=0.05, freq=440.0):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        n = int(8000 * seconds)
        wav.writeframes(b"".join(
            struct.pack("<h",
                        int(8000 * math.sin(2 * math.pi * freq * i / 8000)))
            for i in range(n)
        ))
    return path


def _digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _create(tmp_path, source, name="out.trust.json", extra=()):
    out = tmp_path / name
    rc = trust_main.main(["create", str(source), "--out", str(out),
                          *extra])
    return rc, out


def test_create_real_fixture_and_bundle_verifies(tmp_path, capsys):
    src = _make_wav(tmp_path / "specimen.wav")
    rc, out = _create(tmp_path, src)
    assert rc == 0
    assert out.is_file()
    bundle = media_bundle.load_bundle(str(out))
    report = media_bundle.verify_bundle(bundle)
    assert report["verified"] is True
    art = bundle["package"]["artifacts"][0]
    assert art["role"] == "source"
    assert art["sha256"] == _digest(src)
    assert art["size_bytes"] == os.path.getsize(src)
    assert bundle["package"]["cleanup"]["status"] == "completed"
    assert bundle["package"]["cleanup"]["deleted_artifacts"] == []
    text = capsys.readouterr().out
    assert "create: source identity: sha256=" in text


def test_verify_with_matching_source_succeeds(tmp_path, capsys):
    src = _make_wav(tmp_path / "specimen.wav")
    rc, out = _create(tmp_path, src)
    assert rc == 0
    rc = trust_main.main(["verify", str(out), "--source", str(src)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "source: matches the digest bound in the bundle" in text


def test_verify_without_source_still_works(tmp_path):
    src = _make_wav(tmp_path / "specimen.wav")
    rc, out = _create(tmp_path, src)
    assert rc == 0
    assert trust_main.main(["verify", str(out)]) == 0


def test_modified_source_fails_without_deepfake_wording(tmp_path, capsys):
    src = _make_wav(tmp_path / "specimen.wav")
    rc, out = _create(tmp_path, src)
    assert rc == 0
    with open(src, "ab") as fh:
        fh.write(b"\x00")
    rc = trust_main.main(["verify", str(out), "--source", str(src)])
    assert rc == 1
    text = capsys.readouterr().out
    assert "DOES NOT match" in text
    assert "content mismatch" in text
    assert "deepfake" not in text.lower()


def test_truncated_source_fails(tmp_path):
    src = _make_wav(tmp_path / "specimen.wav")
    rc, out = _create(tmp_path, src)
    assert rc == 0
    data = open(src, "rb").read()[:-10]
    open(src, "wb").write(data)
    assert trust_main.main(["verify", str(out), "--source", str(src)]) == 1


def test_wrong_source_file_fails(tmp_path):
    src = _make_wav(tmp_path / "specimen.wav")
    other = tmp_path / "other.wav"
    _make_wav(other, freq=880.0)
    rc, out = _create(tmp_path, src)
    assert rc == 0
    assert trust_main.main(["verify", str(out), "--source",
                            str(other)]) == 1


def test_create_records_missing_optional_tools_truthfully(
        tmp_path, capsys, monkeypatch):
    src = _make_wav(tmp_path / "specimen.wav")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(tools, "_VERSION_CACHE", {})
    rc, out = _create(tmp_path, src)
    assert rc == 0
    text = capsys.readouterr().out
    assert "not available on PATH" in text
    bundle = media_bundle.load_bundle(str(out))
    pkg = bundle["package"]
    for name in ("ffprobe", "exiftool"):
        assert pkg["tooling"][name]["available"] is False
        assert pkg["tooling"][name]["ok"] is False
    se = pkg["metadata_by_origin"]["source_embedded"]
    assert "container" not in se            # nothing fabricated
    assert "embedded_timestamps" not in se
    statements = " ".join(o["statement"] for o in pkg["observations"])
    assert "was not available" in statements
    # integrity generation works without the tools: bundle verifies
    assert media_bundle.verify_bundle(bundle)["verified"] is True
    assert trust_main.main(["verify", str(out), "--source",
                            str(src)]) == 0


def test_create_with_available_ffprobe_records_real_metadata(
        tmp_path, capsys):
    if not tools.available("ffprobe"):
        pytest.skip("ffprobe not installed")
    src = _make_wav(tmp_path / "specimen.wav")
    rc, out = _create(tmp_path, src)
    assert rc == 0
    text = capsys.readouterr().out
    assert "ffprobe: metadata recorded" in text
    bundle = media_bundle.load_bundle(str(out))
    se = bundle["package"]["metadata_by_origin"]["source_embedded"]
    assert se["container"]["format_name"]  # real probe output, not fixture


def test_no_overwrite_preserves_existing_bundle(tmp_path):
    src = _make_wav(tmp_path / "specimen.wav")
    rc, out = _create(tmp_path, src)
    assert rc == 0
    original = out.read_bytes()
    rc = _create(tmp_path, src)[0]
    assert rc == 1
    assert out.read_bytes() == original


def test_explicit_overwrite_replaces_bundle(tmp_path):
    src = _make_wav(tmp_path / "specimen.wav")
    rc, out = _create(tmp_path, src)
    assert rc == 0
    rc, _ = _create(tmp_path, src, extra=("--overwrite",))
    assert rc == 0


def test_create_preserves_source_bytes(tmp_path):
    src = _make_wav(tmp_path / "specimen.wav")
    before = _digest(src)
    rc, _ = _create(tmp_path, src)
    assert rc == 0
    assert _digest(src) == before


def test_out_same_as_source_is_refused_and_source_intact(tmp_path):
    src = _make_wav(tmp_path / "specimen.wav")
    before = _digest(src)
    rc = trust_main.main(["create", str(src), "--out", str(src),
                          "--overwrite"])
    assert rc == 1
    assert _digest(src) == before


def test_source_input_rejections(tmp_path, capsys):
    missing = tmp_path / "missing.wav"
    rc, _ = _create(tmp_path, missing)
    assert rc == 1
    assert "no such file" in capsys.readouterr().out

    rc, _ = _create(tmp_path, tmp_path)
    assert rc == 1
    assert "not a regular file" in capsys.readouterr().out

    fifo = tmp_path / "pipe"
    if hasattr(os, "mkfifo"):
        os.mkfifo(fifo)
        rc, _ = _create(tmp_path, fifo)
        assert rc == 1
        assert "not a regular file" in capsys.readouterr().out

    if os.geteuid() != 0:
        locked = _make_wav(tmp_path / "locked.wav")
        os.chmod(locked, 0o000)
        try:
            rc, _ = _create(tmp_path, locked)
            assert rc == 1
            assert "not readable" in capsys.readouterr().out
        finally:
            os.chmod(locked, 0o644)


def test_mutation_during_hashing_is_refused(tmp_path, monkeypatch):
    src = _make_wav(tmp_path / "specimen.wav")
    sig = trust_main._stat_signature(str(src))
    changed = sig[:2] + (sig[2] + 1, sig[3])
    calls = iter([sig, changed])
    monkeypatch.setattr(trust_main, "_stat_signature",
                        lambda p: next(calls))
    rc, out = _create(tmp_path, src)
    assert rc == 1
    assert not out.exists()


def test_mutation_after_probes_is_refused(tmp_path, monkeypatch):
    src = _make_wav(tmp_path / "specimen.wav")
    sig = trust_main._stat_signature(str(src))
    changed = sig[:2] + (sig[2] + 1, sig[3])
    calls = iter([sig, sig, changed])
    monkeypatch.setattr(trust_main, "_stat_signature",
                        lambda p: next(calls))
    rc, out = _create(tmp_path, src)
    assert rc == 1
    assert not out.exists()


def test_verify_malformed_bundle_is_clear(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert trust_main.main(["verify", str(bad)]) == 1
    assert "cannot inspect" in capsys.readouterr().out

    missing_src = tmp_path / "nope.wav"
    src = _make_wav(tmp_path / "specimen.wav")
    rc, out = _create(tmp_path, src)
    assert rc == 0
    capsys.readouterr()
    assert trust_main.main(["verify", str(out), "--source",
                            str(missing_src)]) == 1
    assert "no such file" in capsys.readouterr().out


def test_verify_source_without_bound_source_artifact(tmp_path, capsys):
    src = _make_wav(tmp_path / "specimen.wav")
    pkg = pkg_module.new_package("upload", "no-artifact")
    pkg_module.derive_observations(pkg)
    pkg_module.finalize(pkg, "completed")
    rc, message = trust_main._check_bound_source(
        {"package": pkg, "record": {"entries": []}}, str(src))
    assert rc == 1
    assert "no valid evidence_recorded binding" in message


def test_bound_source_artifact_selects_exact_payload_id():
    bundle = {
        "package": {"artifacts": [
            {"artifact_id": "mart-A", "role": "source",
             "sha256": "a" * 64, "size_bytes": 1},
            {"artifact_id": "mart-B", "role": "source",
             "sha256": "b" * 64, "size_bytes": 2},
        ]},
        "record": {"entries": [
            {"entry_type": "evidence_recorded", "payload": {
                "schema": pkg_module.SCHEMA,
                "source_artifact_id": "mart-B",
                "package_identity_sha256": "c" * 64}},
        ]},
    }
    art_id, art = trust_main._bound_source_artifact(bundle)
    assert art_id == "mart-B"
    assert art["size_bytes"] == 2


def test_bound_source_artifact_rejects_invalid_payload():
    base = {
        "package": {"artifacts": [
            {"artifact_id": "mart-A", "role": "source",
             "sha256": "a" * 64, "size_bytes": 1},
        ]},
        "record": {"entries": []},
    }
    assert trust_main._bound_source_artifact(base) == (None, None)
    base["record"]["entries"] = [
        {"entry_type": "evidence_recorded", "payload": {
            "schema": pkg_module.SCHEMA,
            "source_artifact_id": "mart-A",
            "package_identity_sha256": "short"}},
    ]
    assert trust_main._bound_source_artifact(base) == (None, None)


def test_source_size_must_be_valid_integer(tmp_path):
    src = _make_wav(tmp_path / "specimen.wav")
    digest = _digest(src)
    size = os.path.getsize(src)
    for bad_size in (None, True, -5, "844", 1.5):
        bundle = {
            "package": {"artifacts": [
                {"artifact_id": "mart-A", "role": "source",
                 "sha256": digest, "size_bytes": bad_size},
            ]},
            "record": {"entries": [
                {"entry_type": "evidence_recorded", "payload": {
                    "schema": pkg_module.SCHEMA,
                    "source_artifact_id": "mart-A",
                    "package_identity_sha256": "c" * 64}},
            ]},
        }
        rc, message = trust_main._check_bound_source(bundle, str(src))
        assert rc == 1, bad_size
        assert "no valid integer byte size" in message


def test_mutation_during_source_verification_is_refused(
        tmp_path, monkeypatch):
    src = _make_wav(tmp_path / "specimen.wav")
    digest = _digest(src)
    size = os.path.getsize(src)
    bundle = {
        "package": {"artifacts": [
            {"artifact_id": "mart-A", "role": "source",
             "sha256": digest, "size_bytes": size},
        ]},
        "record": {"entries": [
            {"entry_type": "evidence_recorded", "payload": {
                "schema": pkg_module.SCHEMA,
                "source_artifact_id": "mart-A",
                "package_identity_sha256": "c" * 64}},
        ]},
    }
    sig = trust_main._stat_signature(str(src))
    changed = sig[:2] + (sig[2] + 1, sig[3])
    calls = iter([sig, changed])
    monkeypatch.setattr(trust_main, "_stat_signature",
                        lambda p: next(calls))
    rc, message = trust_main._check_bound_source(bundle, str(src))
    assert rc == 1
    assert "changed while being read" in message


def test_multiple_source_artifacts_bound_one_is_checked(tmp_path):
    bound_src = _make_wav(tmp_path / "bound.wav")
    other_src = _make_wav(tmp_path / "other.wav", freq=880.0)
    pkg = pkg_module.new_package("local_file", "two-sources")
    id_a = pkg_module.add_artifact(pkg, "source_media", path=str(bound_src),
                                   size_bytes=os.path.getsize(bound_src),
                                   step="intake", label="bound source")
    pkg_module.add_artifact(pkg, "source_media", path=str(other_src),
                            size_bytes=os.path.getsize(other_src),
                            step="intake", label="other source")
    pkg_module.derive_observations(pkg)
    pkg_module.finalize(pkg, "completed")
    assert not pkg_module.validate(pkg)
    rec = media_bundle.bind_package(pkg)
    out = tmp_path / "two.trust.json"
    media_bundle.save_bundle(str(out), pkg, rec)
    bundle = media_bundle.load_bundle(str(out))
    payload_id = trust_main._bound_source_artifact(bundle)[0]
    assert payload_id == id_a
    # the bound file passes; the other source artifact's file must fail
    rc, message = trust_main._check_bound_source(bundle, str(bound_src))
    assert rc == 0
    rc, message = trust_main._check_bound_source(bundle, str(other_src))
    assert rc == 1
    assert "DOES NOT match" in message


def test_create_survives_source_vanishing_during_metadata(
        tmp_path, monkeypatch):
    src = _make_wav(tmp_path / "specimen.wav")
    sig = trust_main._stat_signature(str(src))
    calls = iter([sig, sig])

    def flaky_stat(path):
        try:
            return next(calls)
        except StopIteration:
            raise FileNotFoundError(path)

    monkeypatch.setattr(trust_main, "_stat_signature", flaky_stat)
    rc, out = _create(tmp_path, src)
    assert rc == 1
    assert not out.exists()
    # the original file is untouched by the refusal path
    assert _digest(src)


def test_verify_source_output_scope(tmp_path, capsys):
    src = _make_wav(tmp_path / "specimen.wav")
    rc, out = _create(tmp_path, src)
    assert rc == 0
    capsys.readouterr()

    rc = trust_main.main(["verify", str(out), "--source", str(src)])
    assert rc == 0
    text = capsys.readouterr().out
    assert not any(l.startswith("VERIFIED: ") for l in text.splitlines())
    assert text.index("bundle envelope") < text.index("source: matches")
    assert "VERIFIED (bundle and bound source): " in text
    capsys.readouterr()

    with open(src, "ab") as fh:
        fh.write(b"\x00")
    rc = trust_main.main(["verify", str(out), "--source", str(src)])
    assert rc == 1
    text = capsys.readouterr().out
    assert not any(l.startswith("VERIFIED: ") for l in text.splitlines())
    assert text.count("NOT VERIFIED: ") == 1
    assert text.index("source: DOES NOT match") < text.index(
        "NOT VERIFIED: ")
    capsys.readouterr()

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    rc = trust_main.main(["verify", str(bad), "--source", str(src)])
    assert rc == 1
    text = capsys.readouterr().out
    assert "source not evaluated" in text or "cannot inspect" in text

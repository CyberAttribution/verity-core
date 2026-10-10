"""Tests for the task-3 compare slice: `compare LEFT.json RIGHT.json
[--left-source --right-source] [--out]` and the compare_bundles /
render_markdown API.

All tests are offline. Real verified bundles are produced with the
`create` CLI from real fixture files; mutated variants are re-bound with
the strict media_bundle APIs (bind_package + save_bundle) so every
comparison input is a bundle that verifies on its own terms. Assertions
cover: exact bound-artifact selection, duration arithmetic gates (bool,
nonfinite, unparseable, overflow), type+ordinal stream pairing with
unmatched streams, malformed nested metadata (no crash, no invented
values), tampered-bundle refusal, source recheck, output path safety,
and Markdown escaping of untrusted values.
"""

import copy
import json
import math
import os
import shutil
import struct
import wave

import pytest

from verity_core.media_ingest import package as pkg_module
from verity_core.media_ingest import tools
from verity_core.trust import __main__ as trust_main
from verity_core.trust import compare as compare_module
from verity_core.trust import media_bundle
from verity_core.trust import record as record_module


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


def _create(tmp_path, source, name="out.trust.json"):
    out = tmp_path / name
    rc = trust_main.main(["create", str(source), "--out", str(out)])
    assert rc == 0
    return media_bundle.load_bundle(str(out))


def _rebound(tmp_path, bundle, mutate, name):
    """A fresh VERIFIED bundle with a mutated (still shape-valid)
    package: the strict bind_package API revalidates and rebinds, so
    the result verifies on its own terms."""
    pkg = copy.deepcopy(bundle["package"])
    mutate(pkg)
    rec = media_bundle.bind_package(pkg)
    out = tmp_path / name
    media_bundle.save_bundle(str(out), pkg, rec)
    return media_bundle.load_bundle(str(out))


def _row(report_rows, field):
    matches = [r for r in report_rows if r["field"] == field]
    assert len(matches) == 1, f"expected exactly one {field!r} row"
    return matches[0]


def _duration_pkg(pkg, value):
    se = pkg["metadata_by_origin"]["source_embedded"]
    se["container"] = {"duration_s": value}


# --- basic comparison --------------------------------------------------------


def test_compare_identical_bundles_all_same(tmp_path):
    src = _make_wav(tmp_path / "a.wav")
    left = _create(tmp_path, src, "left.trust.json")
    right = _create(tmp_path, src, "right.trust.json")
    report = compare_module.compare_bundles(left, right)
    assert report["sha256_equal"] is True
    assert report["size_equal"] is True
    # Packages record their own creation times, so two separately
    # created bundles of identical bytes have DIFFERENT canonical
    # identities; sha256 equality is the byte-identity signal.
    assert report["identity_equal"] is False
    assert report["bytes_verdict"].startswith(
        "the bound artifacts carry the SAME recorded sha256")
    for rows in (report["container_rows"], report["stream_rows"],
                 report["embedded_rows"], report["tooling_rows"]):
        for row in rows:
            assert row["status"] in ("same", "unknown"), row


def test_same_bundle_object_has_equal_identity(tmp_path):
    left = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "l.json")
    report = compare_module.compare_bundles(left, left)
    assert report["identity_equal"] is True
    assert report["sha256_equal"] is True


def test_compare_different_files_reports_differ(tmp_path):
    left = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "l.json")
    right = _create(tmp_path, _make_wav(tmp_path / "b.wav", freq=880.0),
                    "r.json")
    report = compare_module.compare_bundles(left, right)
    assert report["sha256_equal"] is False
    assert report["bytes_verdict"].startswith(
        "the bound artifacts carry DIFFERENT recorded sha256")


def test_declared_labels_are_labels_not_provenance(tmp_path):
    left = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "l.json")
    right = _create(tmp_path, _make_wav(tmp_path / "b.wav"), "r.json")
    report = compare_module.compare_bundles(left, right)
    assert report["left"]["declared_label"] == "a.wav"
    assert report["right"]["declared_label"] == "b.wav"
    md = compare_module.render_markdown(report)
    assert "declared label" in md
    assert "NOT verified provenance" in md


# --- duration arithmetic gates -----------------------------------------------


def test_duration_delta_is_right_minus_left(tmp_path):
    base = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "base.json")
    left = _rebound(tmp_path, base, lambda p: _duration_pkg(p, 10.0),
                    "l.json")
    right = _rebound(tmp_path, base, lambda p: _duration_pkg(p, 12.5),
                     "r.json")
    report = compare_module.compare_bundles(left, right)
    row = _row(report["container_rows"], "container duration_s")
    assert row["status"] == "differ"
    assert row["delta"] == 2.5


@pytest.mark.parametrize("weird", [True, "12.5", "abc", 10 ** 400])
def test_duration_delta_excludes_unusable_values(tmp_path, weird):
    base = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "base.json")
    left = _rebound(tmp_path, base, lambda p: _duration_pkg(p, 10.0),
                    "l.json")
    right = _rebound(tmp_path, base, lambda p: _duration_pkg(p, weird),
                     "r.json")
    report = compare_module.compare_bundles(left, right)
    row = _row(report["container_rows"], "container duration_s")
    assert row["delta"] is None
    assert "duration delta not computed" in row["detail"]


def test_duration_delta_unit_gates_nonfinite_and_overflow():
    """Direct unit test of the defensive arithmetic gates: non-finite
    floats cannot occur in a canonically serialized bundle (bind_package
    refuses them), so these paths are unreachable through real bundles
    and are exercised on the helper directly."""
    delta, note = compare_module._duration_delta(float("nan"), 1.0)
    assert delta is None and "on the left" in note
    delta, note = compare_module._duration_delta(1.0, float("inf"))
    assert delta is None and "on the right" in note
    delta, note = compare_module._duration_delta(-1.7e308, 1.7e308)
    assert delta is None and "nonfinite result" in note
    delta, note = compare_module._duration_delta(10.0, 12.5)
    assert delta == 2.5 and note is None


def test_duration_delta_absent_on_both_sides(tmp_path):
    base = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "base.json")
    left = _rebound(tmp_path, base, lambda p: _duration_pkg(p, None),
                    "l.json")
    right = _rebound(tmp_path, base, lambda p: _duration_pkg(p, None),
                     "r.json")
    report = compare_module.compare_bundles(left, right)
    row = _row(report["container_rows"], "container duration_s")
    assert row["delta"] is None
    assert row["status"] == "unknown"


# --- stream pairing -----------------------------------------------------------


def _streams_pkg(pkg, streams):
    pkg["metadata_by_origin"]["source_embedded"]["streams"] = streams


def test_stream_pairing_type_ordinal_unmatched(tmp_path):
    """Regression for the _one_side_rows crash on unequal stream counts:
    left 9-ish slots vs right fewer, grouped by recorded type."""
    base = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "base.json")
    left_streams = [
        {"type": "video", "codec": "hevc", "codec_long": "HEVC",
         "width": 3840, "height": 2160, "fps": 23.983, "pix_fmt": "yuv420p"},
        {"type": "audio", "codec": "aac", "channels": 2,
         "sample_rate": 44100},
        {"type": "audio", "codec": "aac", "channels": 1,
         "sample_rate": 44100},
        {"type": "data"},
        {"type": "data"},
    ]
    right_streams = [
        {"type": "video", "codec": "h264", "codec_long": "H.264",
         "width": 1920, "height": 1080, "fps": 30.0, "pix_fmt": "yuv420p"},
        {"type": "audio", "codec": "mp3", "channels": 2,
         "sample_rate": 44100},
    ]
    left = _rebound(tmp_path, base, lambda p: _streams_pkg(p, left_streams),
                    "l.json")
    right = _rebound(tmp_path, base,
                     lambda p: _streams_pkg(p, right_streams), "r.json")
    report = compare_module.compare_bundles(left, right)
    count = _row(report["stream_rows"], "stream entry count")
    assert (count["left"], count["right"]) == (5, 2)
    video = _row(report["stream_rows"], "video[0] codec")
    assert video["status"] == "differ"
    assert (video["left"], video["right"]) == ("hevc", "h264")
    audio0 = _row(report["stream_rows"], "audio[0] codec")
    assert (audio0["left"], audio0["right"]) == ("aac", "mp3")
    audio1 = _row(report["stream_rows"], "audio[1] counterpart")
    assert audio1["left"] == "present" and audio1["right"] is None
    audio1_codec = _row(report["stream_rows"], "audio[1] codec")
    assert audio1_codec["left"] == "aac" and audio1_codec["right"] is None
    assert audio1_codec["status"] == "unknown"
    data1 = _row(report["stream_rows"], "data[1] counterpart")
    assert data1["left"] == "present" and data1["right"] is None
    md = compare_module.render_markdown(report)
    assert "DISPLAY ALIGNMENT ONLY" in md
    assert "not proof that paired streams correspond" in md


def test_stream_fields_union_and_tag_values(tmp_path):
    base = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "base.json")
    left = _rebound(tmp_path, base, lambda p: _streams_pkg(p, [
        {"type": "video", "codec": "hevc",
         "stream_tags": {"handler_name": "VideoHandle"}},
    ]), "l.json")
    right = _rebound(tmp_path, base, lambda p: _streams_pkg(p, [
        {"type": "video", "codec": "hevc", "fps": 30.0,
         "stream_tags": {"handler_name": "Core Media Video",
                         "creation_time": "2026-01-01"}},
    ]), "r.json")
    report = compare_module.compare_bundles(left, right)
    fps = _row(report["stream_rows"], "video[0] fps")
    assert fps["left"] is None and fps["right"] == 30.0
    assert fps["status"] == "unknown"
    handler = _row(report["stream_rows"],
                   "video[0] stream_tag handler_name")
    assert handler["status"] == "differ"
    creation = _row(report["stream_rows"],
                    "video[0] stream_tag creation_time")
    assert creation["status"] == "unknown"
    assert creation["detail"] == "not recorded on the left"


# --- malformed nested metadata -------------------------------------------------


def test_malformed_stream_entries_and_tags_do_not_crash(tmp_path):
    base = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "base.json")
    left = _rebound(tmp_path, base, lambda p: _streams_pkg(p, [
        "not-an-object",
        {"type": "video", "codec": "hevc", "stream_tags": ["x"]},
    ]), "l.json")
    right = _rebound(tmp_path, base, lambda p: _streams_pkg(p, [
        {"type": "video", "codec": "hevc"},
    ]), "r.json")
    report = compare_module.compare_bundles(left, right)
    bad = _row(report["stream_rows"], "stream entry 0 (left)")
    assert bad["status"] == "failed comparison"
    assert "not an object" in bad["detail"]
    tags = _row(report["stream_rows"], "video[0] stream_tags")
    assert tags["status"] == "failed comparison"
    assert "not an object" in tags["detail"]
    compare_module.render_markdown(report)


def test_non_object_container_and_tooling_do_not_crash(tmp_path):
    base = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "base.json")

    def mutate_container(p):
        p["metadata_by_origin"]["source_embedded"]["container"] = [1, 2]

    def mutate_tooling(p):
        p["tooling"]["junk"] = 7

    left = _rebound(tmp_path, base, mutate_container, "l.json")
    right = _rebound(tmp_path, base, mutate_tooling, "r.json")
    report = compare_module.compare_bundles(left, right)
    fmt = _row(report["container_rows"], "container format_name")
    assert fmt["status"] == "unknown"
    assert fmt["left"] is None
    junk = _row(report["tooling_rows"], "tool junk")
    assert "unexpected recorded type: int" in junk["right"]
    assert junk["left"] == "not recorded"
    md = compare_module.render_markdown(report)
    assert "unexpected recorded type (list)" in md


def test_missing_metadata_side_renders_unknown(tmp_path, capsys,
                                               monkeypatch):
    src = _make_wav(tmp_path / "a.wav")
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(tools, "_VERSION_CACHE", {})
    left = _create(tmp_path, src, "l.json")
    monkeypatch.setattr(shutil, "which", real_which)
    capsys.readouterr()
    right = _create(tmp_path, src, "r.json")
    report = compare_module.compare_bundles(left, right)
    fmt = _row(report["container_rows"], "container format_name")
    assert fmt["status"] == "unknown"
    assert "not recorded on the left" in fmt["detail"]
    assert report["probe_examination"]["left"].startswith("not examined")
    assert report["left"]["container"] is None
    tool = _row(report["tooling_rows"], "tool ffprobe")
    assert tool["left"] == "not available"


# --- tampered bundles refused ---------------------------------------------------


def test_tampered_bundle_refused_api(tmp_path):
    left = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "l.json")
    right = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "r.json")
    tampered = copy.deepcopy(right)
    tampered["package"]["artifacts"][0]["sha256"] = "f" * 64
    with pytest.raises(compare_module.CompareError, match="right bundle"):
        compare_module.compare_bundles(left, tampered)


def test_tampered_bundle_refused_cli(tmp_path, capsys):
    left = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "l.json")
    right = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "r.json")
    tampered = copy.deepcopy(right)
    tampered["package"]["artifacts"][0]["sha256"] = "f" * 64
    path = tmp_path / "tampered.trust.json"
    path.write_text(json.dumps(tampered))
    rc = trust_main.main(["compare", str(tmp_path / "l.json"), str(path)])
    assert rc == 1
    assert "failed strict verification" in capsys.readouterr().out


def test_exact_bound_artifact_is_compared(tmp_path):
    """The package carries two source artifacts; the record pins the
    SECOND one. Comparison must use the pinned artifact's digest, not
    the first valid source artifact in the list."""
    src = _make_wav(tmp_path / "a.wav")
    other = _make_wav(tmp_path / "b.wav", freq=880.0)
    pkg = pkg_module.new_package("upload", "two-artifacts")
    pkg_module.add_artifact(pkg, "source_media", path=str(src), step="t")
    second_id = pkg_module.add_artifact(pkg, "source_media",
                                        path=str(other), step="t")
    pkg_module.derive_observations(pkg, declared_extension="wav")
    pkg_module.finalize(pkg, "completed")
    identity = media_bundle.compute_identity(pkg)
    second = next(a for a in pkg["artifacts"]
                  if a["artifact_id"] == second_id)
    rec = record_module.new_record(
        subject={"kind": media_bundle._SUBJECT_KIND,
                 "identifier": pkg["source"]["identifier"],
                 "source_sha256": second["sha256"]},
        refs={"evidence": {
            "schema": pkg_module.SCHEMA,
            "source_artifact_id": second_id,
            "package_identity_sha256": identity,
            "artifact_sha256": second["sha256"],
        }},
    )
    record_module.append_entry(
        rec, "evidence_recorded", actor="test",
        payload={"schema": pkg_module.SCHEMA,
                 "source_artifact_id": second_id,
                 "package_identity_sha256": identity})
    out = tmp_path / "two.trust.json"
    media_bundle.save_bundle(str(out), pkg, rec)
    bundle = media_bundle.load_bundle(str(out))
    assert media_bundle.verify_bundle(bundle)["verified"] is True
    report = compare_module.compare_bundles(bundle, bundle)
    assert report["left"]["sha256"] == second["sha256"]
    assert report["left"]["artifact_id"] == second_id


# --- source recheck --------------------------------------------------------------


def test_sources_supplied_and_rechecked(tmp_path, capsys):
    src = _make_wav(tmp_path / "a.wav")
    left = _create(tmp_path, src, "l.json")
    right = _create(tmp_path, src, "r.json")
    rc = trust_main.main(["compare", str(tmp_path / "l.json"),
                          str(tmp_path / "r.json"),
                          "--left-source", str(src),
                          "--right-source", str(src)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "rechecked, matches the bound digest" in text


def test_source_mismatch_refused(tmp_path, capsys):
    src = _make_wav(tmp_path / "a.wav")
    other = _make_wav(tmp_path / "b.wav", freq=880.0)
    left = _create(tmp_path, src, "l.json")
    right = _create(tmp_path, src, "r.json")
    rc = trust_main.main(["compare", str(tmp_path / "l.json"),
                          str(tmp_path / "r.json"),
                          "--right-source", str(other)])
    assert rc == 1
    assert "right source verification failed" in capsys.readouterr().out


def test_source_without_check_source_is_rejected(tmp_path):
    left = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "l.json")
    right = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "r.json")
    with pytest.raises(ValueError, match="check_source is required"):
        compare_module.compare_bundles(left, right,
                                       left_source="whatever.wav")


def test_source_not_supplied_states_not_rechecked(tmp_path):
    left = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "l.json")
    report = compare_module.compare_bundles(left, left)
    for side in ("left", "right"):
        assert report["source_checks"][side]["supplied"] is False
        assert "not supplied" in report["source_checks"][side]["status"]


# --- CLI output handling -----------------------------------------------------------


def test_report_to_stdout_and_out_file(tmp_path, capsys):
    src = _make_wav(tmp_path / "a.wav")
    _create(tmp_path, src, "l.json")
    _create(tmp_path, src, "r.json")
    out = tmp_path / "report.md"
    rc = trust_main.main(["compare", str(tmp_path / "l.json"),
                          str(tmp_path / "r.json"), "--out", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    file_text = out.read_text(encoding="utf-8")
    assert "# Trust bundle comparison" in file_text
    # stdout carries the report verbatim plus a final wrote-report line;
    # anything before it comes from the bundle-creation helpers above
    tail = text[text.index("# Trust bundle comparison"):]
    assert tail == file_text + f"compare: wrote report {out}\n"
    assert f"compare: wrote report {out}" in text


def test_out_may_not_alias_inputs(tmp_path, capsys):
    src = _make_wav(tmp_path / "a.wav")
    _create(tmp_path, src, "l.json")
    _create(tmp_path, src, "r.json")
    left = tmp_path / "l.json"
    right = tmp_path / "r.json"
    # same path as an input bundle
    assert trust_main.main(["compare", str(left), str(right),
                            "--out", str(left)]) == 1
    assert "would overwrite an input" in capsys.readouterr().out
    # hardlink to an input bundle
    hard = tmp_path / "hard.trust.json"
    os.link(left, hard)
    assert trust_main.main(["compare", str(left), str(right),
                            "--out", str(hard)]) == 1
    capsys.readouterr()
    # symlink to a source file supplied via --left-source
    link = tmp_path / "link.wav"
    os.symlink(src, link)
    assert trust_main.main(["compare", str(left), str(right),
                            "--left-source", str(link),
                            "--out", str(link)]) == 1
    assert "would overwrite an input" in capsys.readouterr().out
    assert left.exists() and hard.exists()


def test_out_no_clobber_then_overwrite(tmp_path, capsys):
    src = _make_wav(tmp_path / "a.wav")
    _create(tmp_path, src, "l.json")
    _create(tmp_path, src, "r.json")
    out = tmp_path / "report.md"
    out.write_text("previous")
    rc = trust_main.main(["compare", str(tmp_path / "l.json"),
                          str(tmp_path / "r.json"), "--out", str(out)])
    assert rc == 1
    assert "pass --overwrite" in capsys.readouterr().out
    assert out.read_text(encoding="utf-8") == "previous"
    rc = trust_main.main(["compare", str(tmp_path / "l.json"),
                          str(tmp_path / "r.json"), "--out", str(out),
                          "--overwrite"])
    assert rc == 0
    assert "# Trust bundle comparison" in out.read_text(encoding="utf-8")


# --- Markdown escaping ---------------------------------------------------------------


def test_untrusted_metadata_escaped_in_markdown(tmp_path):
    base = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "base.json")
    hostile = "a|b `c` <script> A&B [x](http://evil) \n line2"

    def mutate(p):
        se = p["metadata_by_origin"]["source_embedded"]
        se["container_tags"] = {"note": hostile}

    left = _rebound(tmp_path, base, mutate, "l.json")
    right = _rebound(tmp_path, base, mutate, "r.json")
    report = compare_module.compare_bundles(left, right)
    md = compare_module.render_markdown(report)
    assert "a\\|b" in md                 # pipe cannot break the table
    assert "\\`c\\`" in md               # literal backticks survive, escaped
    assert "'c'" not in md               # no apostrophe substitution
    assert "<script>" not in md          # HTML cannot be injected
    assert "&lt;script&gt;" in md
    assert "A&amp;B" in md               # ampersands are escaped too
    assert "\\[x\\]" in md               # link syntax cannot be forged
    assert "\n line2" not in md          # newline is folded into the cell
    assert "a\\|b \\`c\\` &lt;script&gt; A&amp;B" in md  # value readable


def test_escape_backslash_and_pipe(tmp_path):
    base = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "base.json")
    raw = "x\\y|z"

    def mutate(p):
        se = p["metadata_by_origin"]["source_embedded"]
        se["container_tags"] = {"note": raw}

    left = _rebound(tmp_path, base, mutate, "l.json")
    right = _rebound(tmp_path, base, mutate, "r.json")
    report = compare_module.compare_bundles(left, right)
    md = compare_module.render_markdown(report)
    # the recorded backslash is doubled BEFORE the pipe is escaped, so
    # the rendering is unambiguous: literal backslash, escaped pipe
    assert "x\\\\y\\|z" in md


def test_long_value_truncated_before_escaping(tmp_path):
    base = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "base.json")
    kept = "a" * 150 + "\\" + "|" + "b" * 100   # len 252
    edge = "a" * 196 + "\\" + "|" + "b" * 50    # len 248

    def mutate(p):
        se = p["metadata_by_origin"]["source_embedded"]
        se["container_tags"] = {"note": kept, "edge": edge}

    left = _rebound(tmp_path, base, mutate, "l.json")
    right = _rebound(tmp_path, base, mutate, "r.json")
    report = compare_module.compare_bundles(left, right)
    md = compare_module.render_markdown(report)
    # "note": the raw text is cut at 197 chars — a*150, backslash, pipe
    # and 45 b's — and only THEN escaped. The pipe (index 151) is inside
    # the kept region, so it survives and must appear escaped.
    assert ("a" * 150 + "\\\\" + "\\|" + "b" * 45 + "...") in md
    assert ("b" * 46 + "...") not in md         # cut exactly at the bound
    # "edge": the backslash sits exactly at the cut boundary. Truncating
    # the RAW text keeps it whole and the escaping doubles it; escaping
    # first would leave a lone split backslash before the ellipsis.
    assert ("a" * 196 + "\\\\" + "...") in md
    assert ("a" * 196 + "\\" + "...") not in md


# --- one-sided stream tags -------------------------------------------------------------


def test_one_sided_stream_tags_are_unrecorded_not_failed(tmp_path):
    """Blocker regression: tags present on ONE side only are a metadata
    difference (unrecorded on the other), never a failed comparison."""
    base = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "base.json")
    left = _rebound(tmp_path, base, lambda p: _streams_pkg(p, [
        {"type": "video", "codec": "h264"},
    ]), "l.json")
    right = _rebound(tmp_path, base, lambda p: _streams_pkg(p, [
        {"type": "video", "codec": "h264",
         "stream_tags": {"language": "eng"}},
    ]), "r.json")
    report = compare_module.compare_bundles(left, right)
    lang = _row(report["stream_rows"], "video[0] stream_tag language")
    assert lang["status"] == "unknown"
    assert lang["left"] is None and lang["right"] == "eng"
    assert "not recorded on the left" in lang["detail"]
    assert all(r["status"] != "failed comparison"
               for r in report["stream_rows"])
    # reverse direction: tags on the left only
    left2 = _rebound(tmp_path, base, lambda p: _streams_pkg(p, [
        {"type": "video", "codec": "h264",
         "stream_tags": {"language": "eng"}},
    ]), "l2.json")
    right2 = _rebound(tmp_path, base, lambda p: _streams_pkg(p, [
        {"type": "video", "codec": "h264"},
    ]), "r2.json")
    report2 = compare_module.compare_bundles(left2, right2)
    lang2 = _row(report2["stream_rows"], "video[0] stream_tag language")
    assert lang2["status"] == "unknown"
    assert lang2["left"] == "eng" and lang2["right"] is None
    assert "not recorded on the right" in lang2["detail"]
    assert all(r["status"] != "failed comparison"
               for r in report2["stream_rows"])


# --- strict equality --------------------------------------------------------------------


def test_bool_is_not_equal_to_number(tmp_path):
    base = _create(tmp_path, _make_wav(tmp_path / "a.wav"), "base.json")

    def with_size(p, value):
        se = p["metadata_by_origin"]["source_embedded"]
        se["container"] = {"size_bytes": value, "duration_s": 1.0}

    left = _rebound(tmp_path, base, lambda p: with_size(p, 0), "l.json")
    right = _rebound(tmp_path, base,
                     lambda p: with_size(p, False), "r.json")
    report = compare_module.compare_bundles(left, right)
    row = _row(report["container_rows"], "container size_bytes")
    assert row["status"] == "differ"
    assert row["left"] == 0 and row["right"] is False


# --- report publishing fail-closed --------------------------------------------------------


def test_link_error_fails_closed_without_rename_fallback(
        tmp_path, capsys, monkeypatch):
    src = _make_wav(tmp_path / "a.wav")
    _create(tmp_path, src, "l.json")
    _create(tmp_path, src, "r.json")
    out = tmp_path / "report.md"

    def broken_link(*args, **kwargs):
        raise OSError("link unsupported")

    monkeypatch.setattr(os, "link", broken_link)
    rc = trust_main.main(["compare", str(tmp_path / "l.json"),
                          str(tmp_path / "r.json"), "--out", str(out)])
    assert rc == 1
    assert "cannot write" in capsys.readouterr().out
    assert not out.exists()              # no silent rename fallback
    assert not list(tmp_path.glob(".verity-report-*"))


def test_concurrent_target_preserved_on_link_collision(
        tmp_path, capsys, monkeypatch):
    """A target that appears between the alias check and the publish
    link (concurrent creation) must be preserved, not replaced."""
    src = _make_wav(tmp_path / "a.wav")
    _create(tmp_path, src, "l.json")
    _create(tmp_path, src, "r.json")
    out = tmp_path / "report.md"
    out.write_text("sentinel")

    def racing_link(*args, **kwargs):
        raise FileExistsError(17, "target appeared concurrently")

    monkeypatch.setattr(os, "link", racing_link)
    rc = trust_main.main(["compare", str(tmp_path / "l.json"),
                          str(tmp_path / "r.json"), "--out", str(out)])
    assert rc == 1
    assert "already exists" in capsys.readouterr().out
    assert out.read_text(encoding="utf-8") == "sentinel"
    assert not list(tmp_path.glob(".verity-report-*"))


# --- renderer structure ---------------------------------------------------------------------


def test_renderer_blank_lines_limits_and_relationship_wording(tmp_path):
    src = _make_wav(tmp_path / "a.wav")
    left = _create(tmp_path, src, "l.json")
    right = _create(tmp_path, src, "r.json")
    report = compare_module.compare_bundles(left, right)
    md = compare_module.render_markdown(report)
    # each of the four as-recorded tables is preceded by a blank line so
    # a preceding bullet list cannot swallow it
    assert md.count(
        "\n\n| field | left | right | status | notes | delta |") == 4
    assert ("does not establish an original/copy relationship") in md
    assert "packages record no claim" not in md
    assert "truncated with ..." in md

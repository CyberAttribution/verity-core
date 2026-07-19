"""
S3 tests: frame + audio extraction (real ffmpeg on synthesized video when
available) and the unified epistemic observation model
(direct_observation / inference / model_interpretation with competing
explanations) that visual/audio/transcript evidence shares with metadata.
"""
import os
import subprocess

import pytest

from verity_core.media_ingest import audio, frames, package, tools

HAVE_FFMPEG = tools.available("ffmpeg")


@pytest.fixture(scope="module")
def test_video(tmp_path_factory):
    """2s synthesized test video with a tone track (no third-party media)."""
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg not installed")
    path = str(tmp_path_factory.mktemp("media") / "clip.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-y", path],
        check=True, timeout=60)
    return path


# ---------------- frames ----------------
def test_frames_extracted_bounded_and_provenanced(test_video, tmp_path):
    out = frames.extract_frames(test_video, str(tmp_path / "f"),
                                max_frames=4, max_width=160, duration_s=2.0)
    assert out["ok"], out.get("error")
    assert 1 <= len(out["frames"]) <= 4
    for f in out["frames"]:
        p = tmp_path / "f" / f["filename"]
        assert p.is_file() and p.stat().st_size > 0
        assert p.read_bytes()[:3] == b"\xff\xd8\xff"      # real JPEG
        assert f["method"] in ("scene_change", "interval")
    assert any(f["method"] == "interval" and f["timestamp_s"] is not None
               for f in out["frames"])


def test_frames_no_video_stream_fails_honestly(tmp_path):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg not installed")
    import wave
    wav = str(tmp_path / "a.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 4000)
    out = frames.extract_frames(wav, str(tmp_path / "f"), max_frames=2)
    assert out["ok"] is False and "no frames" in out["error"]


def test_frames_tool_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "available", lambda n: False)
    out = frames.extract_frames("/x.mp4", str(tmp_path / "f"))
    assert out["ok"] is False and out["tool_missing"] is True


# ---------------- audio ----------------
def test_audio_extracted_mono_compressed(test_video, tmp_path):
    out = audio.extract_audio(test_video, str(tmp_path / "a"),
                              max_seconds=10)
    assert out["ok"], out.get("error")
    p = tmp_path / "a" / out["filename"]
    assert p.is_file() and p.stat().st_size > 0
    assert out["mime"] in ("audio/mp3", "audio/aac")
    assert out["mono"] is True and out["sample_rate"] == 16000


def test_audio_no_track_fails_honestly(tmp_path):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg not installed")
    silent = str(tmp_path / "v.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=duration=1:size=160x120:rate=5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", silent],
        check=True, timeout=60)
    out = audio.extract_audio(silent, str(tmp_path / "a"))
    assert out["ok"] is False


def test_audio_tool_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "available", lambda n: False)
    out = audio.extract_audio("/x.mp4", str(tmp_path / "a"))
    assert out["ok"] is False and out["tool_missing"] is True


# ---------------- unified epistemic observation model ----------------
def test_model_observation_requires_epistemic_structure():
    p = package.new_package("upload", "x.mp4")
    oid = package.add_model_observation(
        p, "Frame 2 shows text overlay reading 'URGENT'.",
        model="gemini-2.0-flash", modality="visual",
        competing_explanations=[
            "Overlay may be original platform captioning rather than "
            "later editing."],
        relevance="Bears on whether on-screen text matches the claimed "
                  "context.",
        artifact_ids=["mart-abc"])
    obs = [o for o in p["observations"] if o["id"] == oid][0]
    assert obs["basis"] == "model_interpretation"
    assert obs["modality"] == "visual"
    assert obs["competing_explanations"]
    assert obs["source"]["model"] == "gemini-2.0-flash"
    assert obs["source"]["artifacts"] == ["mart-abc"]
    # folded into the four-way classification
    mo = p["metadata_by_origin"]["model_observations"]
    assert mo["visual"][0]["observation_id"] == oid


def test_model_observation_default_competing_explanation():
    p = package.new_package("upload", "x.mp4")
    package.add_model_observation(p, "Audio contains a single tone.",
                                  model="m", modality="audio",
                                  competing_explanations=None)
    obs = p["observations"][0]
    assert obs["competing_explanations"]          # never empty for models


def test_all_bases_share_one_observation_list():
    p = package.new_package("upload", "x.mp4")
    package._observe(p, "Container is mp4.", "direct_observation",
                     modality="metadata")
    package._observe(p, "Gap consistent with re-encoding.", "inference",
                     modality="metadata",
                     competing=["Routine transcoding produces the same "
                                "gap."])
    package.add_model_observation(p, "A speaker is visible.", model="m",
                                  modality="visual",
                                  competing_explanations=["Could be a "
                                                          "still image."])
    bases = [o["basis"] for o in p["observations"]]
    assert bases == ["direct_observation", "inference",
                     "model_interpretation"]
    for o in p["observations"]:
        assert o["basis"] in package.OBSERVATION_BASES
        assert "modality" in o and "competing_explanations" in o


def test_derived_media_artifacts_chain_like_metadata(test_video, tmp_path):
    """Frames/audio integrate into the same evidence model: derived
    artifacts with parent/hash/step/tool, events in the same chain."""
    p = package.new_package("upload", "clip.mp4")
    src = package.add_artifact(p, "source_media", path=test_video,
                               step="intake")
    fr = frames.extract_frames(test_video, str(tmp_path / "f"),
                               max_frames=2, duration_s=2.0)
    ids = []
    for f in fr["frames"]:
        ids.append(package.add_artifact(
            p, "frame", path=str(tmp_path / "f" / f["filename"]),
            parent=src, step="frame_extraction", tool=fr["tool"],
            label=f"{f['method']} frame"))
    package.record_event(p, "frame_extraction", "ok", tool=fr["tool"],
                         inputs=[src], outputs=ids)
    for a in p["artifacts"][1:]:
        assert a["parent_artifact"] == src and len(a["sha256"]) == 64
        assert a["tool"]["name"] == "ffmpeg"
    ev = p["processing_events"][-1]
    assert ev["output_artifacts"] == ids and ev["input_artifacts"] == [src]

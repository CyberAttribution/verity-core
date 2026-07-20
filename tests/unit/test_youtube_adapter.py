"""
S5 tests: YouTube adapter URL validation matrix, canonical
reconstruction, failure categorization, probe/retrieve (mocked tool),
and the provider-neutral registry. Offline — yt-dlp is never invoked.
"""
import json

import pytest

from verity_core.media_ingest import remote, tools, youtube

A = youtube.YouTubeAdapter()
VID = "dQw4w9WgXcQ"
CANON = f"https://www.youtube.com/watch?v={VID}"


# ---------------- validation matrix ----------------
@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/watch?v={VID}",
    f"https://youtube.com/watch?v={VID}",
    f"https://m.youtube.com/watch?v={VID}",
    f"https://youtu.be/{VID}",
    f"https://youtu.be/{VID}?t=42",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/embed/{VID}",
])
def test_supported_forms_canonicalize(url):
    c = A.classify(url)
    assert c["supported"], c["reject_reason"]
    assert c["media_id"] == VID
    assert c["canonical_url"] == CANON     # user string never reused


@pytest.mark.parametrize("url,reason_frag", [
    ("https://www.youtube.com/playlist?list=PLx", "Playlists"),
    (f"https://www.youtube.com/watch?list=PLx", "Playlists"),
    ("https://www.youtube.com/@somechannel", "Channel"),
    ("https://www.youtube.com/channel/UCabc", "Channel"),
    ("https://www.youtube.com/live/abc", "Live"),
    (f"http://www.youtube.com/watch?v={VID}", "https"),
    (f"https://user:pw@www.youtube.com/watch?v={VID}", "not accepted"),
    (f"https://www.youtube.com:8443/watch?v={VID}", "not accepted"),
    ("https://www.youtube.com/watch?v=short", "video id"),
    ("https://www.youtube.com/watch", "video id"),
])
def test_rejected_youtube_forms(url, reason_frag):
    c = A.classify(url)
    assert c["provider_match"] and not c["supported"]
    assert reason_frag.lower() in c["reject_reason"].lower()


@pytest.mark.parametrize("url", [
    f"https://vimeo.com/{VID}",
    f"https://youtube.com.evil.example/watch?v={VID}",
    f"https://evilyoutube.com/watch?v={VID}",
    "https://192.0.2.7/watch?v=" + VID,
    "not a url at all",
    "",
])
def test_non_youtube_not_matched(url):
    c = A.classify(url)
    assert not c["provider_match"] and not c["supported"]


def test_watch_url_with_video_and_list_rejected_initial_version():
    c = A.classify(f"https://www.youtube.com/watch?v={VID}&list=PLx")
    # 'v' present alongside list -> still a single video reference; the
    # canonical URL strips the playlist context entirely
    assert c["supported"] and c["canonical_url"] == CANON


# ---------------- registry (provider-neutral) ----------------
def test_registry_resolves_youtube_only():
    assert remote.get_adapter(CANON).provider == "youtube"
    assert remote.get_adapter("https://vimeo.com/123") is None


def test_every_category_has_confidence_effect():
    for c in remote.FAILURE_CATEGORIES:
        assert remote.confidence_effect(c)
    assert "contextual evidence alone" in remote.confidence_effect(
        "bot_challenge")


# ---------------- failure categorization ----------------
@pytest.mark.parametrize("stderr,expected", [
    ("ERROR: Sign in to confirm you're not a bot", "bot_challenge"),
    ("This video is not available in your country", "geo_restricted"),
    ("Sign in to confirm your age", "age_restricted"),
    ("blocked it on copyright grounds", "copyright_restricted"),
    ("ERROR: Private video", "unavailable"),
    ("ERROR: Video unavailable", "unavailable"),
    ("Unable to download webpage: Name or service not known",
     "network_failure"),
    ("something entirely new", "retrieval_error"),
])
def test_categorize_failure(stderr, expected):
    assert A.categorize_failure(stderr) == expected


def test_categorize_timeout_and_tool_missing():
    assert A.categorize_failure("", timed_out=True) == "timeout"
    assert A.categorize_failure("", tool_missing=True) == "tool_missing"


# ---------------- probe (mocked tool) ----------------
PROBE_JSON = {"id": VID, "title": "Clip", "uploader": "Chan",
              "channel_id": "UCabc", "upload_date": "20260701",
              "duration": 120, "is_live": False, "was_live": False,
              "availability": "public", "description": "d" * 3000,
              "view_count": 5, "height": 720}


def test_probe_parses_and_bounds(monkeypatch):
    seen = {}

    def fake(argv, timeout_s=60, **k):
        seen["argv"] = argv
        return {"ok": True, "returncode": 0,
                "stdout": json.dumps(PROBE_JSON), "stderr": ""}
    monkeypatch.setattr(tools, "run_tool", fake)
    monkeypatch.setattr(tools, "tool_version", lambda n: "2026.01.01")
    out = A.probe(CANON)
    assert out["ok"] and out["metadata"]["duration_s"] == 120
    assert len(out["metadata"]["description_excerpt"]) == 2000
    assert "--skip-download" in seen["argv"]
    assert "--no-playlist" in seen["argv"]
    assert seen["argv"][-1] == CANON and seen["argv"][-2] == "--"
    assert not any("cookie" in a for a in seen["argv"])


def test_probe_failure_categorized(monkeypatch):
    monkeypatch.setattr(tools, "run_tool",
                        lambda argv, timeout_s=60, **k:
                        {"ok": False, "returncode": 1, "stdout": "",
                         "stderr": "Sign in to confirm you're not a bot"})
    monkeypatch.setattr(tools, "tool_version", lambda n: None)
    out = A.probe(CANON)
    assert out["ok"] is False and out["category"] == "bot_challenge"


# ---------------- retrieve (mocked tool) ----------------
def test_retrieve_success_locked_to_dest(monkeypatch, tmp_path):
    dest = str(tmp_path / "src")
    seen = {}

    def fake(argv, timeout_s=60, **k):
        seen["argv"] = argv
        with open(f"{dest}/source.mp4", "wb") as fh:
            fh.write(b"\x00\x00\x00\x18ftypmp42")
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}
    monkeypatch.setattr(tools, "run_tool", fake)
    monkeypatch.setattr(tools, "tool_version", lambda n: "v")
    out = A.retrieve(CANON, dest, {"max_filesize_mb": 100,
                                   "max_resolution": 720})
    assert out["ok"] and out["filename"] == "source.mp4"
    argv = seen["argv"]
    assert "--max-filesize" in argv and "100M" in argv
    assert any("height<=720" in a for a in argv)
    assert argv[-1] == CANON and argv[-2] == "--"


def test_retrieve_failure_categorized_and_partials_removed(monkeypatch,
                                                           tmp_path):
    dest = str(tmp_path / "src")

    def fake(argv, timeout_s=60, **k):
        import os
        os.makedirs(dest, exist_ok=True)
        with open(f"{dest}/source.mp4.part", "wb") as fh:
            fh.write(b"x")            # not source.* prefix match? it is not
        with open(f"{dest}/source.mp4", "wb") as fh:
            fh.write(b"partial")
        return {"ok": False, "returncode": 1, "stdout": "",
                "stderr": "This video is not available in your country"}
    monkeypatch.setattr(tools, "run_tool", fake)
    monkeypatch.setattr(tools, "tool_version", lambda n: "v")
    out = A.retrieve(CANON, dest)
    assert out["ok"] is False and out["category"] == "geo_restricted"
    import os
    assert not os.path.exists(f"{dest}/source.mp4")   # partial removed


# ---------------- retrieval-reliability fix (client plan + diag) ----------
def test_sanitize_diag_strips_urls_and_tokens():
    dirty = ("ERROR at https://rr1.googlevideo.com/videoplayback?sig="
             + "x" * 120 + "&ip=1.2.3.4 plus token " + "T" * 90)
    clean = youtube.sanitize_diag(dirty)
    assert "googlevideo" not in clean and "ip=1.2.3.4" not in clean
    assert "x" * 40 not in clean and "T" * 40 not in clean
    assert "[url]" in clean and len(clean) <= 300


def test_probe_bot_challenge_falls_back_once_to_tv(monkeypatch):
    calls = []

    def fake(argv, timeout_s=60, **k):
        calls.append(argv)
        if len(calls) == 1:
            return {"ok": False, "returncode": 1, "stdout": "",
                    "stderr": "Sign in to confirm you're not a bot"}
        return {"ok": True, "returncode": 0,
                "stdout": json.dumps(PROBE_JSON), "stderr": ""}
    monkeypatch.setattr(tools, "run_tool", fake)
    monkeypatch.setattr(tools, "tool_version", lambda n: "v")
    out = A.probe(CANON)
    assert out["ok"] and len(calls) == 2
    # first attempt: yt-dlp defaults (no player_client override)
    assert not any("player_client" in a for a in calls[0])
    # bounded deterministic fallback: exactly one retry, tv client
    assert "youtube:player_client=tv" in calls[1]
    assert out["attempts"] == [
        {"client": "default", "outcome": "bot_challenge"},
        {"client": "tv", "outcome": "ok"}]


def test_probe_no_fallback_for_non_bot_categories(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "run_tool",
                        lambda argv, timeout_s=60, **k:
                        (calls.append(argv) or
                         {"ok": False, "returncode": 1, "stdout": "",
                          "stderr": "ERROR: Private video"}))
    monkeypatch.setattr(tools, "tool_version", lambda n: "v")
    out = A.probe(CANON)
    assert out["ok"] is False and out["category"] == "unavailable"
    assert len(calls) == 1                    # no second attempt


def test_probe_bot_challenge_both_clients_reported(monkeypatch):
    monkeypatch.setattr(tools, "run_tool",
                        lambda argv, timeout_s=60, **k:
                        {"ok": False, "returncode": 1, "stdout": "",
                         "stderr": "confirm you're not a bot"})
    monkeypatch.setattr(tools, "tool_version", lambda n: "v")
    out = A.probe(CANON)
    assert out["category"] == "bot_challenge"
    assert [a["client"] for a in out["attempts"]] == ["default", "tv"]
    assert all(a["outcome"] == "bot_challenge" for a in out["attempts"])


def test_env_configured_clients_and_extractor_args(monkeypatch):
    monkeypatch.setenv("VERITY_YTDLP_PLAYER_CLIENTS", "tv,web")
    monkeypatch.setenv("VERITY_YTDLP_FALLBACK_CLIENTS", "")
    monkeypatch.setenv("VERITY_YTDLP_EXTRACTOR_ARGS",
                       "youtube:getpot_bgutil_baseurl=http://pot:4416")
    calls = []
    monkeypatch.setattr(tools, "run_tool",
                        lambda argv, timeout_s=60, **k:
                        (calls.append(argv) or
                         {"ok": True, "returncode": 0,
                          "stdout": json.dumps(PROBE_JSON),
                          "stderr": ""}))
    monkeypatch.setattr(tools, "tool_version", lambda n: "v")
    out = A.probe(CANON)
    assert out["ok"] and len(calls) == 1
    assert "youtube:player_client=tv,web" in calls[0]
    assert "youtube:getpot_bgutil_baseurl=http://pot:4416" in calls[0]


def test_retrieve_bot_challenge_falls_back_and_cleans_partials(
        monkeypatch, tmp_path):
    dest = str(tmp_path / "src")
    calls = []

    def fake(argv, timeout_s=60, **k):
        calls.append(argv)
        import os
        os.makedirs(dest, exist_ok=True)
        if len(calls) == 1:
            with open(f"{dest}/source.mp4", "wb") as fh:
                fh.write(b"partial")
            return {"ok": False, "returncode": 1, "stdout": "",
                    "stderr": "confirm you're not a bot"}
        with open(f"{dest}/source.mp4", "wb") as fh:
            fh.write(b"\x00\x00\x00\x18ftypmp42")
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}
    monkeypatch.setattr(tools, "run_tool", fake)
    monkeypatch.setattr(tools, "tool_version", lambda n: "v")
    out = A.retrieve(CANON, dest)
    assert out["ok"] and len(calls) == 2
    assert "youtube:player_client=tv" in calls[1]
    assert out["attempts"][0]["outcome"] == "bot_challenge"
    import os
    assert os.path.getsize(f"{dest}/source.mp4") > 7   # not the partial


def test_retrieve_oversize_category(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "run_tool",
                        lambda argv, timeout_s=60, **k:
                        {"ok": False, "returncode": 1, "stdout": "",
                         "stderr": "File is larger than max-filesize"})
    monkeypatch.setattr(tools, "tool_version", lambda n: "v")
    out = A.retrieve(CANON, str(tmp_path / "s"))
    assert out["category"] == "oversize"

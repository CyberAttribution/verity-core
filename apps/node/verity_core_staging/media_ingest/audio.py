"""
media_ingest.audio — bounded audio-track extraction via ffmpeg.

Produces one mono, model-transcribable derived artifact (MP3 64k when the
encoder exists, else ADTS AAC — both accepted by multimodal transports;
a small compressed track keeps the whole payload inside the router's
inline budget). The track is a derived artifact: temporary, hashed,
parented to the source, deleted with media cleanup.
"""
import os

from . import tools

_ENCODERS = (
    # (codec args, filename, mime)
    (["-c:a", "libmp3lame", "-b:a", "64k"], "audio.mp3", "audio/mp3"),
    (["-c:a", "aac", "-b:a", "64k"], "audio.aac", "audio/aac"),
)


def extract_audio(path, out_dir, sample_rate=16000, max_seconds=None,
                  timeout_s=180):
    """Extract the audio track. Returns {ok, filename, mime, codec_args,
    tool, error?}. Never raises; no-audio is an honest failure."""
    tool = {"name": "ffmpeg", "version": tools.tool_version("ffmpeg")}
    if not tools.available("ffmpeg"):
        return {"ok": False, "tool": tool, "error": "ffmpeg not available",
                "tool_missing": True}
    os.makedirs(out_dir, exist_ok=True)
    last_err = "no encoder attempted"
    for codec_args, filename, mime in _ENCODERS:
        out = os.path.join(out_dir, filename)
        argv = ["ffmpeg", "-v", "error", "-i", path, "-vn",
                "-ac", "1", "-ar", str(int(sample_rate))]
        if max_seconds:
            argv += ["-t", str(int(max_seconds))]
        argv += codec_args + ["-y", out]
        r = tools.run_tool(argv, timeout_s=timeout_s)
        if r.get("ok") and os.path.isfile(out) and os.path.getsize(out) > 0:
            return {"ok": True, "tool": tool, "filename": filename,
                    "mime": mime, "sample_rate": int(sample_rate),
                    "mono": True}
        last_err = (r.get("stderr") or r.get("error") or "encode failed") \
            .strip().splitlines()[-1] if (r.get("stderr") or
                                          r.get("error")) else "encode failed"
    return {"ok": False, "tool": tool,
            "error": f"audio extraction failed: {last_err[:200]}"}

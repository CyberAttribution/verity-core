"""
media_ingest.probe — structural media facts via ffprobe.

Everything returned here is a DIRECT OBSERVATION of the file as read by
ffprobe: container, streams, codecs, dimensions, duration, and any
container-level tags. Container tags (creation_time, encoder, handler
names, ...) are SOURCE-EMBEDDED metadata — they travel with the file and
may be absent, wrong, or forged; they are evidence about the file's
claimed history, not proof of it.
"""
from . import tools

_PROBE_ARGS = ["-v", "error", "-print_format", "json",
               "-show_format", "-show_streams"]


def _fraction(s):
    try:
        num, _, den = str(s or "").partition("/")
        num, den = float(num), float(den or 1)
        return round(num / den, 3) if den else None
    except (TypeError, ValueError):
        return None


def probe_media(path, timeout_s=60):
    """Returns {ok, tool, container, streams, embedded_tags} or
    {ok: False, tool, error}. Never raises."""
    tool = {"name": "ffprobe", "version": tools.tool_version("ffprobe")}
    out = tools.run_json_tool(["ffprobe"] + _PROBE_ARGS + ["--", path],
                              timeout_s=timeout_s)
    if not out.get("ok"):
        return {"ok": False, "tool": tool,
                "error": out.get("error")
                or f"ffprobe could not parse the file "
                   f"(rc={out.get('returncode')})",
                "tool_missing": bool(out.get("tool_missing"))}
    data = out.get("data") or {}
    fmt = data.get("format") or {}
    streams = []
    for s in data.get("streams") or []:
        entry = {
            "type": s.get("codec_type"),
            "codec": s.get("codec_name"),
            "codec_long": s.get("codec_long_name"),
        }
        if s.get("codec_type") == "video":
            entry.update({
                "width": s.get("width"), "height": s.get("height"),
                "fps": _fraction(s.get("avg_frame_rate")),
                "pix_fmt": s.get("pix_fmt"),
                "frames": s.get("nb_frames"),
            })
        elif s.get("codec_type") == "audio":
            entry.update({
                "channels": s.get("channels"),
                "sample_rate": s.get("sample_rate"),
            })
        tags = s.get("tags") or {}
        if tags:
            entry["stream_tags"] = {k: str(v)[:300] for k, v in
                                    sorted(tags.items())[:40]}
        streams.append(entry)
    container = {
        "format_name": fmt.get("format_name"),
        "format_long": fmt.get("format_long_name"),
        "duration_s": _float(fmt.get("duration")),
        "size_bytes": _int(fmt.get("size")),
        "bit_rate": _int(fmt.get("bit_rate")),
        "stream_count": _int(fmt.get("nb_streams")),
    }
    embedded_tags = {k: str(v)[:300] for k, v in
                     sorted((fmt.get("tags") or {}).items())[:60]}
    return {"ok": True, "tool": tool, "container": container,
            "streams": streams, "embedded_tags": embedded_tags}


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

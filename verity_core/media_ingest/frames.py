"""
media_ingest.frames — bounded representative-frame extraction via ffmpeg.

Two sampling methods, both DIRECT, DETERMINISTICALLY BOUNDED operations
whose provenance is recorded per frame:
  scene_change  ffmpeg select=gt(scene,threshold) — visually distinct
                moments (best-effort; codecs/content may yield none)
  interval      evenly spaced timestamps across the known duration —
                deterministic coverage fallback/filler

Frames are derived artifacts: temporary, hashed, parented to the source,
and deleted with the job's media cleanup. Extraction is evidence
collection — selecting a frame asserts nothing about its content.
"""
import os

from . import tools

JPEG_QUALITY = "4"          # ffmpeg -q:v (2 best … 31 worst)


def _scale_filter(max_width):
    # never upscale; keep aspect; even height for JPEG writers
    return f"scale='min({int(max_width)},iw)':-2"


def extract_frames(path, out_dir, max_frames=8, max_width=768,
                   duration_s=None, scene_threshold=0.30, timeout_s=120):
    """Extract up to max_frames JPEGs into out_dir. Returns
    {ok, frames: [{filename, method, timestamp_s|None}], tool, error?}.
    Never raises; partial results are returned with ok=True."""
    tool = {"name": "ffmpeg", "version": tools.tool_version("ffmpeg")}
    if not tools.available("ffmpeg"):
        return {"ok": False, "tool": tool, "frames": [],
                "error": "ffmpeg not available", "tool_missing": True}
    os.makedirs(out_dir, exist_ok=True)
    frames = []

    # pass 1: scene-change sampling (up to half the budget, min 1)
    scene_budget = max(1, max_frames // 2)
    scene_out = os.path.join(out_dir, "scene_%03d.jpg")
    r = tools.run_tool(
        ["ffmpeg", "-v", "error", "-i", path,
         "-vf", f"select='gt(scene,{scene_threshold})',"
                + _scale_filter(max_width),
         "-vsync", "vfr", "-frames:v", str(scene_budget),
         "-q:v", JPEG_QUALITY, "-y", scene_out],
        timeout_s=timeout_s)
    if r.get("ok"):
        for name in sorted(os.listdir(out_dir)):
            if name.startswith("scene_") and name.endswith(".jpg"):
                frames.append({"filename": name, "method": "scene_change",
                               "timestamp_s": None})

    # pass 2: interval sampling to fill the budget (deterministic)
    remaining = max_frames - len(frames)
    if remaining > 0:
        if duration_s and duration_s > 0:
            points = [round(duration_s * (i + 0.5) / remaining, 3)
                      for i in range(remaining)]
        else:
            points = [0.0]
        for i, ts in enumerate(points):
            name = f"interval_{i:03d}.jpg"
            out = os.path.join(out_dir, name)
            r2 = tools.run_tool(
                ["ffmpeg", "-v", "error", "-ss", str(ts), "-i", path,
                 "-frames:v", "1", "-vf", _scale_filter(max_width),
                 "-q:v", JPEG_QUALITY, "-y", out],
                timeout_s=timeout_s)
            if r2.get("ok") and os.path.isfile(out) and \
                    os.path.getsize(out) > 0:
                frames.append({"filename": name, "method": "interval",
                               "timestamp_s": ts})

    if not frames:
        return {"ok": False, "tool": tool, "frames": [],
                "error": "no frames could be extracted "
                         "(no video stream, or decode failure)"}
    return {"ok": True, "tool": tool, "frames": frames[:max_frames]}

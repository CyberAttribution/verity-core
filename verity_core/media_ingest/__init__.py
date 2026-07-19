"""
verity_core.media_ingest — media evidence extraction (v0.5.0, optional
[media] extra; system tools: ffprobe/ffmpeg, exiftool).

PORTABILITY CONTRACT (approved sprint requirement): this package is
runtime-neutral. It takes filesystem paths and returns plain dicts. It
imports no web framework, no job store, no model transport, and makes no
network calls. The hosted Verity backend and a future SwarmTracker Node
media-intake sprint consume it identically.

EVIDENCE-AND-ATTRIBUTION ORIENTATION (approved sprint requirement):
extracted metadata is treated as *evidence bearing on attribution
hypotheses* — source, timing, device, software, and processing history —
never as proof of authorship or manipulation by itself. The package
builder (package.py) records, for every source and derived artifact:
identifier, kind, cryptographic hash, acquisition time, embedded
timestamps, extraction tool + version, processing step, parent artifact,
and metadata-origin classification; it separates direct observations from
inferences and preserves uncertainty.

Tool availability is capability data, not an error: a missing tool is
recorded honestly in the evidence package so downstream analysis and the
report can state what was not examined.
"""
from . import audio, exif, frames, package, probe, tools  # noqa: F401

__all__ = ["tools", "probe", "exif", "frames", "audio", "package"]

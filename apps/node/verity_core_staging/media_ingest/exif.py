"""
media_ingest.exif — embedded metadata via ExifTool.

EXIF/XMP/QuickTime metadata is SOURCE-EMBEDDED evidence: timestamps,
device make/model, software, GPS presence, and edit history travel with
the file and bear on source, timing, device, and processing-history
attribution questions. It can be absent (platforms routinely strip it),
stale, or forged — its absence and its inconsistencies are themselves
evidence, but never proof, of anything.

A missing exiftool binary is a capability fact, not an error: the result
records it so the evidence package can state what was not examined.
"""
from . import tools

# Field groups worth carrying as evidence (bounded; exiftool emits
# hundreds of tags — we keep attribution-relevant ones + counts).
_TIMESTAMP_KEYS = (
    "CreateDate", "ModifyDate", "DateTimeOriginal", "MediaCreateDate",
    "MediaModifyDate", "TrackCreateDate", "TrackModifyDate",
    "FileModifyDate", "GPSDateStamp", "SubSecCreateDate")
_DEVICE_KEYS = ("Make", "Model", "Software", "Encoder", "HandlerVendorID",
                "CompressorName", "WriterApplication", "CreatorTool",
                "HistorySoftwareAgent", "DeviceManufacturer",
                "DeviceModelName")
_CONTEXT_KEYS = ("ImageWidth", "ImageHeight", "Duration", "MIMEType",
                 "FileType", "MajorBrand", "Artist", "Author", "Title",
                 "Copyright", "GPSLatitude", "GPSLongitude",
                 "GPSPosition")


def extract_exif(path, timeout_s=60):
    """Returns {ok, tool, timestamps, device_software, context,
    total_tags, gps_present} or {ok: False, tool, error, tool_missing}."""
    tool = {"name": "exiftool", "version": tools.tool_version("exiftool")}
    out = tools.run_json_tool(
        ["exiftool", "-json", "-api", "largefilesupport=1", "--", path],
        timeout_s=timeout_s)
    if not out.get("ok"):
        return {"ok": False, "tool": tool,
                "error": out.get("error") or "exiftool failed",
                "tool_missing": bool(out.get("tool_missing"))}
    rows = out.get("data") or []
    tags = rows[0] if rows and isinstance(rows[0], dict) else {}

    def pick(keys):
        return {k: str(tags[k])[:300] for k in keys if k in tags}

    timestamps = pick(_TIMESTAMP_KEYS)
    device = pick(_DEVICE_KEYS)
    context = pick(_CONTEXT_KEYS)
    return {
        "ok": True, "tool": tool,
        "timestamps": timestamps,
        "device_software": device,
        "context": context,
        "gps_present": any(k.startswith("GPS") for k in tags),
        "total_tags": len(tags),
    }

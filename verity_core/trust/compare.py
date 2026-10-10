"""Deterministic comparison of two verified trust media bundles.

Bounded by design: this module compares what the two bundles RECORD and
nothing else. It makes no claim about whether the underlying files show
the same scene, which file is the original, or whether any content was
altered; it never uses model interpretations, scoring, network access,
or optional external dependencies.

Inputs must be verified trust bundles (load_bundle output). Both are
re-verified here with the strict media_bundle APIs before any field is
trusted. Optional source files are rechecked by a caller-supplied
check_source callable (the CLI passes its exact bound-source
verification); a mismatch fails the comparison.

Comparison surface (all measured, all explicitly unknown when absent):
  - exact bound artifacts (binding payload selection, never a first-
    source heuristic): recorded sha256 and size
  - recorded container fields and duration delta (right - left, only
    when both are finite numbers; bool, nonfinite values, and
    unparseable strings are excluded from the arithmetic)
  - recorded streams, paired by recorded type + ordinal — DISPLAY
    ALIGNMENT ONLY, never proof that paired streams correspond; any
    unmatched or malformed stream entries are shown as-is. Field rows
    cover the union of what BOTH sides recorded for the slot, and
    stream tags are compared per key by value.
  - available attribution-relevant embedded metadata as recorded
  - tool availability and failed tool examinations

Unexpected recorded types (a container that is not an object, a stream
entry or stream_tags that is not an object, a tooling record that is
not an object) never crash the comparison and never get invented
values: they render as unknown/not examined or an explicit failed
comparison.
"""

import datetime
import math

from verity_core.trust import media_bundle


class CompareError(Exception):
    """Comparison refused (bundle failed verification, or a supplied
    source did not match its bound digest)."""


_CONTAINER_FIELDS = (
    "format_name", "format_long", "duration_s", "size_bytes",
    "bit_rate", "stream_count",
)
_VIDEO_FIELDS = ("codec", "codec_long", "width", "height", "fps",
                 "pix_fmt", "frames")
_AUDIO_FIELDS = ("codec", "codec_long", "channels", "sample_rate")


def _finite_number(value):
    """A finite int/float, or None. Excludes bool, NaN/inf, unparseable
    strings, and integers too large for float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _type_name(value):
    if value is None:
        return "absent"
    return type(value).__name__


def _opt_dict(value):
    return value if isinstance(value, dict) else None


def _opt_bool(value):
    return value if isinstance(value, bool) else None


def _opt_int(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _side_facts(bundle, side):
    """Extract the recorded comparison surface from one bundle without
    assuming any nested type. Missing/unexpected shapes become None and
    render as unknown later; nothing is invented."""
    pkg = bundle.get("package") if isinstance(bundle, dict) else {}
    pkg = _mapping(pkg)
    origin = _mapping(pkg.get("metadata_by_origin"))
    se = _mapping(origin.get("source_embedded"))
    tooling = pkg.get("tooling")
    tooling = tooling if isinstance(tooling, dict) else {}
    art_id, art = media_bundle.bound_source_artifact(bundle)
    art = art if isinstance(art, dict) else {}
    container = se.get("container")
    streams = se.get("streams")
    source = _mapping(pkg.get("source"))
    return {
        "side": side,
        "declared_label": source.get("identifier"),
        "acquired_utc": source.get("acquisition_time_utc"),
        "schema": pkg.get("schema"),
        "artifact_id": art_id,
        "sha256": art.get("sha256"),
        "size_bytes": art.get("size_bytes"),
        "container": container if isinstance(container, dict) else None,
        "container_raw_type": _type_name(container),
        "streams": streams if isinstance(streams, list) else None,
        "streams_raw_type": _type_name(streams),
        "container_tags": _opt_dict(se.get("container_tags")),
        "timestamps": _opt_dict(se.get("embedded_timestamps")),
        "device_software": _opt_dict(se.get("device_software")),
        "context": _opt_dict(se.get("embedded_context")),
        "gps_present": _opt_bool(se.get("gps_present")),
        "embedded_tag_count": _opt_int(se.get("embedded_tag_count")),
        "tooling": tooling,
    }


def _examined(facts, tool):
    """Whether the recorded examination by `tool` actually succeeded on
    this side; everything else is some form of 'not examined'."""
    entry = _mapping(facts["tooling"]).get(tool)
    if not isinstance(entry, dict):
        return "not recorded"
    if entry.get("available") is not True:
        return f"not examined ({tool} was not available)"
    if entry.get("ok") is not True:
        return f"not examined ({tool} examination failed)"
    return "examined"


def _same_value(left, right):
    """Strict recorded-value equality: a bool never equals a number
    (True is not 1, False is not 0), everything else compares with ==."""
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return left == right


def _cmp(left, right):
    """(status, detail) for one recorded field; absence is always
    explicit and never read as 'the value does not exist'."""
    if left is None and right is None:
        return "unknown", "not recorded on either side"
    if left is None:
        return "unknown", "not recorded on the left"
    if right is None:
        return "unknown", "not recorded on the right"
    if _same_value(left, right):
        return "same", ""
    return "differ", ""


def _row(field, left, right, delta=None):
    status, detail = _cmp(left, right)
    return {"field": field, "left": left, "right": right,
            "status": status, "detail": detail, "delta": delta}


def _duration_delta(left, right):
    """(delta, note) for container duration. delta is a finite float or
    None; bool, nonfinite values, and ALL strings (recorded durations
    must be JSON numbers to enter the arithmetic — strings are never
    parsed), and overflow-to-infinity results are excluded from the
    arithmetic and explained in the note."""
    lf = _finite_number(left)
    rf = _finite_number(right)
    if lf is None and rf is None:
        return None, ("duration delta not computed (duration is not a "
                      "finite number on either side)")
    if lf is None:
        return None, ("duration delta not computed (duration is not a "
                      "finite number on the left)")
    if rf is None:
        return None, ("duration delta not computed (duration is not a "
                      "finite number on the right)")
    delta = rf - lf
    if not math.isfinite(delta):
        return None, "duration delta not computed (nonfinite result)"
    return round(delta, 3), None


def _container_rows(left, right):
    rows = []
    for field in _CONTAINER_FIELDS:
        l = (left["container"] or {}).get(field)
        r = (right["container"] or {}).get(field)
        row = _row(f"container {field}", l, r)
        if field == "duration_s":
            delta, note = _duration_delta(l, r)
            row["delta"] = delta
            if note:
                row["detail"] = f"{row['detail']}; {note}" \
                    if row["detail"] else note
        rows.append(row)
    return rows


def _stream_groups(streams):
    """Group stream entries by their recorded type, preserving order.
    Non-object entries land in a separate 'invalid' bucket so they can
    be shown as failed comparisons without inventing structure."""
    groups = {}
    order = []
    invalid = []
    if not isinstance(streams, list):
        return groups, order, invalid
    for index, entry in enumerate(streams):
        if not isinstance(entry, dict):
            invalid.append((index, entry))
            continue
        kind = entry.get("type")
        key = kind if isinstance(kind, str) and kind else "untyped"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((index, entry))
    return groups, order, invalid


def _kind_fields(kind):
    if kind == "video":
        return _VIDEO_FIELDS
    if kind == "audio":
        return _AUDIO_FIELDS
    return ()


def _one_side_rows(tag, entry, which):
    """Rows for a stream slot present on only one side; the explicit
    counterpart row states the mismatch instead of inventing a pair."""
    side = "left" if which == "left" else "right"
    rows = []
    kind = entry.get("type")
    field_order = ("type", "codec") + tuple(
        f for f in _kind_fields(kind) if f not in ("type", "codec"))
    for field in field_order:
        value = entry.get(field)
        rows.append(_row(f"{tag} {field}",
                         value if which == "left" else None,
                         value if which == "right" else None))
    tags = entry.get("stream_tags")
    if isinstance(tags, dict):
        for k in sorted(tags):
            rows.append(_row(f"{tag} stream_tag {k}",
                             tags.get(k) if which == "left" else None,
                             tags.get(k) if which == "right" else None))
    elif tags is not None:
        note = f"unexpected recorded type: {_type_name(tags)}"
        rows.append(_row(f"{tag} stream_tags",
                         note if which == "left" else None,
                         note if which == "right" else None))
        rows[-1]["status"] = "failed comparison"
        rows[-1]["detail"] = f"stream_tags on the {side} are not an object"
    rows.append(_row(f"{tag} counterpart", "present" if which == "left"
                     else None, "present" if which == "right" else None))
    return rows


def _pair_rows(tag, l_entry, r_entry):
    """Rows for one aligned stream slot (both sides present). Fields
    are the union of what BOTH sides recorded for the slot, so a field
    present only on the right is still compared; stream tags are paired
    by key and compared by VALUE, so same keys with changed values
    differ. Tags absent (None) on one side are unrecorded: the per-key
    union continues against the other side's keys. The failed-comparison
    status is reserved for recorded tags that are not objects."""
    kind = l_entry.get("type")
    field_order = ("type", "codec") + tuple(
        f for f in _kind_fields(kind) if f not in ("type", "codec"))
    rows = []
    for field in field_order:
        rows.append(_row(f"{tag} {field}",
                         l_entry.get(field), r_entry.get(field)))
    l_raw = l_entry.get("stream_tags")
    r_raw = r_entry.get("stream_tags")
    l_bad = l_raw is not None and not isinstance(l_raw, dict)
    r_bad = r_raw is not None and not isinstance(r_raw, dict)
    if l_bad or r_bad:
        row = _row(
            f"{tag} stream_tags",
            f"unexpected recorded type: {_type_name(l_raw)}" if l_bad
            else ("recorded stream tags (object)"
                  if isinstance(l_raw, dict) else None),
            f"unexpected recorded type: {_type_name(r_raw)}" if r_bad
            else ("recorded stream tags (object)"
                  if isinstance(r_raw, dict) else None),
        )
        row["status"] = "failed comparison"
        bad_sides = [s for s, bad in (("left", l_bad), ("right", r_bad))
                     if bad]
        row["detail"] = ("stream_tags on the "
                         + " and the ".join(bad_sides)
                         + " are not an object")
        rows.append(row)
        return rows
    if l_raw is None and r_raw is None:
        rows.append(_row(f"{tag} stream_tags", None, None))
        return rows
    l_map = l_raw if isinstance(l_raw, dict) else {}
    r_map = r_raw if isinstance(r_raw, dict) else {}
    for k in sorted(set(l_map) | set(r_map)):
        rows.append(_row(f"{tag} stream_tag {k}",
                         l_map.get(k), r_map.get(k)))
    return rows


def _stream_rows(left, right):
    """Rows pair streams ONLY by recorded type + ordinal within that
    type — display alignment, never proof the paired streams
    correspond. Unmatched streams are shown with an explicit
    counterpart row; malformed entries are shown as failed
    comparisons."""
    lg, lo, li = _stream_groups(left["streams"])
    rg, ro, ri = _stream_groups(right["streams"])
    rows = []
    count_l = len(left["streams"]) \
        if isinstance(left["streams"], list) else None
    count_r = len(right["streams"]) \
        if isinstance(right["streams"], list) else None
    rows.append(_row("stream entry count", count_l, count_r))
    for key in sorted(set(lo) | set(ro)):
        l_entries = lg.get(key, [])
        r_entries = rg.get(key, [])
        for i in range(max(len(l_entries), len(r_entries))):
            tag = f"{key}[{i}]"
            l_entry = l_entries[i][1] if i < len(l_entries) else None
            r_entry = r_entries[i][1] if i < len(r_entries) else None
            if l_entry is None:
                rows.extend(_one_side_rows(tag, r_entry, "right"))
            elif r_entry is None:
                rows.extend(_one_side_rows(tag, l_entry, "left"))
            else:
                rows.extend(_pair_rows(tag, l_entry, r_entry))
    for index, entry in li:
        row = _row(f"stream entry {index} (left)",
                   f"unexpected recorded type: {_type_name(entry)}", None)
        row["status"] = "failed comparison"
        row["detail"] = "recorded stream entry is not an object"
        rows.append(row)
    for index, entry in ri:
        row = _row(f"stream entry {index} (right)", None,
                   f"unexpected recorded type: {_type_name(entry)}")
        row["status"] = "failed comparison"
        row["detail"] = "recorded stream entry is not an object"
        rows.append(row)
    return rows


def _tool_desc(entry):
    if not isinstance(entry, dict):
        if entry is None:
            return "not recorded"
        return f"unexpected recorded type: {_type_name(entry)}"
    if entry.get("available") is not True:
        return "not available"
    desc = "available"
    if entry.get("ok") is True:
        desc += ", examination ok"
    elif entry.get("ok") is False:
        desc += ", examination FAILED"
    version = entry.get("version")
    if isinstance(version, str) and version:
        desc += f" ({version})"
    elif version is not None:
        desc += f" (unexpected version type: {_type_name(version)})"
    return desc


def _tooling_rows(left, right):
    names = sorted(set(_mapping(left["tooling"]))
                   | set(_mapping(right["tooling"])))
    rows = []
    for name in names:
        l = _mapping(left["tooling"]).get(name)
        r = _mapping(right["tooling"]).get(name)
        rows.append(_row(f"tool {name}", _tool_desc(l), _tool_desc(r)))
    return rows


def _embedded_rows(left, right):
    """Attribution-relevant embedded metadata as recorded. A side whose
    exiftool examination did not succeed contributes nothing and is
    explicitly 'not examined'; container-level tags are compared for
    whichever sides recorded them."""
    rows = []
    for field in ("embedded_tag_count", "gps_present"):
        rows.append(_row(field, left.get(field), right.get(field)))
    l_exam = _examined(left, "exiftool") == "examined"
    r_exam = _examined(right, "exiftool") == "examined"
    groups = (
        ("tag", "container_tags", True, True),
        ("timestamp", "timestamps", l_exam, r_exam),
        ("device", "device_software", l_exam, r_exam),
        ("context", "context", l_exam, r_exam),
    )
    for prefix, key, l_ok, r_ok in groups:
        l_map = left[key] if l_ok else None
        r_map = right[key] if r_ok else None
        if l_map is None and r_map is None:
            continue
        keys = sorted(set(_mapping(l_map)) | set(_mapping(r_map)))
        for k in keys:
            row = _row(f"{prefix}: {k}", _mapping(l_map).get(k),
                       _mapping(r_map).get(k))
            notes = []
            if l_map is None and isinstance(left[key], dict):
                notes.append("on the left the value is recorded in the "
                             "bundle but not examined (exiftool "
                             "examination did not succeed)")
            if r_map is None and isinstance(right[key], dict):
                notes.append("on the right the value is recorded in the "
                             "bundle but not examined (exiftool "
                             "examination did not succeed)")
            if notes:
                row["detail"] = "; ".join(notes)
            rows.append(row)
    return rows


_MAX_CELL = 200


def _esc(value):
    """Markdown-safe cell text for untrusted recorded values. The RAW
    text is normalized and truncated BEFORE escaping so an escape
    sequence is never cut in half. Existing backslashes are doubled
    first; then ampersand and angle brackets (HTML), table pipes,
    brackets, and backticks are escaped — backticks are escaped rather
    than replaced, so the literal recorded value survives. A hostile
    filename or tag value cannot break the table, inject a code span,
    or forge a link."""
    text = str(value).replace("\r\n", " ").replace("\r", " ") \
        .replace("\n", " ")
    if len(text) > _MAX_CELL:
        text = text[: _MAX_CELL - 3] + "..."
    text = text.replace("\\", "\\\\")
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("|", "\\|")
    text = text.replace("[", "\\[").replace("]", "\\]")
    text = text.replace("`", "\\`")
    return text


def _cell(value):
    return _esc("(not recorded)") if value is None else _esc(value)


def _delta_cell(delta):
    if delta is None:
        return ""
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta} s"


def _table(rows):
    lines = ["| field | left | right | status | notes | delta |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(
            f"| {_esc(row['field'])} | {_cell(row['left'])} | "
            f"{_cell(row['right'])} | {_esc(row['status'])} | "
            f"{_esc(row['detail'])} | {_delta_cell(row['delta'])} |")
    return lines


_STATUS_TEXT = {
    True: "equal",
    False: "DIFFER",
    None: "unknown (no valid recorded value on at least one side)",
}


def render_markdown(report):
    """Render the comparison report as Markdown. All recorded values are
    escaped as untrusted; nothing is rendered from model output."""
    lines = []
    add = lines.append
    add("# Trust bundle comparison — recorded evidence only")
    add("")
    add(f"Generated (UTC): {_esc(report['generated_utc'])}")
    add("")
    add("Deterministic comparison of what two trust bundles RECORD. No "
        "AI judgment, no scoring, no network access. Absence of a "
        "difference below means absence of a difference in the recorded "
        "fields — nothing more.")
    add("")
    add("## Inputs, integrity, and declared labels")
    add("")
    add("| side | declared label (unverified) | package identity "
        "(recorded) | recorded schema | bundle integrity | source file |")
    add("|---|---|---|---|---|---|")
    for side in ("left", "right"):
        f = report[side]
        check = report["source_checks"][side]
        add(f"| {side} | {_cell(f['declared_label'])} | "
            f"{_cell(report[side + '_identity'])} | {_cell(f['schema'])} | "
            f"verified (package validity, record chain, package binding) "
            f"| {_esc(check['status'])} |")
    add("")
    add("The declared labels are the source identifiers carried in the "
        "packages; they are NOT verified provenance. Package identity is "
        "the SHA-256 of the full canonical package JSON bound into each "
        "record.")
    add("")
    add("## Bound source artifacts — recorded byte identity")
    add("")
    for side in ("left", "right"):
        f = report[side]
        add(f"- {side}: bound artifact {_cell(f['artifact_id'])}, recorded "
            f"sha256 {_cell(f['sha256'])}, recorded size "
            f"{_cell(f['size_bytes'])} bytes")
    add(f"- recorded sha256: {_STATUS_TEXT[report['sha256_equal']]}")
    add(f"- recorded size: {_STATUS_TEXT[report['size_equal']]}")
    add(f"- package canonical identity: "
        f"{'identical recorded package content' if report['identity_equal'] else 'packages DIFFER'}")
    add(f"- byte content: {_esc(report['bytes_verdict'])}")
    add("")
    add("Container fields, streams, and embedded metadata below were "
        "recorded by optional tools at creation time; a tool that was "
        "unavailable or failed leaves those fields unexamined, which is "
        "shown — never silently treated as absent.")
    add("")
    add("## Container (as recorded)")
    add("")
    for side in ("left", "right"):
        f = report[side]
        add(f"- {side} ffprobe: {report['probe_examination'][side]}")
        if f["container"] is None:
            add(f"- {side} container: not recorded or unexpected "
                f"recorded type ({f['container_raw_type']})")
    add("")
    lines.extend(_table(report["container_rows"]))
    add("")
    add("## Streams (as recorded)")
    add("")
    add("Streams are paired by recorded type and ordinal (the first "
        "recorded video stream with the first, the second with the "
        "second, and so on). This is DISPLAY ALIGNMENT ONLY — it is not "
        "proof that paired streams correspond to each other. Unmatched "
        "streams are shown explicitly.")
    add("")
    for side in ("left", "right"):
        f = report[side]
        if f["streams"] is None:
            add(f"- {side} streams: not recorded or unexpected recorded "
                f"type ({f['streams_raw_type']})")
    add("")
    lines.extend(_table(report["stream_rows"]))
    add("")
    add("## Embedded metadata (as recorded)")
    add("")
    for side in ("left", "right"):
        f = report[side]
        add(f"- {side} exiftool: {report['exif_examination'][side]}")
        if f["container"] is None:
            add(f"- {side} container tags: not recorded or unexpected "
                f"recorded type ({f['container_raw_type']})")
    if report["left"]["container_tags"] is None \
            and report["right"]["container_tags"] is None:
        add("- container tags: not recorded on either side")
    add("")
    lines.extend(_table(report["embedded_rows"]))
    if report["embedded_rows"]:
        add("")
    add("## Tooling (as recorded)")
    add("")
    lines.extend(_table(report["tooling_rows"]))
    add("")
    add("## Limits")
    add("")
    add("- Verification is relative to hashes retained inside each "
        "bundle itself; a complete rewrite with recomputed hashes would "
        "pass, and removing a trailing suffix of record entries passes.")
    add("- Identical recorded sha256 means the bound artifacts refer to "
        "identical byte content — it says nothing about provenance, "
        "authorship, or authenticity.")
    add("- Declared labels are unverified declarations carried in the "
        "packages; this report does not establish an original/copy "
        "relationship between the two files.")
    add("- Stream pairing is display alignment by recorded type and "
        "ordinal, not evidence that paired streams correspond.")
    add("- Values recorded as absent-but-unexamined are UNKNOWN, not "
        "proof that the metadata does not exist in the underlying "
        "media.")
    add(f"- Table cells longer than {_MAX_CELL} characters are "
        "truncated with ...; the exact recorded values are retained "
        "unchanged in the input bundles.")
    add(f"- {media_bundle.DISCLAIMER}")
    return "\n".join(lines) + "\n"


def compare_bundles(left, right, *, left_source=None, right_source=None,
                    check_source=None):
    """Compare two VERIFIED trust bundles. Returns a plain report dict;
    raises CompareError when either bundle fails strict verification or
    a supplied source does not match its bundle's bound digest.

    check_source(bundle, path) -> (exit_code, message) rechecks a
    supplied source file against the exact bound artifact; it is
    required whenever a source path is supplied.
    """
    bundles = {"left": left, "right": right}
    facts = {}
    for side, bundle in bundles.items():
        report = media_bundle.verify_bundle(bundle)
        if not report["verified"]:
            failed = [f"{label}: {'; '.join(report['issues'][key])}"
                      for key, label in
                      (("envelope", "envelope"), ("package", "package"),
                       ("chain", "record chain"), ("binding", "binding"))
                      if report["issues"][key]]
            raise CompareError(
                f"{side} bundle failed strict verification and will not "
                f"be compared ({'; '.join(failed)})")
        facts[side] = _side_facts(bundle, side)
    source_checks = {}
    if (left_source or right_source) and check_source is None:
        raise ValueError(
            "check_source is required to verify supplied sources")
    for side, path in (("left", left_source), ("right", right_source)):
        if path is None:
            source_checks[side] = {
                "supplied": False,
                "status": "not supplied — the recorded digest was not "
                          "rechecked against actual bytes",
            }
            continue
        rc, message = check_source(bundles[side], path)
        if rc != 0:
            raise CompareError(f"{side} source verification failed: "
                               f"{message}")
        source_checks[side] = {
            "supplied": True,
            "status": "supplied — rechecked, matches the bound digest",
        }
    identity = {side: media_bundle.compute_identity(bundles[side]["package"])
                for side in ("left", "right")}
    sha_l, sha_r = facts["left"]["sha256"], facts["right"]["sha256"]
    size_l = facts["left"]["size_bytes"]
    size_r = facts["right"]["size_bytes"]
    valid_size = lambda v: isinstance(v, int) and not isinstance(v, bool)
    if sha_l and sha_r:
        bytes_verdict = (
            "the bound artifacts carry the SAME recorded sha256, so the "
            "referred-to byte content is identical"
            if sha_l == sha_r else
            "the bound artifacts carry DIFFERENT recorded sha256 values, "
            "so the referred-to byte content differs")
    else:
        missing = ", ".join(
            f"{s} side has no valid recorded digest" for s, v in
            (("left", sha_l), ("right", sha_r)) if not v)
        bytes_verdict = f"byte content comparison is UNKNOWN ({missing})"
    return {
        "generated_utc": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "left": facts["left"],
        "right": facts["right"],
        "left_identity": identity["left"],
        "right_identity": identity["right"],
        "identity_equal": identity["left"] == identity["right"],
        "sha256_equal": (sha_l == sha_r) if (sha_l and sha_r) else None,
        "size_equal": (size_l == size_r) if (valid_size(size_l)
                                             and valid_size(size_r)) \
            else None,
        "bytes_verdict": bytes_verdict,
        "source_checks": source_checks,
        "container_rows": _container_rows(facts["left"], facts["right"]),
        "stream_rows": _stream_rows(facts["left"], facts["right"]),
        "embedded_rows": _embedded_rows(facts["left"], facts["right"]),
        "tooling_rows": _tooling_rows(facts["left"], facts["right"]),
        "probe_examination": {
            "left": _examined(facts["left"], "ffprobe"),
            "right": _examined(facts["right"], "ffprobe"),
        },
        "exif_examination": {
            "left": _examined(facts["left"], "exiftool"),
            "right": _examined(facts["right"], "exiftool"),
        },
    }

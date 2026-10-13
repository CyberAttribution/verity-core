"""
keel_events — Node-side Keel governance ledger for media-evidence
operations (v0.3.0, additive).

The OpenClaw ledger records model calls; it never sees local evidence
extraction or governed media retrieval. This append-only JSONL ledger
records those steps: intake, tool runs, retrieval attempts, cleanup —
with hashes and tool names, never media content and never full user
URLs beyond the canonical form.

Separate file from logs/openclaw_events.jsonl by design: that file is
runtime-owned and this UI never writes to it.
"""
import errno
import json
import math
import os
import time
import uuid
from pathlib import Path

try:
    import fcntl            # POSIX advisory locks (Linux/macOS)
except ImportError:         # pragma: no cover - non-POSIX fallback
    fcntl = None

from . import config

MEDIA_LEDGER_PATH = Path(os.environ.get(
    "NODE_UI_MEDIA_LEDGER",
    str(config.PROJECT_ROOT / "logs" / "keel_media_events.jsonl")))


class LedgerWriteError(Exception):
    """A MANDATORY ledger append failed.

    Raised for: invalid event/detail shape, non-serializable detail
    content (including objects json cannot encode — never silently
    stringified), and any open/write/flush/fsync failure (OSError,
    including short/partial writes) — file OR parent-directory sync.
    Also raised when POSIX flock support (fcntl) is unavailable, since
    the mandatory path refuses to append without record-integrity
    guarantees. Callers that must not proceed without a persisted
    authorization record must catch/propagate this and fail closed."""


def _fsync_dir(dir_path):
    """Best-effort fsync of a directory fd so a NEW file's directory
    entry survives a crash. Fail-soft paths use this: errors are
    swallowed. The mandatory path uses _fsync_dir_strict instead."""
    try:
        _fsync_dir_strict(dir_path)
    except OSError:
        pass


def _fsync_dir_strict(dir_path):
    """fsync a directory fd, propagating OSError (normalized to
    LedgerWriteError by the mandatory wrapper). Note: fsync(file)
    alone does NOT guarantee the filename survives, even when the
    parent directory already existed. If the ledger path has newly
    created ancestor directories, their own entries are NOT synced
    here (deeper ancestry would need walking); the mandatory-path
    docstring states this limitation."""
    dfd = os.open(str(dir_path), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _locked_append(line, require_dir_sync=False):
    """Append one already-serialized line under a shared POSIX advisory
    lock (flock), for BOTH the mandatory and fail-soft paths, so
    concurrent writers cannot interleave partial records. The lock is
    advisory: writers that bypass this module get no protection. It is
    single-host only (no NFS guarantee) — multi-host contention is
    unsupported and documented, not solved.

    With require_dir_sync=True (mandatory path) flock support is
    REQUIRED: if fcntl is unavailable this raises LedgerWriteError
    instead of appending without integrity guarantees, and the parent
    directory sync is strict (OSError propagates). The fail-soft path
    (require_dir_sync=False) appends locklessly when fcntl is missing
    and keeps directory sync best-effort.

    Mandatory path also verifies the pre-existing TAIL under the same
    lock before writing: a prior short/failed write can leave an
    unterminated or unreadable trailing line. Appending after such a
    tail would fuse the new record into it (fsync would still "succeed"
    while read_events sees zero matching rows), so a mandatory append
    FAILS CLOSED there with a clear repair reason. No bytes are
    written and no records are truncated."""
    if require_dir_sync and fcntl is None:
        raise LedgerWriteError(
            "POSIX flock support (fcntl) is unavailable; refusing a "
            "mandatory ledger append without record-integrity guarantees")
    MEDIA_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    f = MEDIA_LEDGER_PATH.open("a", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        if require_dir_sync:
            _verify_complete_tail_locked()
        n = f.write(line)
        if n != len(line):
            # Text-mode write() may return fewer characters only on
            # exotic failures; a short write must not pass as durable.
            raise OSError(errno.EIO,
                          f"short write: {n} of {len(line)} chars")
        f.flush()
        os.fsync(f.fileno())
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        f.close()
    if require_dir_sync:
        _fsync_dir_strict(MEDIA_LEDGER_PATH.parent)
    else:
        _fsync_dir(MEDIA_LEDGER_PATH.parent)


def _verify_complete_tail_locked():
    """Called under the append flock on the mandatory path only: the
    existing file must end with a newline and its last non-empty line
    must parse as JSON, so the row about to be appended lands on a
    fresh, separately readable line. Raises LedgerWriteError (no bytes
    written, nothing truncated) when a prior partial/failed write left
    an unterminated or unreadable trailing record."""
    try:
        size = MEDIA_LEDGER_PATH.stat().st_size
    except OSError as exc:
        raise LedgerWriteError(
            f"ledger size check failed: {exc}") from exc
    if not size:
        return
    with MEDIA_LEDGER_PATH.open("rb") as rf:
        rf.seek(size - 1)
        if rf.read(1) != b"\n":
            raise LedgerWriteError(
                "ledger ends with an UNTERMINATED partial record from a "
                "prior failed write (no trailing newline); a new row would "
                "fuse into it and be unreadable — refusing the mandatory "
                "append. Repair or archive the ledger manually; no bytes "
                "were written and no records were truncated")
        # Read backwards until the complete last non-empty row is found.
        # A valid row larger than one chunk must not look like a torn row.
        window_end, tail = size, b""
        while window_end:
            window_start = max(0, window_end - 65536)
            rf.seek(window_start)
            tail = rf.read(window_end - window_start) + tail
            stripped = tail.rstrip()
            if b"\n" in stripped or window_start == 0:
                last = stripped.rsplit(b"\n", 1)[-1].strip()
                break
            window_end = window_start
    if last:
        try:
            from verity_core.json_safety import bounded_json_loads
            bounded_json_loads(last.decode("utf-8", "replace"))
        except (ValueError, RecursionError) as exc:
            raise LedgerWriteError(
                "ledger ends with an UNREADABLE partial record from a "
                f"prior failed write ({exc}); a new row appended after "
                "it could not be separately verified — refusing the "
                "mandatory append. Repair or archive the ledger "
                "manually; no bytes were written and no records were "
                "truncated")


def _validate_shapes(event: str, detail):
    if not isinstance(event, str) or not event.strip():
        raise LedgerWriteError("event name must be a non-empty string")
    if detail is not None and not isinstance(detail, dict):
        raise LedgerWriteError("detail must be a dict or None")


def append_event(event: str, detail: dict = None) -> str:
    """Append one governance event. Returns the event id. Fail-soft:
    a ledger write problem never blocks evidence handling (the evidence
    package's processing_events remain the authoritative record)."""
    eid = "kme-" + uuid.uuid4().hex[:12]
    row = {
        "id": eid,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "layer": "node_ui_keel",
        "policy_version": None,
        "detail": {k: v for k, v in (detail or {}).items()
                   if v is not None},
    }
    try:
        from . import keel_policy
        row["policy_version"] = keel_policy.policy_summary()["version"]
    except Exception:
        pass
    try:
        _locked_append(json.dumps(row, default=str) + "\n")
    except Exception:
        pass
    return eid


def _check_finite(obj):
    """Reject nonfinite floats (NaN/Inf) that JSON cannot round-trip
    losslessly. json.dumps(allow_nan=False) also enforces this, but an
    explicit walk gives a clearer error before any file is opened.
    Raises LedgerWriteError (callers normalize everything to that).
    Cycle-safe: containers are tracked by id() so a self-referential
    structure is reported instead of recursing to RecursionError."""
    seen = set()
    try:
        _walk(obj, seen)
    except RecursionError:
        raise LedgerWriteError(
            "ledger detail nests too deeply to serialize")


def _walk(obj, seen):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise LedgerWriteError(
                f"nonfinite float {obj!r} is not ledger-serializable")
    elif isinstance(obj, dict):
        if id(obj) in seen:
            raise LedgerWriteError(
                "cyclic ledger detail is not serializable")
        seen.add(id(obj))
        try:
            for k, v in obj.items():
                if not isinstance(k, str):
                    raise LedgerWriteError(
                        "ledger detail keys must be strings")
                _walk(v, seen)
        finally:
            seen.discard(id(obj))
    elif isinstance(obj, (list, tuple)):
        if id(obj) in seen:
            raise LedgerWriteError(
                "cyclic ledger detail is not serializable")
        seen.add(id(obj))
        try:
            for v in obj:
                _walk(v, seen)
        finally:
            seen.discard(id(obj))


def append_event_mandatory(event: str, detail: dict = None) -> str:
    """Append one governance event that MUST reach stable storage before
    the caller proceeds. Used for cloud-escalation authorization records
    (Task 3): a cloud SDK generate call must never run without a
    durably persisted authorization row.

    Differences from append_event:
    - validates event/detail shapes (event: non-empty string; detail:
      dict or None) and raises LedgerWriteError otherwise;
    - serializes the full row BEFORE opening the file, so a
      serialization failure cannot leave a partial row on disk; there
      is NO default=str fallback — an object json cannot encode
      (e.g. a set or a custom class) raises LedgerWriteError instead
      of being silently stringified;
    - normalizes every expected failure (shape/type errors, recursion
      during serialization, and OSError from open/write/flush/fsync/dir
      fsync, including short writes) to LedgerWriteError;
    - flushes AND fsyncs the file handle before returning;
    - appends under an exclusive flock (see _locked_append): flock
      support (fcntl) is REQUIRED here — if it is unavailable this
      raises LedgerWriteError instead of appending without
      record-integrity guarantees;
    - performs a STRICT parent-directory fsync after the append
      (OSError there is a failure, not best-effort);
    - verifies, under the append lock and BEFORE writing, that the
      existing file ends with a complete, readable JSONL row (see
      _verify_complete_tail_locked). A prior short/failed write can
      leave an unterminated trailing line; appending after it would
      fuse the new record into it — the write would fsync "successfully"
      and return an id while read_events sees ZERO matching rows. The
      mandatory path fails closed there instead, with a repair reason;
      no bytes are written and no records are truncated.

    PERSISTENCE GUARANTEES (precise): serialization-before-open is the
    ONLY no-bytes guarantee — a failure during serialization leaves no
    partial bytes anywhere. I/O failures during open/write/flush/fsync
    MAY leave partial or unconfirmed bytes on disk even when the call
    raises LedgerWriteError (e.g. a short write, a failed fsync after
    accepted bytes, or an abrupt process death mid-write); callers
    should treat any LedgerWriteError as "row not durably confirmed,
    file state unknown but unchanged by THIS attempt's serialization".
    On success the row bytes were
    accepted by write() and flushed through fsync() on this file
    descriptor, AND the parent directory was fsynced afterwards so the
    FILENAME entry for a newly created file is also durable (on a
    pre-existing directory). If the mkdir() in this call created NEW
    ANCESTOR directories, their directory entries were NOT fsynced —
    a crash could still lose the whole path on some filesystems; that
    deeper ancestry is documented as out of scope, not solved. This is
    a filesystem-level durability ack — it is NOT proof of
    immutability, tamper-resistance, or any cryptographic property,
    and no authenticated signer is implied. Later writers can still
    modify the file; read_events is best-effort re-reading, not a
    tamper-evident log.

    CONCURRENCY: mandatory AND fail-soft appends both take an
    exclusive POSIX advisory flock for the duration of write+fsync, so
    full records from THIS module do not interleave on a single host.
    This is advisory (other tools writing the file get no protection)
    and offers no NFS/multi-host guarantee — multi-host contention is
    unsupported.

    The returned event id is generated locally (uuid4) and proves no
    identity — it is only a correlation handle."""
    eid = "kme-" + uuid.uuid4().hex[:12]
    _validate_shapes(event, detail)
    row = {
        "id": eid,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "layer": "node_ui_keel",
        "policy_version": None,
        "detail": {k: v for k, v in (detail or {}).items()
                   if v is not None},
    }
    try:
        from . import keel_policy
        row["policy_version"] = keel_policy.policy_summary()["version"]
    except Exception:
        # Policy summary is metadata enrichment; the authorization
        # row itself must still persist, so this stays non-fatal here.
        pass
    # Serialize BEFORE touching the filesystem: a bad row must fail
    # without leaving partial bytes anywhere. No default=str: a
    # non-serializable detail is a caller bug we surface, not mask.
    # _check_finite is cycle-safe and raises LedgerWriteError directly;
    # json.dumps can still hit RecursionError on very deep (non-cyclic)
    # nesting, so it is normalized here too.
    try:
        _check_finite(row)
        line = json.dumps(row, allow_nan=False) + "\n"
    except LedgerWriteError:
        raise
    except (TypeError, ValueError, RecursionError) as exc:
        raise LedgerWriteError(
            f"ledger row is not JSON-serializable: {exc}") from exc
    try:
        _locked_append(line, require_dir_sync=True)
    except LedgerWriteError:
        raise
    except OSError as exc:
        raise LedgerWriteError(
            f"ledger append failed: {exc}") from exc
    return eid


def read_events(limit: int = 50) -> list:
    if not MEDIA_LEDGER_PATH.exists():
        return []
    rows = []
    try:
        with MEDIA_LEDGER_PATH.open("r", encoding="utf-8",
                                    errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return list(reversed(rows[-limit:]))


def count_events() -> int:
    if not MEDIA_LEDGER_PATH.exists():
        return 0
    try:
        with MEDIA_LEDGER_PATH.open("r", encoding="utf-8",
                                    errors="replace") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0

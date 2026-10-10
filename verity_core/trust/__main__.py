"""Minimal offline CLI for trust media bundles.

    python -m verity_core.trust create FILE --out BUNDLE.json [--overwrite]
    python -m verity_core.trust verify BUNDLE.json [--source FILE]
    python -m verity_core.trust compare LEFT.json RIGHT.json
        [--left-source FILE --right-source FILE] [--out REPORT.md]
        [--overwrite]
    python -m verity_core.trust demo [--out PATH] [--overwrite] [--corrupt]

`create` reads the source file read-only in a single streaming pass (full
SHA-256 plus actual byte count), folds in real ffprobe/exiftool output when
those optional binaries are present, and truthfully records their absence
otherwise (integrity generation never depends on them). The package is
validated and the canonical full-package hash is bound into the trust
record exactly as in media_bundle; the bundle is then published atomically
and `--out` is never allowed to be the source file itself.

The verify command reports three verdicts separately — package validity,
record chain integrity, and package binding — and exits nonzero unless all
three hold. With --source it additionally compares the file's bytes against
the digest bound in the bundle; a mismatch is reported as a content
mismatch, never as a verdict about authenticity. Nothing here verifies
authorship or media authenticity.

`compare` deterministically compares two VERIFIED bundles: both are
re-verified with the strict media_bundle APIs first, optional source files
are rechecked against each bundle's exact bound artifact, and the report
covers recorded container fields (with a duration delta), streams paired
by recorded type + ordinal (display alignment only), attribution-relevant
embedded metadata, and tool examination status. Untrusted labels and
metadata are escaped for Markdown; the report is printed to stdout and
optionally written atomically via --out, which may not alias any input.
The comparison draws no conclusion about scene content, provenance, or
authenticity.
"""

import argparse
import hashlib
import os
import stat
import sys
import tempfile

from verity_core.trust import compare as compare_module
from verity_core.trust import media_bundle

_LABELS = (
    ("envelope", "bundle envelope"),
    ("package", "package validity"),
    ("chain", "record chain integrity"),
    ("binding", "package binding"),
)


def _print_verdicts(report):
    for key, label in _LABELS:
        status = "ok" if not report["issues"][key] else "FAIL"
        print(f"{label:<24} {status}")
        for issue in report["issues"][key]:
            print(f"  - {issue}")


def _print_report(report, bundle_path=None):
    if bundle_path:
        print(f"bundle: {bundle_path}")
    _print_verdicts(report)
    print(("VERIFIED: " if report["verified"] else "NOT VERIFIED: ")
          + media_bundle.DISCLAIMER)


def _cmd_verify(args):
    try:
        bundle = media_bundle.load_bundle(args.bundle)
    except (OSError, ValueError) as exc:
        print(f"cannot inspect {args.bundle}: {exc}")
        return 1
    report = media_bundle.verify_bundle(bundle)
    if args.source:
        return _verify_with_source(bundle, report, args)
    _print_report(report, bundle_path=args.bundle)
    return 0 if report["verified"] else 1


def _verify_with_source(bundle, report, args):
    """Bundle checks and source status are clearly scoped: no overall
    verdict is printed until the --source comparison has been made."""
    print(f"bundle: {args.bundle}")
    _print_verdicts(report)
    if not report["verified"]:
        print("source not evaluated because the bundle does not verify")
        print("NOT VERIFIED: " + media_bundle.DISCLAIMER)
        return 1
    rc, message = _check_bound_source(bundle, args.source)
    print(message)
    if rc == 0:
        print("VERIFIED (bundle and bound source): "
              + media_bundle.DISCLAIMER)
        return 0
    print("NOT VERIFIED: " + media_bundle.DISCLAIMER)
    return 1


def _check_source_input(path):
    """Clear rejection of nonexistent, unreadable, and nonregular inputs."""
    try:
        st = os.stat(path)
    except OSError:
        return False, f"no such file: {path}"
    if not stat.S_ISREG(st.st_mode):
        return False, f"not a regular file: {path}"
    if not os.access(path, os.R_OK):
        return False, f"not readable: {path}"
    return True, None


def _same_path(a, b):
    if os.path.exists(a) and os.path.exists(b):
        try:
            return os.path.samefile(a, b)
        except OSError:
            pass
    return os.path.abspath(a) == os.path.abspath(b)


def _stat_signature(path):
    st = os.stat(path)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _stream_identity(path, chunk=1024 * 256):
    """Single read-only pass: full SHA-256 plus the actual number of bytes
    read (not what the directory entry claims)."""
    h = hashlib.sha256()
    total = 0
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
            total += len(block)
    return h.hexdigest(), total


def _cmd_create(args):
    from verity_core.media_ingest import exif, package, probe

    source = args.source
    ok, err = _check_source_input(source)
    if not ok:
        print(f"create: {err}")
        return 1
    if _same_path(source, args.out):
        print(f"create: --out {args.out} is the source file itself; "
              f"refusing to overwrite the original")
        return 1
    try:
        st_before = _stat_signature(source)
        digest, size = _stream_identity(source)
        st_after = _stat_signature(source)
    except OSError as exc:
        print(f"create: cannot read {source}: {exc}")
        return 1
    if st_before != st_after or size != st_after[2]:
        print("create: source changed during hashing; refusing to bind "
              "an unstable file")
        return 1

    basename = os.path.basename(source)
    ext = os.path.splitext(basename)[1].lstrip(".").lower() or None
    pkg = package.new_package(
        "local_file",
        basename,
        declared={"original_filename": basename},
    )
    artifact_id = package.add_artifact(
        pkg,
        "source_media",
        sha256=digest,
        size_bytes=size,
        step="intake",
        label="locally supplied source file",
    )
    probe_result = probe.probe_media(source)
    package.attach_probe(pkg, probe_result, artifact_id)
    exif_result = exif.extract_exif(source)
    package.attach_exif(pkg, exif_result, artifact_id)
    try:
        unchanged = _stat_signature(source) == st_after
    except OSError as exc:
        print(f"create: source vanished during metadata collection "
              f"({exc.__class__.__name__}); refusing to bind")
        return 1
    if not unchanged:
        print("create: source changed during ingestion; refusing to bind")
        return 1
    package.derive_observations(pkg, declared_extension=ext)
    package.derive_recommended_corroboration(pkg)
    package.finalize(pkg, "completed", deleted_artifact_ids=[])

    issues = package.validate(pkg)
    if issues:
        print("create: package validation failed:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    rec = media_bundle.bind_package(pkg)
    try:
        media_bundle.save_bundle(args.out, pkg, rec,
                                 overwrite=args.overwrite)
    except FileExistsError:
        print(f"create: {args.out} already exists; pass --overwrite "
              f"to replace it")
        return 1
    except OSError as exc:
        print(f"create: cannot write {args.out}: {exc}")
        return 1
    print(f"create: wrote bundle {args.out}")
    print(f"create: source identity: sha256={digest} size={size} bytes")
    for name, result in (("ffprobe", probe_result),
                         ("exiftool", exif_result)):
        if result.get("ok"):
            version = (result.get("tool") or {}).get("version") \
                or "unknown version"
            print(f"create: {name}: metadata recorded ({version})")
        elif result.get("tool_missing"):
            print(f"create: {name}: not available on PATH; that evidence "
                  f"was not examined (recorded in the package)")
        else:
            print(f"create: {name}: failed ({result.get('error')}); "
                  f"recorded in the package")
    report = media_bundle.verify_file(args.out)
    _print_report(report)
    return 0 if report["verified"] else 1


# Shared implementation (public in media_bundle; kept under the same name
# here so existing tests and callers keep working).
_bound_source_artifact = media_bundle.bound_source_artifact


def _check_bound_source(bundle, source):
    """Compare a file's actual bytes against the digest bound in the
    bundle. Returns (exit_code, message); message may be multiline."""
    ok, err = _check_source_input(source)
    if not ok:
        return 1, f"source: {err}"
    art_id, art = _bound_source_artifact(bundle)
    if art is None:
        return 1, ("source: bundle contains no valid evidence_recorded "
                   "binding with a source artifact digest; --source "
                   "cannot be checked")
    size = art.get("size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return 1, (f"source: bound artifact {art_id} has no valid integer "
                   f"byte size; --source cannot be checked")
    try:
        st_before = _stat_signature(source)
        actual_digest, actual_size = _stream_identity(source)
        st_after = _stat_signature(source)
    except OSError as exc:
        return 1, f"source: cannot read {source}: {exc}"
    if st_before != st_after or actual_size != st_after[2]:
        return 1, ("source: file changed while being read; cannot "
                   "compare it against the bound digest")
    digest_ok = actual_digest == art["sha256"]
    size_ok = actual_size == size
    if digest_ok and size_ok:
        return 0, (f"source: matches the digest bound in the bundle "
                   f"(sha256={art['sha256']}, {actual_size} bytes)")
    lines = ["source: DOES NOT match the digest bound in the bundle:"]
    if not digest_ok:
        lines.append(f"  - sha256 expected {art['sha256']}, "
                     f"actual {actual_digest}")
    if not size_ok:
        lines.append(f"  - size expected {size} bytes, "
                     f"actual {actual_size} bytes")
    lines.append("  the file's bytes differ from what was bound at "
                 "creation; this is a content mismatch, not a finding "
                 "about the file's authenticity")
    return 1, "\n".join(lines)


def _build_demo_package():
    """Build an honest demo package: a small generated WAV is hashed from
    disk, no tool observations are fabricated, and the temporary source is
    really deleted before the completed cleanup is recorded."""
    import math
    import struct
    import wave
    from verity_core.media_ingest import package

    workdir = tempfile.mkdtemp(prefix="trust-demo-")
    source_path = os.path.join(workdir, "specimen.wav")
    with wave.open(source_path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 8000)))
            for i in range(800)
        ))
    pkg = package.new_package(
        "upload",
        "specimen.wav",
        declared={"original_filename": "specimen.wav"},
    )
    artifact_id = package.add_artifact(
        pkg,
        "source_media",
        path=source_path,
        size_bytes=os.path.getsize(source_path),
        step="intake",
        label="uploaded source",
    )
    package.derive_observations(pkg, declared_extension="wav")
    os.unlink(source_path)
    package.finalize(pkg, "completed", deleted_artifact_ids=[artifact_id])
    return pkg, workdir


def _cmd_demo(args):
    pkg, workdir = _build_demo_package()
    rec = media_bundle.bind_package(pkg)
    out_path = args.out or os.path.join(workdir, "specimen.trust.json")
    if args.out:
        os.rmdir(workdir)  # source was deleted; output lives elsewhere
    try:
        media_bundle.save_bundle(out_path, pkg, rec, overwrite=args.overwrite)
    except FileExistsError:
        print(f"demo: {out_path} already exists; pass --overwrite to replace it")
        return 1
    except OSError as exc:
        print(f"demo: cannot write {out_path}: {exc}")
        return 1
    print(f"demo: wrote bundle {out_path}")
    print("demo: source media is a generated 0.1 s WAV in a temporary "
          "directory; its sha256 and size are recorded from disk, no tool "
          "observations are fabricated, and the temporary source was "
          "deleted after hashing (recorded in the package cleanup). The "
          "saved bundle remains verifiable after that deletion.")
    report = media_bundle.verify_file(out_path)
    _print_report(report)
    if args.corrupt:
        import copy

        tampered = copy.deepcopy(media_bundle.load_bundle(out_path))
        tampered["package"]["artifacts"][0]["sha256"] = "f" * 64
        tampered_report = media_bundle.verify_bundle(tampered)
        print("demo: after corrupting the bound artifact sha256 in the "
              "package copy:")
        _print_report(tampered_report)
        if tampered_report["verified"]:
            print("demo: corruption was NOT detected (bug)")
            return 1
        print("demo: corruption was detected; original file on disk is "
              "untouched")
    return 0 if report["verified"] else 1


def _aliases_output(out_path, *inputs):
    """True when out_path would alias any input file (same file via
    hardlink/symlink, or the same path spelled differently)."""
    return any(_same_path(out_path, p) for p in inputs if p)


def _write_report(path, text, overwrite=False):
    """Publish a report atomically. Without --overwrite the target is
    never replaced or linked-over (no-clobber, including hardlink
    collisions and a target that appears concurrently); with it the
    file is replaced via a same-directory temporary file. Any link
    failure fails closed: the temporary file is removed and the target
    is left untouched — a link error can never degrade into a rename
    that replaces the target."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".verity-report-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)
            os.unlink(tmp)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cmd_compare(args):
    inputs = (args.left, args.right, args.left_source, args.right_source)
    if args.out and _aliases_output(args.out, *inputs):
        print("compare: --out would overwrite an input file; choose a "
              "different report path")
        return 1
    bundles = {}
    for side, path in (("left", args.left), ("right", args.right)):
        try:
            bundles[side] = media_bundle.load_bundle(path)
        except (OSError, ValueError) as exc:
            print(f"compare: cannot load {side} bundle {path}: {exc}")
            return 1
    try:
        report = compare_module.compare_bundles(
            bundles["left"], bundles["right"],
            left_source=args.left_source,
            right_source=args.right_source,
            check_source=_check_bound_source,
        )
    except compare_module.CompareError as exc:
        print(f"compare: {exc}")
        return 1
    text = compare_module.render_markdown(report)
    print(text, end="")
    if args.out:
        try:
            _write_report(args.out, text, overwrite=args.overwrite)
        except FileExistsError:
            print(f"compare: {args.out} already exists; pass --overwrite "
                  f"to replace it")
            return 1
        except OSError as exc:
            print(f"compare: cannot write {args.out}: {exc}")
            return 1
        print(f"compare: wrote report {args.out}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m verity_core.trust",
        description=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser(
        "create", help="ingest a real file and save a bound bundle")
    create.add_argument("source", help="path to the source media file")
    create.add_argument("--out", required=True,
                        help="output bundle path (must differ from source)")
    create.add_argument("--overwrite", action="store_true",
                        help="replace an existing output file")
    create.set_defaults(func=_cmd_create)
    verify = sub.add_parser("verify", help="verify a bundle file")
    verify.add_argument("bundle", help="path to a trust media bundle JSON")
    verify.add_argument("--source", default=None,
                        help="optional file to compare against the bound "
                             "source digest")
    verify.set_defaults(func=_cmd_verify)
    compare = sub.add_parser(
        "compare",
        help="compare two verified trust bundles (recorded evidence only)")
    compare.add_argument("left", help="path to the left trust bundle JSON")
    compare.add_argument("right", help="path to the right trust bundle JSON")
    compare.add_argument("--left-source", default=None,
                         help="optional file to recheck against the left "
                              "bundle's bound digest")
    compare.add_argument("--right-source", default=None,
                         help="optional file to recheck against the right "
                              "bundle's bound digest")
    compare.add_argument("--out", default=None,
                         help="write the Markdown report to this path "
                              "(also printed to stdout)")
    compare.add_argument("--overwrite", action="store_true",
                         help="replace an existing report file")
    compare.set_defaults(func=_cmd_compare)
    demo = sub.add_parser("demo", help="build, bind, save, and verify a demo bundle")
    demo.add_argument("--out", default=None,
                      help="output path (default: a fresh temporary file)")
    demo.add_argument("--overwrite", action="store_true",
                      help="replace an existing output file")
    demo.add_argument("--corrupt", action="store_true",
                      help="also verify an in-memory corrupted copy (must fail)")
    demo.set_defaults(func=_cmd_demo)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

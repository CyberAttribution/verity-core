"""Minimal offline inspection CLI for trust media bundles.

    python -m verity_core.trust verify BUNDLE.json
    python -m verity_core.trust demo [--out PATH] [--overwrite] [--corrupt]

The verify command reports three verdicts separately — package validity,
record chain integrity, and package binding — and exits nonzero unless all
three hold. It never claims to verify authorship or media authenticity.
"""

import argparse
import os
import sys
import tempfile

from verity_core.trust import media_bundle

_LABELS = (
    ("envelope", "bundle envelope"),
    ("package", "package validity"),
    ("chain", "record chain integrity"),
    ("binding", "package binding"),
)


def _print_report(report, bundle_path=None):
    if bundle_path:
        print(f"bundle: {bundle_path}")
    for key, label in _LABELS:
        status = "ok" if not report["issues"][key] else "FAIL"
        print(f"{label:<24} {status}")
        for issue in report["issues"][key]:
            print(f"  - {issue}")
    print(("VERIFIED: " if report["verified"] else "NOT VERIFIED: ")
          + media_bundle.DISCLAIMER)


def _cmd_verify(args):
    try:
        report = media_bundle.verify_file(args.bundle)
    except (OSError, ValueError) as exc:
        print(f"cannot inspect {args.bundle}: {exc}")
        return 1
    _print_report(report, bundle_path=args.bundle)
    return 0 if report["verified"] else 1


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


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m verity_core.trust",
        description=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="verify a bundle file")
    verify.add_argument("bundle", help="path to a trust media bundle JSON")
    verify.set_defaults(func=_cmd_verify)
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

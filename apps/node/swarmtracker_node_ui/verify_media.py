"""
verify_media — SwarmTracker Node v0.3.0 M1 verification gate.

Run this ON THE M1 before recording the YC demo. It exercises the real
application (real local model if OpenClaw/Ollama are up, deterministic
fallback otherwise) with locally synthesized specimens for all three
media families, and prints checklist-format PASS/FAIL lines matching
YC_DEMO_VERIFICATION_CHECKLIST.md.

Usage (from the project root, venv active):
    python -m swarmtracker_node_ui.verify_media
    python -m swarmtracker_node_ui.verify_media --youtube \
        "https://www.youtube.com/watch?v=<short-cc-video>"

Uses a THROWAWAY database and media ledger in a temp dir — it never
touches data/swarmtracker_node.db or the real ledgers.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

# throwaway persistence BEFORE importing the app
_TMP = tempfile.mkdtemp(prefix="stnode-verify-")
os.environ["NODE_UI_DATA_DIR"] = _TMP
os.environ["NODE_UI_DB"] = os.path.join(_TMP, "verify.db")
os.environ["NODE_UI_MEDIA_LEDGER"] = os.path.join(_TMP, "kme.jsonl")

RESULTS = []


def check(name, ok, note=""):
    RESULTS.append((name, bool(ok), note))
    print(("PASS  " if ok else "FAIL  ") + name + ("  — " + note if note
                                                   else ""))
    return ok


def synth(kind, dest):
    """Synthesize one specimen with ffmpeg. Returns path or None."""
    out = os.path.join(dest, {
        "video": "verify.mp4", "audio": "verify.mp3",
        "image": "verify.jpg"}[kind])
    argv = {
        "video": ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                  "testsrc2=duration=4:size=480x270:rate=12",
                  "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                  "-shortest", "-pix_fmt", "yuv420p", "-y", out],
        "audio": ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                  "sine=frequency=440:duration=3",
                  "-c:a", "libmp3lame", "-y", out],
        "image": ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                  "testsrc2=duration=1:size=480x270:rate=1",
                  "-frames:v", "1", "-y", out],
    }[kind]
    try:
        subprocess.run(argv, check=True, timeout=60)
        return out if os.path.getsize(out) > 0 else None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--youtube", default="",
                    help="optional real YouTube URL for a live "
                         "governed-retrieval check")
    ap.add_argument("--upload", default="",
                    help="optional path to a REAL media file (e.g. a "
                         "real MP4) to verify in addition to synthetic "
                         "specimens")
    ap.add_argument("--escalate", action="store_true",
                    help="optional LIVE Vertex AI escalation check "
                         "(requires ADC + GOOGLE_CLOUD_PROJECT; sends the "
                         "synthetic specimen's extracted frames/audio to "
                         "Vertex AI)")
    ap.add_argument("--list-models", action="store_true",
                    help="print the exact Vertex publisher model IDs "
                         "available to your project/location, then exit "
                         "(ground truth for VERTEX_MODEL_* — no guessing)")
    args = ap.parse_args()

    if args.list_models:
        from swarmtracker_node_ui import providers
        res = providers.list_available_models()
        print("Vertex model discovery @ location=%s" %
              providers.VERTEX_LOCATION)
        if not res.get("ok"):
            print("  ERROR:", res.get("error"))
            sys.exit(1)
        gem = [m for m in res["models"]
               if str(m["id"]).startswith("gemini")]
        for m in sorted(gem, key=lambda x: x["id"]):
            print(f"  {m['id']}  [{m.get('launch_stage') or '—'}]")
        print(f"\n{len(gem)} gemini model(s). Set VERTEX_MODEL_PRIMARY / "
              "VERTEX_MODEL_FALLBACK to ids from this list.")
        sys.exit(0)

    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as app_mod
    from swarmtracker_node_ui import keel_policy
    c = TestClient(app_mod.app)
    S = app_mod.STORE

    # ---- platform ----
    h = c.get("/healthz").json()
    check("Node boots (healthz)", h.get("status") == "ok",
          f"v{h.get('version')} · core={h.get('verity_core', '?')[:40]}")
    check("Local runtime online", h.get("runtime_online"),
          "OpenClaw reachable" if h.get("runtime_online") else
          "OFFLINE — pipeline will use deterministic fallback; start "
          "OpenClaw+Ollama for the graded-model demo")
    check("Media pipeline present", h.get("media_pipeline"))
    st = keel_policy.self_test()
    check("Governance self-tests", st.get("all_passed"),
          ", ".join(k for k, v in st.items() if v is False) or
          "all checks green")
    check("Keel media retrieval policy",
          keel_policy.media_retrieval_allowed()[0])
    check("Keel multimodal escalation policy (scoped, operator-gated, "
          "Local Only default)",
          st.get("media_escalation_scoped_or_disabled") and
          st.get("media_escalation_operator_gated_local_default"))
    from swarmtracker_node_ui import providers
    pcfg = providers.provider_config()
    check("Cloud provider readiness (Vertex AI / ADC)", True,
          (f"{pcfg['name']} ready — project {pcfg['project']} · "
           f"{pcfg['region']} · default {pcfg['default_model_label']} · "
           "ADC, no API key") if pcfg["ready"] else
          (f"{pcfg['name']} not ready: {pcfg['reason']} — escalation "
           "disabled; everything stays Local Only (valid demo config)"))

    # ---- media families, full chain ----
    def full_chain(label, path, fname, mime):
        inv = "Verify " + label
        with open(path, "rb") as f:
            r = c.post("/media/upload",
                       files={"media_file": (fname, f, mime)},
                       data={"new_investigation": inv})
        if r.status_code != 200:
            return check(label + " upload", False,
                         f"HTTP {r.status_code}")
        pid = next((p["id"] for p in S.list_projects()
                    if p["name"] == inv), None)
        cases = S.list_canonical_cases(pid) if pid else []
        if not cases:
            return check(label + " upload", False,
                         "no canonical case persisted")
        case = S.get_canonical_case(cases[0]["id"])
        g = S.project_graph(pid)
        types = {e["type"] for e in g["entities"]}
        tl = [t["label"] for t in S.timeline(pid)]
        a = case["assessment"] or {}
        vf_status = (a.get("visual_findings") or {}).get("status")
        af_status = (a.get("audio_findings") or {}).get("status")
        bits = {
            "graded report": bool(a.get("assessment_conclusion")),
            "evidence pkg": len(case["package"]["artifacts"]) >= 1,
            "case id": case["id"].startswith("DFD-"),
            "atlas node": "canonical_case" in types and "media" in types,
            "timeline": any("Canonical Case" in t for t in tl),
            "cleanup": case["package"]["cleanup"]["status"] == "deleted",
            # truthful modality language: extracted media must never be
            # described as absent
            "truthful states": vf_status not in ("no_visual_evidence",)
            and af_status not in ("no_audio_evidence",),
            "local-only default": not (case["case"].get("runtime") or
                                       {}).get("escalated"),
        }
        note = ("fallback assessment (model offline)"
                if a.get("fallback") else "model-graded") + \
            ("" if all(bits.values()) else
             " | FAILED: " + ", ".join(k for k, v in bits.items()
                                       if not v))
        return check(label + " upload — full chain", all(bits.values()),
                     note)

    v = synth("video", _TMP)
    full_chain("Video (mp4)", v, "verify.mp4", "video/mp4") if v else \
        check("Video (mp4) upload — full chain", False, "ffmpeg synth failed")
    a = synth("audio", _TMP)
    full_chain("Audio (mp3)", a, "verify.mp3", "audio/mpeg") if a else \
        check("Audio (mp3) upload — full chain", False, "ffmpeg synth failed")
    i = synth("image", _TMP)
    full_chain("Image (jpg)", i, "verify.jpg", "image/jpeg") if i else \
        check("Image (jpg) upload — full chain", False, "ffmpeg synth failed")

    if args.upload:
        ext = os.path.splitext(args.upload)[1].lstrip(".").lower()
        mime = {"mp4": "video/mp4", "mov": "video/quicktime",
                "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "mp3": "audio/mpeg",
                "wav": "audio/wav", "m4a": "audio/mp4"}.get(
                    ext, "application/octet-stream")
        full_chain(f"REAL file ({os.path.basename(args.upload)})",
                   args.upload, os.path.basename(args.upload), mime)

    # ---- validation + rejection ----
    fake = os.path.join(_TMP, "fake.mp4")
    with open(fake, "wb") as f:
        f.write(b"not a real video, just bytes")
    with open(fake, "rb") as f:
        r = c.post("/media/upload",
                   files={"media_file": ("fake.mp4", f, "video/mp4")},
                   data={})
    check("File validation (magic-byte rejection)",
          "does not match" in r.text)

    # ---- guaranteed demo case ----
    r = c.post("/media/demo", data={"new_investigation": "Verify Demo"})
    check("Guaranteed demo case", r.status_code == 200 and
          "DFD-" in r.text)

    # ---- YouTube ----
    r = c.post("/media/retrieve",
               data={"media_url": "https://vimeo.com/1"})
    check("Retrieval denial (non-YouTube URL)",
          "Only single YouTube video URLs" in r.text)
    if args.youtube:
        print("...  live governed retrieval (may take a minute)")
        r = c.post("/media/retrieve",
                   data={"media_url": args.youtube,
                         "new_investigation": "Verify YouTube"})
        ok = r.status_code == 200 and "DFD-" in r.text
        note = "retrieved + analyzed + case persisted" if ok else \
            "categorized failure — see report page output; retry or " \
            "record with upload + demo case"
        check("YouTube URL ingestion (LIVE)", ok, note)
    else:
        check("YouTube URL ingestion (LIVE)", False,
              "SKIPPED — rerun with --youtube <url> for the live check")

    # ---- optional LIVE governed escalation ----
    if args.escalate:
        if not providers.provider_config()["ready"]:
            check("Governed Vertex AI escalation (LIVE)", False,
                  providers.provider_config()["reason"])
        else:
            v2 = synth("video", _TMP)
            with open(v2, "rb") as f:
                r = c.post("/media/upload",
                           files={"media_file": ("esc.mp4", f,
                                                 "video/mp4")},
                           data={"new_investigation": "Verify Escalation",
                                 "escalate_multimodal": "1"})
            ok = (r.status_code == 200 and "Governed Hybrid" in r.text
                  and "Yes — extracted artifacts only" in r.text)
            note = ("mode transitioned to Governed Hybrid; Vertex "
                    "model + latency recorded" if ok else
                    "escalation did not complete — report shows the "
                    "honest degradation; check ADC/project/quota")
            check("Governed Vertex AI escalation (LIVE)", ok, note)
    else:
        check("Governed Vertex AI escalation (LIVE)", False,
              "SKIPPED — rerun with --escalate for the live check "
              "(optional for the demo)")

    from swarmtracker_node_ui import keel_events
    check("Media governance ledger recording",
          keel_events.count_events() > 0,
          f"{keel_events.count_events()} events (throwaway ledger)")

    n_ok = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n== {n_ok}/{len(RESULTS)} checks passed ==")
    print("Throwaway verification data was written to", _TMP,
          "(safe to delete).")
    sys.exit(0 if n_ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()

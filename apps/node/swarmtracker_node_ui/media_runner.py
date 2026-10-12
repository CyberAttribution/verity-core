"""
media_runner — SwarmTracker Node's local Media Evidence pipeline (v0.3.0).

Mirrors the reference backend's media_analysis.py sequence with a
synchronous, single-user job model (no job store, no worker concurrency):

    intake -> sha256 -> probe/exif -> frames/audio -> evidence package
           -> Verity Core analysis (local governed router) -> graded
              assessment -> validate (fail-safe) -> cleanup

Truthfulness guarantees preserved from the reference implementation:
- bounded language (graded conclusions only; never "real/fake"),
- anti-fabrication (no visual/audio findings unless that media reached
  the model — on the text-only local runtime it never does, and the
  report says so honestly),
- deterministic fallback_assessment when the model is unavailable,
- package.validate() fail-safe: on internal inconsistency the graded
  report is withheld rather than emitted,
- temporary media deleted on success AND failure; only hashes/derived
  evidence/report are retained.

NOTE: size caps and the 12-format list follow the handoff (S1). When the
reference media_analysis.py (backend release/v0.3.0) is available,
reconcile _FAMILIES caps and magic-byte rules against it exactly.
"""
import os
import shutil
import tempfile
import time

from . import core_bridge, keel_events, keel_policy, providers

# ---------------- formats (S1: 12 formats, magic-byte verified) --------

_FAMILIES = {
    "video": {"exts": {"mp4", "mov", "avi", "mkv", "webm"},
              "cap_bytes": 512 * 1024 * 1024},
    "audio": {"exts": {"mp3", "wav", "m4a"},
              "cap_bytes": 128 * 1024 * 1024},
    "image": {"exts": {"jpg", "jpeg", "png", "webp"},
              "cap_bytes": 32 * 1024 * 1024},
}
ACCEPTED_EXTENSIONS = sorted(
    e for f in _FAMILIES.values() for e in f["exts"])
ACCEPT_ATTR = ",".join("." + e for e in ACCEPTED_EXTENSIONS)

_IMAGE_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
               "png": "image/png", "webp": "image/webp"}


def family_of(ext: str):
    ext = ext.lower().lstrip(".")
    for fam, spec in _FAMILIES.items():
        if ext in spec["exts"]:
            return fam
    return None


def _magic_ok(head: bytes, ext: str) -> bool:
    """Content verification: the leading bytes must be consistent with
    the declared extension's family. Never trusts the filename alone."""
    if len(head) < 12:
        return False
    if ext in ("mp4", "mov", "m4a"):
        return head[4:8] == b"ftyp"
    if ext == "avi":
        return head[:4] == b"RIFF" and head[8:12] == b"AVI "
    if ext in ("mkv", "webm"):
        return head[:4] == b"\x1a\x45\xdf\xa3"
    if ext == "mp3":
        return head[:3] == b"ID3" or (head[0] == 0xFF and
                                      (head[1] & 0xE0) == 0xE0)
    if ext == "wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if ext in ("jpg", "jpeg"):
        return head[:3] == b"\xff\xd8\xff"
    if ext == "png":
        return head[:8] == b"\x89PNG\r\n\x1a\n"
    if ext == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return False


# ---------------- staged progress (the camera's view) ------------------

STAGES = ("received", "extracting", "analyzing", "complete")


class JobStages:
    """Ordered stage log for the UI's staged-progress view."""

    def __init__(self):
        self.steps = []

    def add(self, stage, status, detail=""):
        self.steps.append({"stage": stage, "status": status,
                           "detail": str(detail)[:300],
                           "at": time.strftime("%H:%M:%S")})
        # every pipeline stage is also a Keel governance-ledger event
        keel_events.append_event("media_" + stage,
                                 {"status": status,
                                  "detail": str(detail)[:300]})

    def as_list(self):
        return list(self.steps)


# ---------------- the job ----------------------------------------------

def _apply_modality_truth(assessment, *, fam, extraction, media_examined,
                          escalated_ok):
    """Make the visual/audio sections state exactly what happened, in
    five distinguishable conditions: medium absent · extraction failed ·
    extracted but not semantically examined by the active runtime ·
    examined locally · examined via authorized Gemini escalation.
    Never claims a medium 'lacks content' when it was simply not
    examined. Post-processes the (verbatim, unmodified) core normalize
    output — the vendored core is not touched."""
    vf = assessment.get("visual_findings")
    af = assessment.get("audio_findings")
    if not isinstance(vf, dict):
        vf = {}
        assessment["visual_findings"] = vf
    if not isinstance(af, dict):
        af = {}
        assessment["audio_findings"] = af
    n_frames = len((extraction or {}).get("frames") or [])
    audio_track = (extraction or {}).get("audio")

    if media_examined:
        if escalated_ok:
            for d in (vf, af):
                if d.get("status") == "reviewed":
                    d["limitations"] = (str(d.get("limitations") or "")
                                        + " — review performed via "
                                        "authorized Gemini escalation, "
                                        "not by the local runtime"
                                        ).lstrip(" —")
        return

    # not examined by any model — say precisely why, per modality
    if fam == "image":
        vf.update(status="extracted_not_examined",
                  limitations="the image was hashed and preserved as "
                              "evidence, but the active runtime (local "
                              "text-only model) did not semantically "
                              "examine it; no authorized escalation")
    elif fam == "video":
        if n_frames > 0:
            vf.update(status="extracted_not_examined",
                      limitations=f"{n_frames} representative frame(s) "
                                  "were extracted, hashed, and preserved "
                                  "as evidence, but the active runtime "
                                  "(local text-only model) did not "
                                  "semantically examine them; no "
                                  "authorized escalation")
        else:
            vf.update(status="extraction_failed",
                      limitations="a visual stream was expected but "
                                  "frame extraction produced no "
                                  "artifacts (see the stage log); no "
                                  "visual claims are made")
    else:
        vf.update(status="no_visual_stream",
                  limitations="this specimen type carries no visual "
                              "stream; none was expected or extracted")

    if fam == "audio":
        af.update(status="extracted_not_examined",
                  limitations="the audio track was hashed and preserved "
                              "as evidence, but the active runtime "
                              "(local text-only model) did not "
                              "semantically examine it; no authorized "
                              "escalation")
    elif fam == "video":
        if audio_track:
            af.update(status="extracted_not_examined",
                      limitations="a mono audio track was extracted and "
                                  "preserved as evidence, but the "
                                  "active runtime (local text-only "
                                  "model) did not semantically examine "
                                  "it; no authorized escalation")
        else:
            af.update(status="not_extracted",
                      limitations="no audio track was extracted — the "
                                  "stream may be absent or extraction "
                                  "failed (see the stage log); no audio "
                                  "claims are made")
    else:
        af.update(status="no_audio_stream",
                  limitations="this specimen type carries no audio "
                              "stream; none was expected or extracted")


def _escalation_router(generate_fn, selected_model=None,
                       operator_authorized=False):
    """Build the governed escalation router via the shared
    governed-analysis service (single source of truth). Fail-closed:
    explicit per-job operator authorization (deny by default), Keel
    policy, then provider readiness (ADC + project)."""
    from . import governed_analysis
    return governed_analysis.build_router(
        generate_fn, selected_model,
        operator_authorized=operator_authorized)


def run_media_job(src_path, orig_filename, *, generate_fn,
                  declared=None, case_context="", temp_root=None,
                  source_kind="upload", source_identifier=None,
                  retrieval=None, escalate=False, selected_model=None):
    """Run the complete local media pipeline on one uploaded file.

    `src_path` is a temp copy owned by this job (deleted on exit, success
    or failure). `declared` carries the submitter's untrusted claims
    (claimed_date/place/context/source_account). Returns a result dict:
    {ok, error?, stages, package, assessment?, report?, router_meta,
     validation_issues, extraction}. Never raises.
    """
    if not core_bridge.HAS_MEDIA_INGEST:
        return {"ok": False, "stages": [],
                "error": "media_ingest is not available in this build "
                         "(vendored core predates 0.5.0)."}
    mp = core_bridge.media_package
    stages = JobStages()
    workdir = tempfile.mkdtemp(prefix="stnode-media-",
                               dir=temp_root or None)
    ext = (os.path.splitext(orig_filename or "")[1] or "").lower().lstrip(".")
    fam = family_of(ext)
    pkg = mp.new_package(source_kind,
                         source_identifier or orig_filename
                         or "uploaded-file",
                         declared=dict(declared or {}))
    if retrieval:
        # Four-way metadata origin: platform metadata is
        # retrieval-generated — never implied to be the uploader's
        # original embedded metadata.
        pkg["metadata_by_origin"]["retrieval_generated"].update(
            retrieval.get("metadata") or {})
        mp.record_event(pkg, "remote_retrieval", "ok",
                        tool=retrieval.get("tool"),
                        note=retrieval.get("note") or
                        "governed egress: operator-initiated retrieval")
    result = {"ok": False, "stages": stages, "package": pkg,
              "assessment": None, "report": None, "router_meta": {},
              "validation_issues": [], "analysis_provenance": None,
              "extraction": {"frames": [], "audio": None},
              "family": fam, "error": None}
    deleted = []
    src_art = None
    try:
        # ---- stage: received (format + size + content verification) ----
        try:
            size = os.path.getsize(src_path)
            with open(src_path, "rb") as fh:
                head = fh.read(16)
        except OSError as e:
            stages.add("received", "failed", f"unreadable upload: {e}")
            mp.record_rejection(pkg, f"unreadable upload: {e}", "received")
            result["error"] = "The uploaded file could not be read."
            return result
        sha = mp.tools.sha256_file(src_path) if hasattr(mp, "tools") else \
            core_bridge.media_tools.sha256_file(src_path)
        if fam is None:
            stages.add("received", "failed",
                       f"unsupported format .{ext or '?'}")
            mp.record_rejection(pkg, f"unsupported format .{ext}",
                                "received", sha256=sha)
            result["error"] = ("Unsupported format. Accepted: "
                               + ", ".join(ACCEPTED_EXTENSIONS) + ".")
            return result
        if size > _FAMILIES[fam]["cap_bytes"]:
            stages.add("received", "failed", f"{size} bytes exceeds the "
                       f"{fam} cap")
            mp.record_rejection(pkg, "size cap exceeded", "received",
                                sha256=sha)
            result["error"] = (f"File exceeds the "
                               f"{_FAMILIES[fam]['cap_bytes'] // (1024*1024)}"
                               f" MB {fam} cap.")
            return result
        if not _magic_ok(head, ext):
            stages.add("received", "failed",
                       "content does not match declared format")
            mp.record_rejection(pkg, "magic-byte mismatch with declared "
                                     "extension", "received", sha256=sha)
            result["error"] = ("File content does not match its declared "
                               "format; refused as a specimen-integrity "
                               "precaution.")
            return result
        src_art = mp.add_artifact(pkg, "source_media", sha256=sha,
                                  size_bytes=size, label=orig_filename)
        mp.record_event(pkg, "intake", "ok", outputs=[src_art],
                        note=f"{fam} specimen accepted ({size} bytes)")
        stages.add("received", "ok",
                   f"{orig_filename} · {fam} · sha256 {sha[:12]}…")

        # ---- stage: extracting (probe / exif / frames / audio) ----
        pr = core_bridge.probe.probe_media(src_path)
        mp.attach_probe(pkg, pr, artifact_id=src_art)
        stages.add("extracting", "ok" if pr.get("ok") else "degraded",
                   "ffprobe: " + (
                       (pr.get("container") or {}).get("format_name") or
                       pr.get("error") or "no structural facts"))
        ex = core_bridge.exif.extract_exif(src_path)
        mp.attach_exif(pkg, ex, artifact_id=src_art)
        stages.add("extracting", "ok" if ex.get("ok") else "degraded",
                   "exiftool: " + (f"{ex.get('total_tags', 0)} tags"
                                   if ex.get("ok") else
                                   ex.get("error") or "no embedded tags"))

        parts = []
        if fam == "video":
            dur = ((pr.get("container") or {}).get("duration_s")
                   if pr.get("ok") else None)
            fr = core_bridge.frames.extract_frames(
                src_path, os.path.join(workdir, "frames"), duration_s=dur)
            fids = []
            for f in fr.get("frames", []):
                fpath = os.path.join(workdir, "frames", f["filename"])
                fid = mp.add_artifact(pkg, "frame", path=fpath,
                                      parent=src_art,
                                      step="frame_extraction",
                                      tool=fr.get("tool"),
                                      label=f["filename"])
                fids.append(fid)
                deleted.append((fid, fpath))
                parts.append({"mime": "image/jpeg", "path": fpath})
            mp.record_event(pkg, "frame_extraction",
                            "ok" if fr.get("ok") else "failed",
                            tool=fr.get("tool"), inputs=[src_art],
                            outputs=fids,
                            note=fr.get("error") or
                            f"{len(fids)} frames (scene+interval)")
            stages.add("extracting", "ok" if fr.get("ok") else "degraded",
                       f"frames: {len(fids)}" if fr.get("ok")
                       else "frames: " + (fr.get("error") or "failed"))
            au = core_bridge.audio.extract_audio(
                src_path, os.path.join(workdir, "audio"))
            if au.get("ok"):
                apath = os.path.join(workdir, "audio", au["filename"])
                aid = mp.add_artifact(pkg, "audio_track", path=apath,
                                      parent=src_art,
                                      step="audio_extraction",
                                      tool=au.get("tool"),
                                      label=au["filename"])
                deleted.append((aid, apath))
                parts.append({"mime": au.get("mime") or "audio/mp4",
                              "path": apath})
                mp.record_event(pkg, "audio_extraction", "ok",
                                tool=au.get("tool"), inputs=[src_art],
                                outputs=[aid], note="mono track")
                result["extraction"]["audio"] = au["filename"]
                stages.add("extracting", "ok", "audio: mono track "
                           f"({au['filename']})")
            else:
                mp.record_event(pkg, "audio_extraction", "failed",
                                tool=au.get("tool"), inputs=[src_art],
                                note=au.get("error") or "")
                stages.add("extracting", "degraded",
                           "audio: " + (au.get("error") or "none"))
            result["extraction"]["frames"] = [f["filename"] for f in
                                              fr.get("frames", [])]
        elif fam == "image":
            parts.append({"mime": _IMAGE_MIME.get(ext, "image/jpeg"),
                          "path": src_path})
        elif fam == "audio":
            parts.append({"mime": "audio/mpeg" if ext == "mp3"
                          else "audio/mp4" if ext == "m4a"
                          else "audio/wav", "path": src_path})

        mp.derive_observations(pkg, declared_extension=ext)

        # ---- stage: analyzing (governed local pipeline, with optional
        # ---- Keel-governed multimodal escalation) ----
        digest = mp.evidence_digest(pkg)
        esc_router, esc_reason, sent_manifest = None, "", []
        auth_eid, auth_write_failed, result_write_failed = \
            None, False, False
        if escalate and parts:
            # `escalate` is this job's explicit per-job operator
            # selection (form radio / direct caller); forwarded as the
            # strict keyword-only authorization signal. Never inferred
            # from model choice, credentials, readiness, or prior jobs.
            # Availability honesty: a router-build failure here means
            # the job failed BEFORE any provider contact (no provider
            # SDK generate invocation) — reported as unavailable, never
            # as a denial or a provider error. Note that once the
            # router exists, an attempted generate proves only that the
            # SDK call was made, not that bytes reached the remote
            # provider (credential/transport setup can fail pre-HTTP).
            esc_router, esc_reason = _escalation_router(
                generate_fn, selected_model,
                operator_authorized=(escalate is True))
            if esc_router is None:
                stages.add("analyzing", "degraded",
                           "escalation unavailable: " + esc_reason
                           + " — continuing Local Only")
                keel_events.append_event("media_escalation_denied",
                                         {"reason": esc_reason})
            else:
                for a in pkg["artifacts"]:
                    if a["kind"] in ("frame", "audio_track"):
                        sent_manifest.append(
                            {"kind": a["kind"], "sha256": a["sha256"],
                             "size_bytes": a["size_bytes"]})
                if fam in ("image", "audio"):
                    sent_manifest.append(
                        {"kind": "source_image" if fam == "image"
                         else "source_audio", "sha256": sha,
                         "size_bytes": size})
                esc_provider = getattr(esc_router, "provider",
                                       None) or "vertex"
                # Task 3 — MANDATORY ledger authorization BEFORE the
                # core run_media call can trigger any cloud generate.
                # A write failure here means the escalation is NOT
                # attempted: the job continues LOCAL ONLY with an
                # explicit reason in the stage log (never mislabeled as
                # a provider failure or a policy denial).
                auth_eid = None
                auth_write_failed = False
                try:
                    auth_eid = keel_events.append_event_mandatory(
                        "media_escalation_authorized",
                        {"provider": esc_provider,
                         "artifact_count": len(sent_manifest),
                         "artifacts": sent_manifest,
                         "boundary": "extracted artifacts only; source "
                                     "video, evidence package, case, "
                                     "and ledgers remain local"})
                except (keel_events.LedgerWriteError, ValueError,
                        OSError) as exc:
                    auth_write_failed = True
                    reason = ("authorization ledger write failed — "
                              "escalation not attempted: "
                              + str(exc)[:160])
                    stages.add("analyzing", "degraded", reason
                               + " — continuing Local Only")
                    keel_events.append_event(
                        "media_escalation_auth_write_failed",
                        {"reason": str(exc)[:200]})
                    mp.record_event(pkg, "multimodal_escalation",
                                    "not_authorized",
                                    note=reason)
                    # Correlation directive: an authorization write
                    # failure means NOTHING was authorized or sent.
                    # Carry the explicit audit reason everywhere the
                    # unavailable reason would have gone, and clear
                    # the sent manifest so no report/custody field can
                    # imply artifacts left the node.
                    esc_router = None
                    esc_reason = reason
                    sent_manifest = []
                else:
                    mp.record_event(
                        pkg, "multimodal_escalation", "authorized",
                        note=f"operator-authorized: {len(sent_manifest)} "
                             "extracted artifact(s) to "
                             f"{esc_provider}; evidence package and "
                             "source remain local")
                    stages.add("analyzing", "ok",
                               f"authorized escalation: "
                               f"{len(sent_manifest)} extracted "
                               f"artifact(s) -> {esc_provider} "
                               "(interpretation only; fusion stays "
                               "local)")
        elif escalate and not parts:
            stages.add("analyzing", "degraded",
                       "escalation requested but this specimen produced "
                       "no artifacts to send — Local Only")
        stages.add("analyzing", "ok", "evidence digest -> local model "
                   "(Keel-locked)")
        report, router = core_bridge.run_media(
            digest, generate_fn, label=orig_filename or "Media evidence",
            case_context=case_context, parts=parts, router=esc_router)
        result["report"] = report
        escalated = esc_router is not None
        if escalated:
            mm_ok = bool(getattr(router, "multimodal_ok", False))
            mm_err = getattr(router, "multimodal_error", None)
            # Post-call result audit: a write failure CANNOT undo the
            # generate call that already ran. Keep the truthful provider
            # outcome (mm_ok/mm_err below) and surface the audit
            # failure separately; never rerun inference.
            result_write_failed = False
            try:
                keel_events.append_event_mandatory(
                    "media_escalation_result",
                    {"ok": mm_ok, "error": mm_err,
                     "authorization_event_id": auth_eid,
                     "provider": esc_provider})
            except (keel_events.LedgerWriteError, ValueError, OSError):
                result_write_failed = True
            mp.record_event(pkg, "multimodal_escalation",
                            "ok" if mm_ok else "failed",
                            note="gemini multimodal review completed"
                            if mm_ok else f"failed: {mm_err} — degraded "
                            "to Local Only text analysis")
            stages.add("analyzing", "ok" if mm_ok else "degraded",
                       "gemini multimodal review completed"
                       if mm_ok else
                       f"gemini review failed ({str(mm_err)[:80]}) — "
                       "degraded honestly to Local Only")
        media_examined = bool(parts) and \
            getattr(router, "supports_media", False) and \
            getattr(router, "multimodal_ok", True)
        has_frames = media_examined and fam in ("video", "image")
        has_audio = media_examined and (fam == "audio" or
                                        result["extraction"]["audio"])
        ma_raw, analysis_ok = None, False
        for a in report.artifact_analyses:
            extra = getattr(a, "extra", None) or {}
            if isinstance(extra.get("media_assessment"), dict):
                ma_raw, analysis_ok = extra["media_assessment"], True
                break
        if analysis_ok:
            assessment = core_bridge.media_assessment.normalize(
                ma_raw, has_frames=bool(has_frames),
                has_audio=bool(has_audio))
        else:
            assessment = core_bridge.media_assessment.fallback_assessment(
                pkg, reason="the local model returned no media assessment")
        _apply_modality_truth(
            assessment, fam=fam, extraction=result["extraction"],
            media_examined=bool(media_examined),
            escalated_ok=bool(escalated and getattr(router,
                                                   "multimodal_ok",
                                                   False)))
        # technical_authenticity is always present, with an honest
        # signal-level statement matching what actually happened
        ta = assessment.get("technical_authenticity")
        if not isinstance(ta, dict):
            ta = {}
            assessment["technical_authenticity"] = ta
        ta.setdefault("metadata_codec_findings", [])
        ta.setdefault("original_file_available", "unknown")
        ta.setdefault("derivative_or_repost_indicators", [])
        if not media_examined:
            ta["signal_level_analysis"] = (
                "signal-level review not performed by this runtime "
                "(local text-only model; frames/audio were extracted as "
                "evidence but not machine-reviewed)")
        else:
            ta.setdefault(
                "signal_level_analysis",
                "model review of the extracted frames/audio via "
                "authorized escalation; not validated pixel-level "
                "deepfake detection")
        # --- provider-failure truthfulness (v0.3.4) ---
        # After an AUTHORIZED escalation that FAILED, extracted artifacts
        # exist but were examined by neither the local (text-only) model
        # nor Gemini. We must NOT emit a graded authenticity conclusion,
        # and we must NEVER imply the media lacked visual/audio content.
        mm_ok = bool(escalated and getattr(router, "multimodal_ok",
                                           False))
        artifacts_exist = (bool(result["extraction"]["frames"]) or
                           bool(result["extraction"]["audio"]) or
                           fam in ("image", "audio"))
        provider_failed = bool(escalated) and not mm_ok
        multimodal_unavailable = (provider_failed and not media_examined
                                  and artifacts_exist)
        attempted_models = list(getattr(router, "attempted_models", [])
                                or []) if escalated else []
        provider_error = getattr(router, "multimodal_error", None) \
            if escalated else None
        provider_status = getattr(router, "multimodal_status", None) \
            if escalated else None
        if multimodal_unavailable:
            n_art = (len(result["extraction"]["frames"]) +
                     (1 if result["extraction"]["audio"] else 0)) or \
                len(sent_manifest) or 1
            assessment["assessment_conclusion"] = \
                "multimodal_interpretation_unavailable"
            assessment["conclusion_rationale"] = (
                "Multimodal interpretation was authorized but "
                "unavailable (provider error). "
                f"{n_art} extracted artifact(s) — sampled frames and/or "
                "the audio track — were hashed and preserved as local "
                "evidence but were NOT semantically examined by any "
                "model, so no authenticity conclusion is drawn. The "
                "local metadata/provenance findings below are retained.")
            assessment["multimodal_unavailable"] = True
            assessment["provider_error"] = provider_error
            assessment["attempted_models"] = attempted_models
            assessment["provider_status"] = provider_status

        result["router_meta"] = {
            "supports_media": bool(getattr(router, "supports_media",
                                           False)),
            "media_examined": bool(media_examined),
            "fallback": bool(assessment.get("fallback")),
            "escalated": bool(escalated),
            "escalation_reason": esc_reason or None,
            "escalation_provider": (getattr(router, "provider", "vertex")
                                    if escalated else None),
            "escalation_ok": mm_ok if escalated else None,
            "multimodal_unavailable": bool(multimodal_unavailable),
            "attempted_models": attempted_models,
            "model_used": getattr(router, "model_used", None)
            if escalated else None,
            "latency_ms": getattr(router, "latency_ms", None)
            if escalated else None,
            "vertex_project": getattr(router, "project", None)
            if escalated else None,
            "vertex_region": getattr(router, "region", None)
            if escalated else None,
            "attempt_history": getattr(router, "multimodal_attempts", [])
            if escalated else [],
            "provider_error": provider_error,
            "provider_status": provider_status,
            "escalation_auth_event_id": auth_eid,
            "escalation_auth_write_failed": auth_write_failed,
            "escalation_result_write_failed":
                result_write_failed if escalated else False,
            "sent_artifacts": sent_manifest if escalated else [],
            "custody": {
                "source_file": "processed locally, deleted after "
                               "analysis; never sent to any provider",
                "retained_local": [a.get("sha256") for a in
                                   pkg["artifacts"] if a.get("sha256")],
                "sent_to_provider": [s.get("sha256") for s in
                                     sent_manifest]
                if (escalated and mm_ok) else [],
                "sent_but_provider_failed": [s.get("sha256") for s in
                                             sent_manifest]
                if provider_failed else [],
            },
        }
        # Provenance: which layer produced which findings. Rendered on
        # the report so local and escalated analysis are never blended.
        esc_ok = escalated and getattr(router, "multimodal_ok", False)
        prov_name = getattr(router, "provider", "vertex") if escalated \
            else None
        if escalated and not esc_ok:
            models = ", ".join(attempted_models) or "none"
            esc_state = (f"{prov_name}: authorized but FAILED — provider "
                         "error"
                         + (f" (HTTP {provider_status})"
                            if provider_status else "")
                         + f"; models attempted: {models}; extracted "
                         "artifacts preserved locally, not examined")
        elif esc_ok:
            mu = getattr(router, "model_used", None) or "n/a"
            lat = getattr(router, "latency_ms", None)
            esc_state = (f"{prov_name}: authorized + completed on "
                         f"{mu}" + (f" ({lat} ms)" if lat else ""))
        elif escalate:
            esc_state = "requested but unavailable: " + \
                (esc_reason or "no artifacts to send")
        else:
            esc_state = "not requested (Local Only default)"
        result["analysis_provenance"] = {
            "extraction": "local — ffmpeg/ffprobe/exiftool "
                          "(deterministic)",
            "artifact_analysis": (
                (getattr(router, "provider", "vertex") + " — authorized "
                 "multimodal escalation on "
                 + (getattr(router, "model_used", None) or "?"))
                if esc_ok else
                "local model (OpenClaw, Keel-locked)"),
            "fusion": "local model (OpenClaw, Keel-locked)",
            "persistence_governance": "local — SQLite store, Keel "
                                      "policy, media ledger",
            "escalation": esc_state,
            "sent_artifacts": sent_manifest if escalated else [],
            "attempted_models": attempted_models,
            "provider_error": provider_error,
            "provider_status": provider_status,
            "multimodal_unavailable": bool(multimodal_unavailable),
        }
        stages.add("analyzing", "ok" if analysis_ok else "degraded",
                   "graded assessment" if analysis_ok else
                   "deterministic fallback assessment (model output "
                   "unavailable)")

        # ---- fail-safe validation, then success ----
        result["assessment"] = assessment
        result["ok"] = True
        return result
    except Exception as e:   # a demo must degrade, never crash
        stages.add("analyzing", "failed", f"{e.__class__.__name__}: {e}")
        result["error"] = ("Media pipeline error: "
                           f"{e.__class__.__name__}. Evidence handling "
                           "was aborted; temporary media is deleted.")
        return result
    finally:
        # ---- stage: cleanup (mandatory, success AND failure) ----
        cleanup_status = "deleted"
        try:
            shutil.rmtree(workdir, ignore_errors=True)
            if os.path.isfile(src_path):
                os.unlink(src_path)
        except OSError:
            cleanup_status = "partial"
        try:
            mp.record_event(pkg, "cleanup", "ok",
                            note="source + derived media removed; hashes "
                                 "and evidence retained")
            mp.finalize(pkg, cleanup_status,
                        deleted_artifact_ids=[d[0] for d in deleted] +
                        ([src_art] if src_art else []))
            issues = mp.validate(pkg)
            result["validation_issues"] = issues
            if issues and result.get("ok"):
                # fail safe: withhold the graded report
                result["ok"] = False
                result["assessment"] = None
                result["error"] = ("Evidence-package validation failed; "
                                   "the graded report was withheld "
                                   "(fail-safe). Issues: "
                                   + "; ".join(issues[:5]))
        except Exception:
            pass
        stages.add("complete",
                   "ok" if result.get("ok") else "failed",
                   f"temporary media {cleanup_status}")
        result["stages"] = stages.as_list()


# ---------------- YouTube URL ingestion (governed egress) ---------------

RETRIEVAL_LIMITS = {"max_filesize_mb": 200, "max_resolution": 720,
                    "max_duration_s": 15 * 60}


def run_youtube_job(url, *, generate_fn, declared=None, case_context="",
                    temp_root=None, escalate=False, selected_model=None):
    """Retrieve one operator-submitted YouTube URL under the Keel
    governed-egress policy, then run the standard local pipeline on the
    retrieved file. Fail-soft with categorized, truthful failures.

    Residential-egress note: the anonymous default->tv->mweb client plan
    is expected to succeed on the M1; the PO-token/cookie escalations
    remain available but are not required.
    """
    stages = JobStages()

    def fail(error, category=None, extra=None):
        keel_events.append_event("media_retrieval_denied_or_failed",
                                 {"category": category, "error": error})
        out = {"ok": False, "error": error, "stages": stages.as_list(),
               "retrieval_category": category, "package": None,
               "assessment": None, "report": None, "router_meta": {},
               "validation_issues": [], "analysis_provenance": None,
               "extraction": {"frames": [], "audio": None},
               "family": None}
        out.update(extra or {})
        return out

    # Keel gate (defense in depth; the route checks too)
    allowed, reason = keel_policy.media_retrieval_allowed()
    if not allowed:
        stages.add("received", "failed", "Keel policy: " + reason)
        return fail("Keel policy lock: " + reason, "policy_denied")
    if not keel_policy.media_tool_allowed("yt-dlp"):
        stages.add("received", "failed",
                   "yt-dlp is not in the Keel media tool allow-list")
        return fail("Keel policy lock: yt-dlp not allowed",
                    "policy_denied")
    if not core_bridge.HAS_MEDIA_INGEST:
        return fail("media_ingest unavailable (core predates 0.5.0)")

    adapter = core_bridge.media_remote.get_adapter(url)
    if adapter is None:
        stages.add("received", "failed", "no registered adapter for URL")
        return fail("Only single YouTube video URLs are supported for "
                    "retrieval. Upload the file directly instead.",
                    "unsupported_url")
    c = adapter.classify(url)
    if not c.get("supported"):
        stages.add("received", "failed", c.get("reject_reason") or
                   "URL rejected")
        return fail(c.get("reject_reason") or "URL rejected by the "
                    "adapter's validation.", "unsupported_url")
    canonical = c["canonical_url"]
    keel_events.append_event("media_retrieval_authorized",
                             {"adapter": adapter.provider,
                              "canonical_url": canonical,
                              "limits": RETRIEVAL_LIMITS})
    stages.add("received", "ok",
               f"URL validated · {adapter.provider} · {c['media_id']} "
               "(canonical form; the submitted string never reaches the "
               "tool)")

    workdir = tempfile.mkdtemp(prefix="stnode-yt-", dir=temp_root or None)
    try:
        pr = adapter.probe(canonical, timeout_s=60)
        md = pr.get("metadata") or {}
        if pr.get("ok"):
            stages.add("retrieving", "ok",
                       "metadata probe: "
                       + (md.get("title") or "untitled")[:60]
                       + f" · {md.get('duration_s') or '?'}s")
            if md.get("is_live"):
                return fail("Live streams are not retrieved.",
                            "unsupported_media")
        else:
            stages.add("retrieving", "degraded",
                       "metadata probe failed ("
                       + str(pr.get("category")) + "); attempting "
                       "bounded retrieval anyway")
        rr = adapter.retrieve(canonical, workdir,
                              limits=dict(RETRIEVAL_LIMITS),
                              timeout_s=300)
        if not rr.get("ok"):
            cat = rr.get("category") or "retrieval_error"
            msg = core_bridge.media_remote.user_message(cat)
            eff = core_bridge.media_remote.confidence_effect(cat)
            stages.add("retrieving", "failed", f"{cat}")
            return fail(msg, cat, {"confidence_effect": eff,
                                   "probe_metadata": md or None})
        fpath = os.path.join(workdir, rr["filename"])
        stages.add("retrieving", "ok",
                   f"retrieved {rr['filename']} "
                   f"({os.path.getsize(fpath)} bytes) via governed "
                   "egress")
        result = run_media_job(
            fpath, rr["filename"], generate_fn=generate_fn,
            declared=dict(declared or {}), case_context=case_context,
            temp_root=temp_root, source_kind="youtube",
            source_identifier=canonical, escalate=escalate,
            selected_model=selected_model,
            retrieval={"metadata": {"provider": adapter.provider, **md},
                       "tool": rr.get("tool"),
                       "note": "operator-initiated governed retrieval "
                               f"({adapter.provider})"})
        # merge the retrieval stage log ahead of the pipeline's
        result["stages"] = stages.as_list() + result.get("stages", [])
        result["retrieval_metadata"] = md or None
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------- guaranteed demo case (no external dependency) ---------

def synthesize_demo_media(dest_dir):
    """ffmpeg-synthesized demonstration clip (pattern + tone + label).
    Returns (path, error). Honest by construction: the clip is labeled
    synthetic on its face and in its declared context."""
    if not core_bridge.media_tools.available("ffmpeg"):
        return None, "ffmpeg is not available on this node"
    os.makedirs(dest_dir, exist_ok=True)
    out = os.path.join(dest_dir, "swarmtracker-demo-specimen.mp4")
    r = core_bridge.media_tools.run_tool(
        ["ffmpeg", "-v", "error",
         "-f", "lavfi", "-i",
         "testsrc2=duration=6:size=640x360:rate=24",
         "-f", "lavfi", "-i", "sine=frequency=520:duration=6",
         "-vf",
         "drawtext=text='SWARMTRACKER SYNTHETIC DEMO SPECIMEN'"
         ":fontcolor=white:fontsize=22:box=1:boxcolor=black@0.6"
         ":x=(w-text_w)/2:y=h-40",
         "-shortest", "-pix_fmt", "yuv420p", "-y", out],
        timeout_s=60)
    if not r.get("ok") or not os.path.isfile(out):
        # fallback without drawtext (font support varies)
        r2 = core_bridge.media_tools.run_tool(
            ["ffmpeg", "-v", "error",
             "-f", "lavfi", "-i",
             "testsrc2=duration=6:size=640x360:rate=24",
             "-f", "lavfi", "-i", "sine=frequency=520:duration=6",
             "-shortest", "-pix_fmt", "yuv420p", "-y", out],
            timeout_s=60)
        if not r2.get("ok") or not os.path.isfile(out):
            return None, "ffmpeg could not synthesize the demo clip"
    return out, None


def run_demo_job(*, generate_fn, temp_root=None, escalate=False,
                 selected_model=None):
    """The guaranteed demo path: synthesize media locally, then run the
    complete REAL pipeline on it — nothing is mocked downstream."""
    workdir = tempfile.mkdtemp(prefix="stnode-demo-", dir=temp_root or None)
    try:
        path, err = synthesize_demo_media(workdir)
        if err:
            return {"ok": False, "error": err, "stages": [],
                    "package": None, "assessment": None, "report": None,
                    "router_meta": {}, "validation_issues": [],
                    "extraction": {"frames": [], "audio": None},
                    "family": None}
        keel_events.append_event("media_demo_case_started",
                                 {"specimen": os.path.basename(path)})
        return run_media_job(
            path, os.path.basename(path), generate_fn=generate_fn,
            declared={"claimed_context":
                      "SwarmTracker synthetic demonstration specimen — "
                      "generated locally by ffmpeg for pipeline "
                      "demonstration; not real-world media"},
            case_context="This specimen is a locally synthesized "
                         "demonstration clip. Assess it exactly as any "
                         "specimen; its synthetic origin is declared.",
            temp_root=temp_root, escalate=escalate,
            selected_model=selected_model)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

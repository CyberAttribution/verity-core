"""
verity_core.artifacts.media_assessment — graded-conclusion + evidence-
domain logic for the Media Evidence report (v0.5.0 S4).

Two jobs, both deterministic and model-independent:

normalize(ma, has_frames, has_audio)
    Defensive normalization of a model-produced media_assessment:
    * assessment_conclusion clamped to the six graded values (never a
      real/fake binary; unrecognized -> "inconclusive");
    * confidence_by_domain forced to the five evidence domains
      (technical, provenance, contextual, narrative, corroboration) so
      future Origin/Beacon/Physics integrations consume a stable shape;
    * ANTI-FABRICATION GUARD: visual findings are stripped when no
      frames were attached, audio findings when no audio was attached —
      a model cannot report on evidence it never received;
    * bounded-language limitations are enforced on visual/audio sections
      (vision-model review, not validated pixel-level detection; model
      listening, not biometric identification).

fallback_assessment(evidence_package, reason)
    Honest deterministic assessment when the analysis runtime is
    unavailable: the evidence stands, the model reading does not.
"""
from .prompts import MEDIA_CONCLUSIONS, MEDIA_DOMAINS

_VISUAL_LIMIT = ("vision-model review of sampled frames; not validated "
                 "pixel-level deepfake detection")
_AUDIO_LIMIT = ("model listening; no biometric speaker identification or "
                "validated synthetic-voice detection")


def _as_str_list(v, cap=12):
    if isinstance(v, list):
        return [str(x)[:400] for x in v if str(x).strip()][:cap]
    if isinstance(v, str) and v.strip():
        return [v.strip()[:400]]
    return []


def _clamp_conf(v):
    try:
        return max(0, min(100, int(float(v))))
    except (TypeError, ValueError):
        return 0


def _epistemic_items(v, cap=8):
    """Frame/speaker observation entries keep the observation /
    interpretation / competing_explanations epistemic shape."""
    out = []
    for item in (v if isinstance(v, list) else [])[:cap]:
        if isinstance(item, str):
            out.append({"observation": item[:400], "interpretation": "",
                        "competing_explanations": []})
        elif isinstance(item, dict):
            out.append({
                "observation": str(item.get("observation") or "")[:400],
                "interpretation": str(item.get("interpretation")
                                      or "")[:400],
                "competing_explanations":
                    _as_str_list(item.get("competing_explanations"), 4),
            })
    return [o for o in out if o["observation"]]


def normalize_conclusion(value):
    v = str(value or "").strip().lower()
    return v if v in MEDIA_CONCLUSIONS else "inconclusive"


def normalize_domains(v):
    v = v if isinstance(v, dict) else {}
    out = {}
    for d in MEDIA_DOMAINS:
        entry = v.get(d) if isinstance(v.get(d), dict) else {}
        out[d] = {"confidence": _clamp_conf(entry.get("confidence")),
                  "basis": str(entry.get("basis") or "not assessed")[:300]}
    return out


def normalize(ma, has_frames=False, has_audio=False):
    """Never raises; always returns a complete, honest assessment dict."""
    ma = dict(ma) if isinstance(ma, dict) else {}
    ma["assessment_conclusion"] = normalize_conclusion(
        ma.get("assessment_conclusion"))
    ma["conclusion_rationale"] = str(ma.get("conclusion_rationale")
                                     or "No rationale returned.")[:800]
    ma["confidence_by_domain"] = normalize_domains(
        ma.get("confidence_by_domain"))

    # ---- visual findings (anti-fabrication) ----
    vf = ma.get("visual_findings") if isinstance(
        ma.get("visual_findings"), dict) else {}
    if has_frames:
        ma["visual_findings"] = {
            "status": "reviewed",
            "frame_observations":
                _epistemic_items(vf.get("frame_observations")),
            "visible_text": _as_str_list(vf.get("visible_text")),
            "visual_discontinuities":
                _as_str_list(vf.get("visual_discontinuities")),
            "limitations": _VISUAL_LIMIT,
        }
    else:
        ma["visual_findings"] = {
            "status": "no_visual_evidence", "frame_observations": [],
            "visible_text": [], "visual_discontinuities": [],
            "limitations": "no frames were attached; visual review was "
                           "not performed",
        }

    # ---- audio findings (anti-fabrication) ----
    af = ma.get("audio_findings") if isinstance(
        ma.get("audio_findings"), dict) else {}
    if has_audio:
        ma["audio_findings"] = {
            "status": "reviewed",
            "transcript": str(af.get("transcript") or "")[:8000],
            "speaker_observations":
                _epistemic_items(af.get("speaker_observations")),
            "discontinuities": _as_str_list(af.get("discontinuities")),
            "limitations": _AUDIO_LIMIT,
        }
    else:
        ma["audio_findings"] = {
            "status": "no_audio_evidence", "transcript": "",
            "speaker_observations": [], "discontinuities": [],
            "limitations": "no audio was attached; audio review was not "
                           "performed",
        }

    # ---- recommended corroboration (guidance, not conclusions) ----
    rc = []
    for item in (ma.get("recommended_corroboration")
                 if isinstance(ma.get("recommended_corroboration"), list)
                 else [])[:8]:
        if isinstance(item, str):
            rc.append({"item": item[:300], "rationale": "", "bearing": ""})
        elif isinstance(item, dict) and item.get("item"):
            rc.append({"item": str(item.get("item"))[:300],
                       "rationale": str(item.get("rationale") or "")[:300],
                       "bearing": str(item.get("bearing") or "")[:300]})
    ma["recommended_corroboration"] = rc
    return ma


def fallback_assessment(evidence_package=None, reason=""):
    """Deterministic assessment when no model analysis ran. The graded
    conclusion reflects the missing analysis honestly."""
    pkg = evidence_package or {}
    has_any_evidence = bool(pkg.get("artifacts")) or bool(
        (pkg.get("metadata_by_origin") or {}).get("source_embedded"))
    conclusion = "inconclusive" if has_any_evidence else \
        "insufficient_media_evidence"
    basis = ("deterministic evidence extraction only; model analysis "
             + (f"unavailable: {reason}" if reason else "unavailable"))
    ma = normalize({
        "assessment_conclusion": conclusion,
        "conclusion_rationale":
            "Model analysis did not run"
            + (f" ({str(reason)[:200]})" if reason else "")
            + ". Deterministic evidence (hashes, structure, embedded "
            "metadata, provenance chain) was recorded; no model "
            "interpretation of the media was produced.",
        "confidence_by_domain": {d: {"confidence": 0, "basis": basis}
                                 for d in MEDIA_DOMAINS},
        "technical_authenticity": {
            "metadata_codec_findings": [],
            "original_file_available": "unknown",
            "derivative_or_repost_indicators": [],
            "signal_level_analysis": "not performed - " + basis},
        "recommended_corroboration":
            list(pkg.get("recommended_corroboration") or []),
    }, has_frames=False, has_audio=False)
    ma["fallback"] = True
    return ma

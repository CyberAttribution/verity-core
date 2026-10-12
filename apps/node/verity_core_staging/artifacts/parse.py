"""
verity_core.artifacts.parse — defensive parsing of model output.

Never raises. Missing fields degrade to "unknown"/empty, and every parse
records whether structured output was recovered (`parsed`).
"""
import json
import re

from .schemas import (RISK_LEVELS, ArtifactAnalysis, AttributionHypothesis,
                      IndicatorSet)


def extract_json(text: str):
    """First balanced {...} block in model output, parsed; else None."""
    text = text or ""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def as_list(v):
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [s.strip() for s in re.split(r"\n|;", v) if s.strip()]
    return []


def clamp_conf(v):
    try:
        return max(0, min(100, int(float(v))))
    except (TypeError, ValueError):
        return 0


def clamp_risk(v):
    v = str(v or "unknown").strip().lower()
    return v if v in RISK_LEVELS else "unknown"


def parse_artifact_analysis(raw_text: str, artifact_id: str = "",
                            indicators: IndicatorSet = None,
                            event_id: str = "") -> ArtifactAnalysis:
    data = extract_json(raw_text) or {}
    known = {"risk_level", "confidence", "verdict", "signals",
             "organization_context_questions", "recommended_action"}
    return ArtifactAnalysis(
        artifact_id=artifact_id,
        risk_level=clamp_risk(data.get("risk_level")),
        confidence=clamp_conf(data.get("confidence")),
        verdict=str(data.get("verdict") or
                    ("No structured verdict returned." if data
                     else "Model returned unstructured output.")),
        signals=as_list(data.get("signals")),
        recommended_action=str(data.get("recommended_action") or "—"),
        org_questions=as_list(data.get("organization_context_questions")),
        indicators=indicators or IndicatorSet(),
        extra={k: v for k, v in data.items() if k not in known},
        event_id=event_id or "",
        raw_response=raw_text or "",
        parsed=bool(data),
    )


def parse_fusion(raw_text: str) -> dict:
    """Parse the fusion-pass output into report-ready fields."""
    data = extract_json(raw_text) or {}
    hyps = []
    for h in (data.get("attribution_hypotheses") or []):
        if not isinstance(h, dict):
            continue
        hyps.append(AttributionHypothesis(
            hypothesis=str(h.get("hypothesis") or "").strip() or
            "not assessed",
            campaign_type=str(h.get("campaign_type") or "unknown").strip(),
            confidence=clamp_conf(h.get("confidence")),
            supporting_evidence=as_list(h.get("supporting_evidence")),
            missing_evidence=as_list(h.get("missing_evidence")),
            alternatives=as_list(h.get("alternatives")),
        ))
    return {
        "parsed": bool(data),
        "fused_verdict": str(data.get("fused_verdict") or
                             ("No structured fusion returned." if not data
                              else "No fused verdict returned.")),
        "overall_risk": clamp_risk(data.get("overall_risk")),
        "overall_confidence": clamp_conf(data.get("overall_confidence")),
        "corroborations": as_list(data.get("corroborations")),
        "contradictions": as_list(data.get("contradictions")),
        "missing_evidence": as_list(data.get("missing_evidence")),
        "recommendations": as_list(data.get("recommendations")),
        "attribution": hyps,
        "raw_response": raw_text or "",
    }

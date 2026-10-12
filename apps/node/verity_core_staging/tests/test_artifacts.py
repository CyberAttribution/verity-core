"""
Unit tests for verity_core.artifacts — pure Python, deterministic mock
router, no network. Portable to the verity-core repo CI as-is.

Run from the staging parent directory:
    python3 -m pytest verity_core_staging/tests -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from verity_core_staging.artifacts import fuse, normalize, parse, pipeline  # noqa: E402
from verity_core_staging.artifacts.schemas import FusedReport, IndicatorSet  # noqa: E402
from verity_core_staging.routers.base import CallableRouter  # noqa: E402
from verity_core_staging.routers.openclaw import OpenClawRouter  # noqa: E402


# ---------- normalize ----------

def test_extract_indicators():
    text = ("From accounts@apexsupplies-billing.example click "
            "http://portal.example-verify.top/login now, or call "
            "198.51.100.77. Also see billing.apex.example.")
    ind = normalize.extract_indicators(text, "boss@firm.example")
    assert "apexsupplies-billing.example" in ind.domains
    assert "portal.example-verify.top" in ind.domains
    assert "billing.apex.example" in ind.domains
    assert ind.ips == ["198.51.100.77"]
    assert "accounts@apexsupplies-billing.example" in ind.emails
    assert "boss@firm.example" in ind.emails          # claimed sender folded in
    assert ind.urls == ["http://portal.example-verify.top/login"]


def test_make_artifact_envelope():
    a = normalize.make_artifact("some evidence text", evidence_type="dns_log",
                                source="sample")
    assert a.id.startswith("art-") and len(a.sha256) == 64
    assert a.evidence_type == "dns_log" and a.source == "sample"


# ---------- parse ----------

def test_parse_artifact_defensive():
    r = parse.parse_artifact_analysis("no json here", artifact_id="art-x")
    assert not r.parsed and r.risk_level == "unknown" and r.confidence == 0
    r2 = parse.parse_artifact_analysis(
        '```{"risk_level":"HIGH","confidence":"88.5","signals":"a; b",'
        '"verdict":"v","recommended_action":"r","pqc_relevance":"none"}```',
        artifact_id="art-y")
    assert r2.parsed and r2.risk_level == "high" and r2.confidence == 88
    assert r2.signals == ["a", "b"]
    assert r2.extra.get("pqc_relevance") == "none"   # profile fields survive


def test_parse_fusion_defensive():
    f = parse.parse_fusion("garbage")
    assert not f["parsed"] and f["overall_risk"] == "unknown"
    f2 = parse.parse_fusion(json.dumps({
        "fused_verdict": "v", "overall_risk": "medium",
        "overall_confidence": 250,
        "attribution_hypotheses": [
            {"hypothesis": "vendor impersonation", "campaign_type": "BEC",
             "confidence": 70, "alternatives": ["legit vendor change"]},
            "not-a-dict-ignored"],
    }))
    assert f2["parsed"] and f2["overall_confidence"] == 100
    assert len(f2["attribution"]) == 1
    assert f2["attribution"][0].alternatives == ["legit vendor change"]


# ---------- fuse (deterministic) ----------

def _mk_analysis(aid, domains=(), ips=(), risk="high"):
    return parse.parse_artifact_analysis(
        json.dumps({"risk_level": risk, "confidence": 60, "verdict": "v",
                    "signals": [], "recommended_action": "r"}),
        artifact_id=aid,
        indicators=IndicatorSet(domains=list(domains), ips=list(ips)))


def test_campaign_clustering_connected_components():
    a = _mk_analysis("art-1", domains=["evil.example.top"])
    b = _mk_analysis("art-2", domains=["evil.example.top", "other.example"])
    c = _mk_analysis("art-3", domains=["other.example"])
    d = _mk_analysis("art-4", domains=["unrelated.example"])
    campaigns = fuse.cluster_campaigns([a, b, c, d])
    assert len(campaigns) == 1
    camp = campaigns[0]
    assert camp.artifact_ids == ["art-1", "art-2", "art-3"]   # transitive
    assert "evil.example.top" in camp.shared_indicators
    assert camp.campaign_id.startswith("CAMP-")
    assert camp.confidence <= 80                              # honesty cap
    # durable id: same inputs -> same id
    again = fuse.cluster_campaigns([a, b, c, d])[0]
    assert again.campaign_id == camp.campaign_id


def test_graph_build():
    a = _mk_analysis("art-1", domains=["evil.example.top"],
                     ips=["203.0.113.9"])
    arts = [normalize.make_artifact("x", label="Email A",
                                    claimed_sender="ceo@firm.example")]
    arts[0].id = "art-1"
    campaigns = []
    entities, rels = fuse.build_graph(arts, [a], campaigns)
    types = {e.type for e in entities}
    assert {"artifact", "domain", "ip", "sender"} <= types
    assert any(r.relation == "claimed sender of" for r in rels)
    assert all(r.artifact_ids for r in rels)                  # provenance


def test_confidence_aggregation_caps():
    good = _mk_analysis("a1", domains=["x.example"])
    bad = parse.parse_artifact_analysis("junk", artifact_id="a2")
    assert fuse.aggregate_confidence([good, good], 90, True) == 90
    assert fuse.aggregate_confidence([good, bad], 90, True) == 67   # capped
    assert fuse.aggregate_confidence([good], 90, False) <= 40      # no fusion


# ---------- pipeline (mock router end-to-end) ----------

ARTIFACT_OUT = json.dumps({
    "risk_level": "high", "confidence": 75,
    "verdict": "Vendor impersonation indicators.",
    "signals": ["urgency", "new bank details"],
    "organization_context_questions": ["Is this vendor real?"],
    "recommended_action": "verify out of band"})

FUSION_OUT = json.dumps({
    "fused_verdict": "Two artifacts share infrastructure; coordinated "
                     "vendor-fraud attempt likely.",
    "overall_risk": "high", "overall_confidence": 80,
    "corroborations": ["art emails share evil.example.top"],
    "contradictions": [], "missing_evidence": ["DNS registration data"],
    "attribution_hypotheses": [{
        "hypothesis": "vendor-impersonation payment fraud campaign",
        "campaign_type": "BEC/vendor fraud", "confidence": 65,
        "supporting_evidence": ["shared domain"], "missing_evidence": ["headers"],
        "alternatives": ["coincidental reuse of shared hosting"]}],
    "recommendations": ["freeze payment change"]})


def make_scripted_router():
    calls = {"n": 0}

    def gen(prompt):
        calls["n"] += 1
        if "fusion engine" in prompt:
            return {"ok": True, "response": FUSION_OUT,
                    "event_id": f"evt-fuse-{calls['n']}"}
        return {"ok": True, "response": ARTIFACT_OUT,
                "event_id": f"evt-art-{calls['n']}"}
    return CallableRouter(gen), calls


def test_pipeline_end_to_end():
    arts = [
        normalize.make_artifact(
            "Pay now via http://pay.evil.example.top/inv", label="Email A",
            evidence_type="email_message"),
        normalize.make_artifact(
            "query: pay.evil.example.top IN A", label="DNS log",
            evidence_type="dns_log"),
    ]
    router, calls = make_scripted_router()
    rpt = pipeline.analyze_artifacts(arts, router, profile_mode="msp")
    assert isinstance(rpt, FusedReport)
    assert calls["n"] == 3                       # 2 artifacts + 1 fusion
    assert rpt.overall_risk == "high" and rpt.fusion_parsed
    assert rpt.overall_confidence == 80
    assert len(rpt.artifact_analyses) == 2
    assert all(a.event_id.startswith("evt-art") for a in rpt.artifact_analyses)
    assert rpt.fusion_event_id.startswith("evt-fuse")
    assert len(rpt.campaigns) == 1               # shared domain clustered
    assert rpt.attribution[0].alternatives      # alternatives required
    assert any("Origin" in l for l in rpt.limitations)  # honesty baked in
    d = rpt.to_dict()                            # schema round-trip
    assert d["campaigns"][0]["campaign_id"].startswith("CAMP-")
    assert {e["type"] for e in d["entities"]} >= {"artifact", "domain", "campaign"}


def test_pipeline_degrades_honestly_on_failures():
    arts = [normalize.make_artifact("evidence one at bad.example.top",
                                    label="A"),
            normalize.make_artifact("evidence two at bad.example.top",
                                    label="B")]
    router = CallableRouter(lambda p: {"ok": False, "error": "runtime down",
                                       "response": ""})
    rpt = pipeline.analyze_artifacts(arts, router)
    assert rpt.overall_risk == "unknown"
    assert rpt.overall_confidence <= 40
    assert not rpt.fusion_parsed
    assert len(rpt.campaigns) == 1               # deterministic layer survives
    assert any("failed" in l for l in rpt.limitations)


def test_openclaw_router_field_mapping():
    r = OpenClawRouter(lambda p: {"ok": True, "response": "x",
                                  "openclaw_event_id": "evt-9"})
    out = r.generate("p")
    assert out["event_id"] == "evt-9"
    r2 = OpenClawRouter(lambda p: 1 / 0)
    assert r2.generate("p")["ok"] is False       # never raises


def test_pipeline_caps_artifacts():
    arts = [normalize.make_artifact(f"text {i}", label=f"A{i}")
            for i in range(9)]
    router, calls = make_scripted_router()
    rpt = pipeline.analyze_artifacts(arts, router)
    assert len(rpt.artifact_analyses) == pipeline.MAX_ARTIFACTS

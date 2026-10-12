"""
verity_core.artifacts.fuse — deterministic cross-artifact correlation.

Everything here is computed, not model-generated: shared-infrastructure
detection, within-submission campaign clustering (connected components over
shared indicators), and the Atlas entity/relationship graph. Model-assisted
fusion (verdict, hypotheses) happens in pipeline.py on top of this.
"""
from collections import defaultdict

from .schemas import CampaignIndicator, Entity, Relationship

_ENTITY_TYPE = {"domains": "domain", "ips": "ip", "emails": "email",
                "urls": "url"}


def shared_indicators(analyses: list) -> dict:
    """indicator -> sorted list of artifact_ids that contain it (>=2 only)."""
    seen = defaultdict(set)
    for a in analyses:
        for ind in a.indicators.all():
            seen[ind].add(a.artifact_id)
    return {ind: sorted(ids) for ind, ids in seen.items() if len(ids) >= 2}


def cluster_campaigns(analyses: list) -> list:
    """Connected components over shared indicators → CampaignIndicator list.

    Only clusters of >=2 artifacts become campaigns. Confidence is a bounded
    function of how much infrastructure is shared, never above 80: within-
    submission correlation cannot justify more.
    """
    shared = shared_indicators(analyses)
    # union-find over artifact ids
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for ids in shared.values():
        for other in ids[1:]:
            union(ids[0], other)

    groups = defaultdict(set)
    for ind, ids in shared.items():
        for aid in ids:
            groups[find(aid)].add(aid)

    campaigns = []
    for root, members in groups.items():
        if len(members) < 2:
            continue
        inds = sorted({ind for ind, ids in shared.items()
                       if set(ids).issubset(members)})
        conf = min(80, 30 + 10 * len(inds))
        campaigns.append(CampaignIndicator(
            artifact_ids=sorted(members),
            shared_indicators=inds,
            basis="shared infrastructure within this submission",
            confidence=conf,
        ))
    return sorted(campaigns, key=lambda c: c.campaign_id)


def build_graph(artifacts: list, analyses: list, campaigns: list) -> tuple:
    """Atlas feed: (entities, relationships) with per-artifact provenance."""
    entities = {}
    rels = []

    def ent(eid, etype, label):
        if eid not in entities:
            entities[eid] = Entity(id=eid, type=etype, label=label)
        return entities[eid]

    art_by_id = {a.id: a for a in artifacts}
    for an in analyses:
        art = art_by_id.get(an.artifact_id)
        label = (art.label if art else an.artifact_id)
        ent(an.artifact_id, "artifact", label)
        if art and art.claimed_sender:
            sid = "sender:" + art.claimed_sender.lower()
            ent(sid, "sender", art.claimed_sender)
            rels.append(Relationship(source=sid, target=an.artifact_id,
                                     relation="claimed sender of",
                                     artifact_ids=[an.artifact_id]))
        if art and art.claimed_org:
            oid = "org:" + art.claimed_org.lower()
            ent(oid, "organization", art.claimed_org)
            rels.append(Relationship(source=oid, target=an.artifact_id,
                                     relation="claimed org of",
                                     artifact_ids=[an.artifact_id]))
        ind = an.indicators
        for field_name, etype in _ENTITY_TYPE.items():
            for v in getattr(ind, field_name):
                nid = f"{etype}:{v}"
                ent(nid, etype, v)
                rels.append(Relationship(source=an.artifact_id, target=nid,
                                         relation="contains",
                                         artifact_ids=[an.artifact_id]))
    for c in campaigns:
        ent(c.campaign_id, "campaign", c.campaign_id)
        for aid in c.artifact_ids:
            rels.append(Relationship(source=c.campaign_id, target=aid,
                                     relation="clusters",
                                     artifact_ids=[aid]))
    return list(entities.values()), rels


def correlation_summary(analyses: list, campaigns: list) -> dict:
    """Compact, trustworthy summary handed to the fusion model pass."""
    return {
        "shared_indicators": shared_indicators(analyses),
        "campaign_clusters": [
            {"campaign_id": c.campaign_id, "artifact_ids": c.artifact_ids,
             "shared_indicators": c.shared_indicators}
            for c in campaigns],
        "per_artifact_risk": {a.artifact_id: a.risk_level for a in analyses},
    }


def aggregate_confidence(analyses: list, fusion_conf: int,
                         fusion_parsed: bool) -> int:
    """Bounded overall confidence: fusion value, capped by evidence quality."""
    if not analyses:
        return 0
    parsed = [a for a in analyses if a.parsed]
    parse_ratio = len(parsed) / len(analyses)
    cap = int(40 + 55 * parse_ratio)          # unparsed artifacts cap trust
    if not fusion_parsed:
        cap = min(cap, 40)
    return max(0, min(fusion_conf, cap))

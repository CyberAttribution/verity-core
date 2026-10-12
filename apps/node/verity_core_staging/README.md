# verity_core_staging — v0.1.5 artifacts layer (drop-in for CyberAttribution/verity-core)

**Status:** staged in the SwarmTracker Verity project repo; the live
`CyberAttribution/verity-core` repo is authoritative and is NOT in this
workspace. This staging tree is additive-only and designed to merge without
touching any existing verity-core module.

## Merge contract

- Copy `artifacts/` and `routers/` into the verity-core package root
  (becoming `verity_core.artifacts` and `verity_core.routers`).
- **Zero imports** from existing modules (`model_router`, `technical_intel`,
  `scraping`, `storage`) — no conflict is possible with code not visible here.
- Model transport is abstracted behind `routers.base.ModelRouter` (a
  2-method protocol). In b1, wrap the existing Gemini `model_router.py` in a
  ~15-line `routers/gemini.py` adapter; nothing else changes.
- `tests/` are pure-Python (mock router, no network) — portable to the
  verity-core CI as-is.
- Before merging: run the repo checklist recorded in
  `V015_ARCHITECTURE_AND_SPRINT_PLAN.md` §9.4.

## What this layer adds (v0.1.5 "Visible Intelligence")

Multi-artifact evidence envelope → per-artifact structured analysis →
deterministic correlation (shared indicators, campaign clustering, Atlas
entity/relationship graph) → model-assisted fusion (corroboration,
contradiction, bounded attribution hypotheses, missing evidence) → one
`FusedReport` schema shared by SwarmTracker Node (a2/a3) and public Verity
(b2). Two-stage orchestration is deliberate: a 7B local model cannot emit
one giant fused JSON over many raw artifacts; per-artifact + fusion passes
work on both local and cloud runtimes.

Honesty invariants baked in: no named threat actors; campaign detection is
within-submission (longitudinal tracking deferred to Origin — schema
carries durable campaign IDs for later adoption); every report carries
explicit `limitations`.

# Existing SwarmTracker Node application

This directory preserves the existing Node v0.5.0 release source under version control alongside Verity Core. It is the existing browser UI, not a new desktop application or a deployment.

Run from this directory with the repository's Core installed and FastAPI, requests, httpx, Jinja2 and python-multipart available:
    python -m uvicorn swarmtracker_node_ui.app:app --host 127.0.0.1 --port 8077

The app expects its existing legacy local runtime and Ollama endpoints. Do not confuse that legacy /v0/generate service with current OpenClaw plugin hooks. No live deployment has been verified by this import.

The original vendored Core remains for compatibility, including Vertex support not yet reconciled into the installed Core. The bridge can mix installed analysis modules with vendored optional-provider modules; consolidated provider provenance remains follow-up work. The import preserves existing behavior and does not add OS sandboxing or model forward-pass intervention.

Baseline governed tests:
    python -m pytest swarmtracker_node_ui/tests_governed.py -q

All verification in this session uses mocked providers and temporary stores. No provider credentials or user media are included.

## Per-job operator authorization for cloud-assist (deny by default)

Shared cloud-assist entry points in `swarmtracker_node_ui.governed_analysis`
(`build_router`, `run_text_cloud_assist`) require an explicit
keyword-only `operator_authorized=True` per job — only the boolean
`True` counts; truthy strings, numbers, collections, and omitted/false
values are refused. With no valid signal the service makes ZERO
readiness, provider-build, or generate calls and returns an honest
denied result. The `workflow` parameter is also keyword-only; the
reserved sensitive workflow is refused at these entry points
(`ReservedWorkflowError`), independent of UI guards.

Operator intent is DISTINCT from execution authorization:

- missing/malformed intent → `authorized: False, denied: True`
- Keel policy refusal (with intent given) → `authorized: False,
  denied: True, reason: <policy why>`
- provider unavailability/build failure after authorization →
  `authorized: True, denied: False, available: False` (NOT a denial)

Denial honesty holds through the metadata and provenance helpers:
`text_assist_router_meta` shapes a denied job with
`escalation_provider: None`, empty `attempted_models`/`sent_artifacts`,
and `denied`/`reason` fields; `canonical_case._escalation_record`
classifies it as state `"denied"`; the report and fused-report
templates render a dedicated "Governed cloud-assist — denied" panel.
Authorization is never persisted — every job starts denied.

LIMITATION: `operator_authorized` is a trusted local-caller intent
signal, NOT authenticated human identity, a session proof, or
cryptographic approval. Anything with runtime/host access can call the
functions directly with `operator_authorized=True`; the gate exists so
the governed service itself never escalates on implicit state (model
choice, installed credentials, readiness, prior jobs).

Regression tests:
    python -m pytest swarmtracker_node_ui/tests_governed.py \
        swarmtracker_node_ui/test_service_authorization.py -q

"""
SwarmTracker Node UI v0.1 — local FastAPI app.

Run from the project root (~/openclaw-v0):
    uvicorn swarmtracker_node_ui.app:app --host 127.0.0.1 --port 8077

Additive only. Talks to OpenClaw on 127.0.0.1:8090 and reads
logs/openclaw_events.jsonl. No cloud calls, no writes to existing files.
"""
import json
import sqlite3
import hashlib
import os
import tempfile
import time
import uuid
from collections import OrderedDict

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse, FileResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import adapters, analysis, canonical_case, config, core_bridge
from . import governance, keel_events, keel_policy, ledger, media_runner
from . import agents, governed_analysis, providers, decision_routes, monitoring, local_activity, report_routes, sessions, workflows, product_demo
from . import openclaw_client as oc
from . import store as store_mod

app = FastAPI(title="Verity by SwarmTracker", docs_url=None, redoc_url=None)

# Local investigation persistence (v0.2.0). Fail-soft: if the DB can't open
# (corrupt/locked), the app still boots and analysis works; investigation
# features report the problem instead of silently reinitializing.
STORE = None
STORE_ERROR = None
try:
    STORE = store_mod.Store()
except Exception as _e:  # CorruptDatabase or filesystem
    STORE_ERROR = str(_e)
app.mount("/static", StaticFiles(directory=str(config.PKG_DIR / "static")),
          name="static")
templates = Jinja2Templates(directory=str(config.PKG_DIR / "templates"))


def render(name: str, ctx: dict):
    """TemplateResponse across Starlette versions (request-first vs legacy)."""
    if "router_meta" in ctx:
        ctx["execution"] = governed_analysis.execution_record(ctx["router_meta"])
    try:
        return templates.TemplateResponse(ctx["request"], name, ctx)
    except TypeError:
        return templates.TemplateResponse(name, ctx)


def base_ctx(request: Request, page: str) -> dict:
    ctx = {
        "request": request,
        "page": page,
        "cfg": {
            "node": config.NODE_NAME,
            "mode": config.MODE,
            "policy": config.KEEL_POLICY,
            "cloud": config.CLOUD_ESCALATION,
            "model": config.DEFAULT_MODEL,
            "hardware": config.HARDWARE,
            "hardware_next": config.HARDWARE_NEXT,
            "runtime": "Ollama (direct local)" if config.RUNTIME_KIND == "ollama" else config.RUNTIME_LABEL,
            "openclaw_url": config.OPENCLAW_URL,
            "ollama_url": config.OLLAMA_URL,
            "ledger_path": str(config.LEDGER_PATH),
        },
    }
    if page in ("agents", "governance", "sessions"):
        from .workbench_routes import summary as routed_summary
        ctx["routed_requests"] = routed_summary(request)
        from .company_routes import database as company_database
        try:
            ctx["company_work_summaries"] = company_database().recent()
        except (sqlite3.Error, OSError, ValueError):
            ctx["company_work_summaries"] = []
            ctx["company_work_error"] = True
    return ctx


def node_status() -> dict:
    hz = oc.healthz()
    models = oc.ollama_models()
    runtime_online = isinstance(hz,dict) and (hz.get("status")=="ok" or hz.get("ok") is True)
    model_online = config.DEFAULT_MODEL in models if models else False
    return {
        "runtime_online": runtime_online,
        "model_online": model_online,
        "node_online": runtime_online and model_online,
        "healthz": hz,
        "node_name": hz.get("node") or config.NODE_NAME,
        "provider": hz.get("provider") or "—",
        "schema": hz.get("event_schema_version") or "—",
        "reported_model": hz.get("default_model") or config.DEFAULT_MODEL,
        "ollama_models": models,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    ctx = base_ctx(request, "dashboard")
    ctx["status"] = node_status()
    from . import customer_routes, workbench_routes, overview, store
    try:
        ctx['workflow']=overview.workflow(customer_routes.database(),workbench_routes.runs(),request.query_params.get('agent',''),request.query_params.get('resource',''))
    except (sqlite3.Error,OSError,ValueError):
        ctx['workflow']=dict(state='unknown',detail='Configuration or request store unavailable. Review Models & Connections.',choices=[],connections=[])
    ctx['application_health']='Available' if not STORE_ERROR else 'Storage needs attention'
    ctx['profile_path']=str(store.DB_PATH.parent)
    ctx['request_inventory']=overview.store_inventory(store.DB_PATH.with_name('local-inference.db'))
    ctx['company_inventory']=overview.store_inventory(store.DB_PATH.with_name('company-work.db'))
    ctx['primary_inventory']=overview.store_inventory(store.DB_PATH)
    if any(ctx[name]['state']=='unknown' for name in ('primary_inventory','request_inventory','company_inventory')):
        ctx['application_health']='Responding; evidence storage needs attention'
    ctx['context_inventory']=overview.context_inventory(store.DB_PATH.with_name('organization-context.db'))
    ctx['ledger_scope']='not created' if not config.LEDGER_PATH.exists() else 'readable' if config.LEDGER_PATH.is_file() and __import__('os').access(config.LEDGER_PATH,__import__('os').R_OK) else 'unknown'
    ctx["ledger_count"] = ledger.count_events()
    events = ledger.read_events(limit=25)
    ctx["recent"] = [e for e in events if "generate" in e["event"].lower()][:8] \
        or events[:8]
    response=render("dashboard.html", ctx)
    response.headers['Cache-Control']='no-store'
    return response


def modes_ctx() -> dict:
    """Selector data for the analyze form (v0.1.1, extended v0.1.4)."""
    return {
        "profile_modes": analysis.PROFILE_MODES,
        "profile_descriptions": analysis.PROFILE_DESCRIPTIONS,
        "analysis_modes": analysis.ANALYSIS_MODES,
        "analysis_mode_descriptions": analysis.ANALYSIS_MODE_DESCRIPTIONS,
        "implemented_modes": analysis.IMPLEMENTED_ANALYSIS_MODES,
        "mode_notice": analysis.MODE_NOTICE,
        "event_categories": analysis.EVENT_CATEGORIES,
        "evidence_type_groups": analysis.EVIDENCE_TYPE_GROUPS,
    }


# ---- Fused report session cache (v0.1.5 a3) ----
# Session cache for the Atlas viewer + the save-to-investigation step.
# Real persistence is now the SQLite store; this stays a short-lived buffer.
FUSED_REPORTS = OrderedDict()
FUSED_SUBMISSIONS = OrderedDict()   # report_id -> {items, profile_mode, meta}
MAX_STORED_REPORTS = 10


def store_report(report, submission=None):
    FUSED_REPORTS[report.report_id] = report
    if submission is not None:
        FUSED_SUBMISSIONS[report.report_id] = submission
    while len(FUSED_REPORTS) > MAX_STORED_REPORTS:
        rid, _ = FUSED_REPORTS.popitem(last=False)
        FUSED_SUBMISSIONS.pop(rid, None)


# ---- Media job cache (v0.3.4) for report export actions ----
MEDIA_JOBS = OrderedDict()


def _cache_media_job(token, payload):
    MEDIA_JOBS[token] = payload
    while len(MEDIA_JOBS) > MAX_STORED_REPORTS:
        MEDIA_JOBS.popitem(last=False)


# ---- Generic investigation job cache (v0.5.0) for export parity ----
GENERIC_JOBS = OrderedDict()


def _cache_generic_job(token, payload):
    GENERIC_JOBS[token] = payload
    while len(GENERIC_JOBS) > MAX_STORED_REPORTS:
        GENERIC_JOBS.popitem(last=False)


def _apply_escalation_ctx(ctx):
    """Shared escalation/provider context for any page with mode cards."""
    esc_allowed, esc_reason = keel_policy.multimodal_escalation_allowed()
    pcfg = providers.provider_config()
    ctx["escalation_allowed"] = esc_allowed
    ctx["escalation_reason"] = esc_reason
    ctx["escalation_ready"] = esc_allowed and pcfg["ready"]
    ctx["escalation_unready_note"] = "" if pcfg["ready"] else pcfg["reason"]
    ctx["provider"] = pcfg
    return ctx


def _mode_strings(rm):
    """(executed_mode, cloud_escalation, cloud_used, provider_display)
    from a router_meta dict — the single source for the report header,
    identical across Media Evidence and generic investigations."""
    rm = rm or {}
    esc_ok = bool(rm.get("escalated")) and bool(rm.get("escalation_ok"))
    prov = (rm.get("escalation_provider") or "vertex")
    prov_label = "Vertex AI" if prov == "vertex" else prov
    if rm.get("audit_write_failed") or \
            rm.get("escalation_auth_write_failed"):
        # Task 3 — pre-call authorization ledger write failure:
        # fail-closed Local Only with the explicit audit reason (never
        # mislabeled as a provider failure or a policy denial).
        return ("Local Only",
                "Not authorized — ledger write failed",
                "No — escalation not attempted "
                "(authorization ledger write failed)",
                "ollama_local")
    execution = governed_analysis.execution_record(rm)
    if execution["state"] in ("denied", "input_blocked", "unavailable",
                              "authorized_not_attempted"):
        return ("Local Only", execution["state"].replace("_", " ").title(),
                "No — provider not attempted", "ollama_local")
    if esc_ok:
        return ("Governed Hybrid", f"Authorized — {prov_label} (this job)",
                ("Yes — extracted artifacts only" if rm.get("supports_media")
                 else "Yes — bounded artifacts only"),
                f"ollama_local + {prov} (authorized)")
    if rm.get("escalated"):
        st = rm.get("provider_status")
        return ("Governed Hybrid", f"Authorized — {prov_label} (this job)",
                "Failed" + (f" (HTTP {st})" if st else "")
                + " — local findings preserved",
                f"ollama_local ({prov} escalation failed)")
    return ("Local Only", "Disabled", "No", "ollama_local")


# ---- Sample Evidence Library (v0.1.4, local synthetic files only) ----

SAMPLES_DIR = config.PKG_DIR / "samples"


def load_samples_index() -> list:
    try:
        return json.loads((SAMPLES_DIR / "index.json").read_text(
            encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


@app.get("/api/demo-case")
def get_demo_case():
    """Canonical deterministic demo case (vendor fraud, 3 artifacts).
    Byte-identical copy ships in the Public Verity embed so both surfaces
    render the same graph structure."""
    try:
        return json.loads((SAMPLES_DIR / "demo_case_vendor_fraud.json")
                          .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return JSONResponse({"error": "demo case unavailable"},
                            status_code=404)


@app.get("/api/samples/{key}")
def get_sample(key: str):
    """Return one synthetic sample (metadata + text). Local files only;
    the key must exist in samples/index.json — no path input is used."""
    for meta in load_samples_index():
        if meta.get("key") == key:
            try:
                text = (SAMPLES_DIR / meta["file"]).read_text(
                    encoding="utf-8", errors="replace")
            except OSError:
                return JSONResponse({"error": "sample file unreadable"},
                                    status_code=500)
            if len(text) > 64_000:
                text = text[:64_000]
            return {"meta": meta, "text": text}
    return JSONResponse({"error": "unknown sample"}, status_code=404)


def _clean_modes(profile_mode: str, analysis_mode: str,
                 event_category: str) -> tuple:
    """Validate selections; fall back to safe defaults."""
    if profile_mode not in analysis.PROFILE_MODES:
        profile_mode = "smb"
    if analysis_mode not in analysis.ANALYSIS_MODES:
        analysis_mode = "local_only"
    cats = [k for k, _ in analysis.EVENT_CATEGORIES[profile_mode]]
    if event_category not in cats:
        event_category = cats[0]
    return profile_mode, analysis_mode, event_category


def gov_modes_ctx() -> dict:
    """Per-mode governance indicators for the analyze form (v0.1.2)."""
    gov = governance.assess()
    ind = governance.mode_indicators(gov["overall"],
                                     gov["overall_confidence"])
    ind["overall"] = gov["overall"]
    ind["overall_confidence"] = gov["overall_confidence"]
    return ind


@app.get("/analyze", response_class=HTMLResponse)
def analyze_form(request: Request):
    ctx = base_ctx(request, "analyze")
    ctx["status"] = node_status()
    ctx["modes"] = modes_ctx()
    ctx["gov_modes"] = gov_modes_ctx()
    ctx["samples"] = load_samples_index()
    _apply_escalation_ctx(ctx)
    return render("analyze.html", ctx)


@app.post("/analyze", response_class=HTMLResponse)
def analyze_run(request: Request,
                input_type: str = Form("text"),
                evidence: str = Form(""),
                claimed_sender: str = Form(""),
                claimed_org: str = Form(""),
                requested_action: str = Form(""),
                profile_mode: str = Form("smb"),
                analysis_mode: str = Form("local_only"),
                event_category: str = Form(""),
                evidence_type: str = Form(""),
                sample_source: str = Form(""),
                uploaded_filename: str = Form(""),
                escalate_multimodal: str = Form(""),
                vertex_model: str = Form(""),
                policy: str = Form("")):
    ctx = base_ctx(request, "analyze")
    _apply_escalation_ctx(ctx)
    ctx["modes"] = modes_ctx()
    ctx["samples"] = load_samples_index()
    profile_mode, analysis_mode, event_category = _clean_modes(
        profile_mode, analysis_mode, event_category)
    evidence_type = analysis.resolve_evidence_type(evidence_type, input_type)
    sample_source = sample_source.strip()[:120]
    uploaded_filename = uploaded_filename.strip()[:120]
    prefill = {
        "input_type": input_type, "evidence": evidence,
        "claimed_sender": claimed_sender, "claimed_org": claimed_org,
        "requested_action": requested_action, "profile_mode": profile_mode,
        "analysis_mode": analysis_mode, "event_category": event_category,
        "evidence_type": evidence_type, "sample_source": sample_source,
        "uploaded_filename": uploaded_filename,
    }
    if not evidence.strip():
        ctx["status"] = node_status()
        ctx["form_error"] = "Evidence is required."
        ctx["form_prefill"] = prefill
        ctx["gov_modes"] = gov_modes_ctx()
        return render("analyze.html", ctx)

    # v0.1.3 Keel policy lock: the form does not expose a policy field, so
    # any submitted policy value is tampering or a future form change —
    # validate it against the local policy file BEFORE any runtime call.
    policy_ok, policy_reason = keel_policy.validate_policy(policy)
    if not policy_ok:
        ctx["status"] = node_status()
        ctx["form_error"] = ("Keel policy lock: " + policy_reason +
                             ". No analysis was run.")
        ctx["form_prefill"] = prefill
        ctx["gov_modes"] = gov_modes_ctx()
        return render("analyze.html", ctx)
    locked_policy = keel_policy.enforced_policy(policy)

    prompt = analysis.build_prompt(input_type, evidence, claimed_sender,
                                   claimed_org, requested_action,
                                   profile_mode=profile_mode,
                                   analysis_mode=analysis_mode,
                                   event_category=event_category,
                                   evidence_type=evidence_type)
    # Regardless of the selected analysis mode, execution is Local Only:
    # the Keel policy file enables no other executable mode (v0.1.3).
    result = local_activity.generate(lambda: oc.generate(prompt, policy=locked_policy))

    if not result.get("ok"):
        ctx["status"] = node_status()
        ctx["form_error"] = result.get("error", "Local analysis failed.")
        ctx["form_prefill"] = prefill
        ctx["gov_modes"] = gov_modes_ctx()
        return render("analyze.html", ctx)

    report = analysis.parse_result(result["response"],
                                   profile_mode=profile_mode,
                                   evidence_type=evidence_type)
    event_id = result.get("openclaw_event_id")

    # v0.5.0: single-artifact Analyze reuses the shared governed service
    # for Local/Governed + provider provenance + exports (no separate
    # implementation). Local analysis above always runs locally.
    one_item = [{"label": (uploaded_filename or sample_source
                           or "evidence"),
                 "text": evidence, "evidence_type": evidence_type,
                 "claimed_sender": claimed_sender,
                 "claimed_org": claimed_org,
                 "requested_action": requested_action}]
    escalate = (escalate_multimodal == "1")
    cloud = governed_analysis.run_text_cloud_assist(
        one_item, workflow="single_analysis",
        selected_model=vertex_model.strip() or None,
        operator_authorized=escalate) if escalate else None
    rm = governed_analysis.text_assist_router_meta(cloud) if cloud \
        else {"escalated": False, "cloud_requested": False,
              "operator_authorized": False, "authorized": False,
              "provider_attempted": False}
    executed_mode, cloud_escalation, cloud_used, _pd = _mode_strings(rm)
    token = "sa-" + uuid.uuid4().hex
    # build an export-ready report dict (indicators as entities) so
    # JSON/Report/Atlas/Briefing exports work identically to fused
    from verity_core_staging.artifacts import normalize as _norm
    ind = _norm.extract_indicators(evidence, claimed_sender)
    ents = ([{"id": d, "type": "domain", "label": d} for d in ind.domains]
            + [{"id": i, "type": "ip", "label": i} for i in ind.ips]
            + [{"id": u, "type": "url", "label": u} for u in ind.urls]
            + [{"id": e, "type": "sender", "label": e}
               for e in ind.emails])
    export_report = {"report_id": token,
                     "fused_verdict": report.get("verdict", ""),
                     "overall_risk": report.get("risk_level", "unknown"),
                     "overall_confidence": report.get("confidence", 0),
                     "entities": ents, "relationships": [],
                     "attribution": [], "campaigns": [],
                     "limitations": ["Single-artifact analysis; "
                                     "corroborate out-of-band."]}
    _cache_generic_job(token, {
        "report_id": token, "kind": "single_analysis",
        "decision_participant": {
            "id": "runtime-" + hashlib.sha256(
                json.dumps([result.get("node"), result.get("model"), result.get("provider")], sort_keys=True).encode()).hexdigest()[:24],
            "name": str(result.get("node") or "Unreported runtime"),
            "framework": result.get("framework") or "Legacy OpenClaw v0 API",
            "model": str(result.get("model") or "Not reported"),
            "identity_provenance": ("configured local runtime; self-reported; no runtime audit event or attestation"
                                    if config.RUNTIME_KIND == "ollama" else "runtime self-reported; not independently verified"),
            "delegated_by": None, "runtime_event_id": event_id, "activity_run_id":result.get("activity_run_id")},
        "workflow": "single_analysis", "profile_mode": profile_mode,
        "executed_mode": executed_mode, "cloud_used": cloud_used,
        "report": export_report, "items": one_item,
        "cloud_assist": cloud, "router_meta": rm})
    ctx["report_token"] = token
    ctx["cloud_assist"] = cloud
    ctx["router_meta"] = rm

    ctx["page"] = "report"
    ctx["report"] = report
    ctx["meta"] = {
        "event_id": event_id or "not recorded",
        "model": result.get("model"),
        "policy": result.get("policy"),
        "provider": result.get("provider") or "ollama_local",
        "node": result.get("node") or config.NODE_NAME,
        "decision": result.get("decision") or "—",
        "governance_alarm": result.get("governance_alarm"),
        "governance_failures": result.get("governance_failures") or [],
        "input_type": analysis.INPUT_TYPES.get(input_type, "Text"),
        "raw_response": result.get("response") or "",
        # Keel Trace (v0.1.1)
        "profile_mode": analysis.PROFILE_MODES[profile_mode],
        "analysis_mode": analysis.ANALYSIS_MODES[analysis_mode],
        "analysis_mode_executed": analysis_mode in
            analysis.IMPLEMENTED_ANALYSIS_MODES,
        "event_category": analysis.category_label(profile_mode,
                                                  event_category),
        "evidence_type": analysis.evidence_type_label(evidence_type),
        "executed_mode": executed_mode,
        "cloud_escalation": cloud_escalation, "cloud_used": cloud_used,
        "sample_source": sample_source,
        "uploaded_filename": uploaded_filename,
        "data_boundary": config.DATA_BOUNDARY,
        "tools_allowed": config.TOOLS_ALLOWED,
        "tools_denied": config.TOOLS_DENIED,
        "evidence_role": config.EVIDENCE_ROLE,
    }
    pol = keel_policy.policy_summary()
    ctx["meta"].update({
        "policy": result.get("policy") or locked_policy,
        "policy_source": pol["source"],
        "policy_version": pol["version"],
        "policy_lock_ok": pol["ok"],
        "data_boundary": pol["data_boundary"],
        "tools_allowed": pol["tools_allowed"],
        "tools_denied": pol["tools_denied"],
        "evidence_role": pol["evidence_role"],
    })
    return render("report.html", ctx)


@app.post("/analyze/fused", response_class=HTMLResponse)
def analyze_fused(request: Request,
                  artifacts_json: str = Form("[]"),
                  profile_mode: str = Form("smb"),
                  analysis_mode: str = Form("local_only"),
                  escalate_multimodal: str = Form(""),
                  vertex_model: str = Form(""),
                  workflow: str = Form("general_evidence"),
                  policy: str = Form("")):
    """Multi-artifact fusion — LOCAL two-stage pipeline (fusion always
    local). v0.5.0: optional per-job Governed Cloud-Assisted Analysis
    sends a BOUNDED evidence digest to the provider and attaches a
    labeled supplement; nothing else leaves the node."""
    ctx = base_ctx(request, "analyze")
    ctx["modes"] = modes_ctx()
    ctx["samples"] = load_samples_index()
    _apply_escalation_ctx(ctx)

    def back_with_error(msg):
        ctx["status"] = node_status()
        ctx["form_error"] = msg
        ctx["gov_modes"] = gov_modes_ctx()
        return render("analyze.html", ctx)

    # reserved sensitive-media workflow is inactive in v0.5.0
    try:
        governed_analysis.guard_workflow(workflow)
    except governed_analysis.ReservedWorkflowError as e:
        return back_with_error(str(e))

    policy_ok, policy_reason = keel_policy.validate_policy(policy)
    if not policy_ok:
        return back_with_error("Keel policy lock: " + policy_reason +
                               ". No analysis was run.")
    locked_policy = keel_policy.enforced_policy(policy)

    if profile_mode not in analysis.PROFILE_MODES:
        profile_mode = "smb"
    if analysis_mode not in analysis.ANALYSIS_MODES:
        analysis_mode = "local_only"

    try:
        items = json.loads(artifacts_json or "[]")
        assert isinstance(items, list)
    except (json.JSONDecodeError, AssertionError):
        return back_with_error("Fusion batch was malformed; nothing was run.")
    items = [i for i in items if isinstance(i, dict) and
             str(i.get("text", "")).strip()]
    if not items:
        return back_with_error("Fusion batch is empty — add at least one "
                               "artifact with evidence text.")

    # local fusion — ALWAYS local, regardless of escalation
    report = core_bridge.run_fused(
        items, profile_mode,
        lambda p: oc.generate(p, policy=locked_policy))

    # optional governed cloud-assist over a BOUNDED digest (Part 2)
    escalate = (escalate_multimodal == "1")
    cloud = governed_analysis.run_text_cloud_assist(
        items, workflow=workflow,
        selected_model=vertex_model.strip() or None,
        operator_authorized=escalate) if escalate else None
    rm = governed_analysis.text_assist_router_meta(cloud) if cloud \
        else {"escalated": False, "cloud_requested": False,
              "operator_authorized": False, "authorized": False,
              "provider_attempted": False}
    executed_mode, cloud_escalation, cloud_used, provider_display = \
        _mode_strings(rm)

    store_report(report, submission={
        "items": items, "profile_mode": profile_mode,
        "workflow": workflow,
        "model": config.DEFAULT_MODEL, "policy": locked_policy})
    pol = keel_policy.policy_summary()
    ctx["page"] = "report"
    ctx["fused"] = report
    ctx["fused_dict"] = report.to_dict()
    ctx["cloud_assist"] = cloud
    ctx["router_meta"] = rm
    ctx["provenance"] = governed_analysis.provenance(
        None, escalated=rm.get("escalated"),
        escalate_requested=escalate,
        esc_reason=(cloud or {}).get("reason", ""),
        attempted_models=rm.get("attempted_models", []),
        provider_status=rm.get("provider_status"),
        sent_manifest=rm.get("sent_artifacts", []),
        local_extraction="local — indicator/entity extraction "
                         "(deterministic)") if escalate else None
    ctx["can_save"] = store_ok()
    ctx["investigations"] = STORE.list_projects() if store_ok() else []
    ctx["report_token"] = report.report_id
    ctx["meta"] = {
        "profile_mode": analysis.PROFILE_MODES[profile_mode],
        "analysis_mode": analysis.ANALYSIS_MODES[analysis_mode],
        "analysis_mode_executed": True,
        "executed_mode": executed_mode,
        "cloud_escalation": cloud_escalation, "cloud_used": cloud_used,
        "policy": locked_policy,
        "policy_source": pol["source"],
        "policy_version": pol["version"],
        "data_boundary": pol["data_boundary"],
        "tools_allowed": pol["tools_allowed"],
        "tools_denied": pol["tools_denied"],
        "evidence_role": pol["evidence_role"],
        "model": config.DEFAULT_MODEL,
        "provider": provider_display,
        "node": config.NODE_NAME,
        "core_source": core_bridge.CORE_SOURCE,
        "artifact_count": len(report.artifact_analyses),
        "evidence_type_labels": {a.artifact_id: "" for a in
                                 report.artifact_analyses},
    }
    ctx["evidence_labels"] = analysis.EVIDENCE_TYPES
    # cache for exports (JSON / STIX / Report) parity with media
    _cache_generic_job(report.report_id, {
        "report_id": report.report_id, "kind": "investigation",
        "workflow": workflow, "profile_mode": profile_mode,
        "executed_mode": executed_mode, "cloud_used": cloud_used,
        "report": report.to_dict(), "items": items,
        "cloud_assist": cloud, "router_meta": rm,
        "provenance": ctx.get("provenance")})
    return render("fused_report.html", ctx)


# ================= Investigations (v0.2.0 persistence) =================
# Product language: "Investigation" in the UI; schema key remains `project`.

def store_ok():
    return STORE is not None


def inv_base(request: Request, page: str) -> dict:
    ctx = base_ctx(request, page)
    ctx["store_ok"] = store_ok()
    ctx["store_error"] = STORE_ERROR
    ctx["statuses"] = store_mod.INVESTIGATION_STATUSES
    return ctx


@app.get("/investigations", response_class=HTMLResponse)
def investigations_list(request: Request):
    ctx = inv_base(request, "investigations")
    ctx["investigations"] = STORE.list_projects(include_archived=True) \
        if store_ok() else []
    return render("investigations.html", ctx)


@app.post("/investigations/new")
def investigation_create(request: Request, name: str = Form(...),
                         description: str = Form("")):
    if not store_ok() or not name.strip():
        return RedirectResponse("/investigations", status_code=303)
    pid = STORE.create_project(name, description)
    return RedirectResponse(f"/investigations/{pid}", status_code=303)


@app.get("/investigations/{pid}", response_class=HTMLResponse)
def investigation_detail(request: Request, pid: str):
    if not store_ok():
        return RedirectResponse("/investigations", status_code=303)
    inv = STORE.get_project(pid)
    if not inv:
        return RedirectResponse("/investigations", status_code=303)
    ctx = inv_base(request, "investigations")
    ctx["inv"] = inv
    ctx["events"] = STORE.list_events(pid)
    ctx["artifacts"] = STORE.list_artifacts(pid)
    ctx["analyses"] = STORE.list_analyses(pid)
    ctx["notes"] = STORE.list_notes(pid)
    graph = STORE.project_graph(pid)
    ctx["graph_counts"] = {"entities": len(graph["entities"]),
                           "relationships": len(graph["relationships"])}
    return render("investigation_detail.html", ctx)


@app.post("/investigations/{pid}/edit")
def investigation_edit(request: Request, pid: str, name: str = Form(...),
                       description: str = Form(""), status: str = Form("open")):
    if store_ok():
        try:
            STORE.update_project(pid, name=name, description=description,
                                 status=status)
        except store_mod.StoreError:
            pass
    return RedirectResponse(f"/investigations/{pid}", status_code=303)


@app.post("/investigations/{pid}/events/new")
def investigation_add_event(request: Request, pid: str, name: str = Form(...),
                            summary: str = Form(""),
                            t_claimed: str = Form(""),
                            time_status: str = Form("unknown")):
    if store_ok() and name.strip():
        STORE.create_event(pid, name, summary=summary, t_claimed=t_claimed,
                           time_status=time_status)
    return RedirectResponse(f"/investigations/{pid}", status_code=303)


@app.post("/investigations/{pid}/notes/new")
def investigation_add_note(request: Request, pid: str, body: str = Form(...),
                           target_kind: str = Form("investigation"),
                           target_id: str = Form("")):
    if store_ok() and body.strip():
        STORE.add_note(pid, target_kind, target_id or pid, body)
    return RedirectResponse(f"/investigations/{pid}", status_code=303)


@app.post("/investigations/{pid}/markers/new")
def investigation_add_marker(request: Request, pid: str, label: str = Form(...),
                             t_claimed: str = Form(""),
                             time_status: str = Form("claimed")):
    if store_ok() and label.strip():
        STORE.add_marker(pid, label, t_claimed=t_claimed,
                         time_status=time_status)
    return RedirectResponse(f"/investigations/{pid}/timeline",
                            status_code=303)


@app.post("/investigations/{pid}/delete")
def investigation_delete(request: Request, pid: str,
                         confirm_name: str = Form(...)):
    if store_ok():
        try:
            STORE.delete_project(pid, confirm_name)
        except store_mod.StoreError as e:
            ctx = inv_base(request, "investigations")
            inv = STORE.get_project(pid)
            if inv:
                ctx["inv"] = inv
                ctx["events"] = STORE.list_events(pid)
                ctx["artifacts"] = STORE.list_artifacts(pid)
                ctx["analyses"] = STORE.list_analyses(pid)
                ctx["notes"] = STORE.list_notes(pid)
                ctx["graph_counts"] = {"entities": 0, "relationships": 0}
                ctx["delete_error"] = str(e)
                return render("investigation_detail.html", ctx)
    return RedirectResponse("/investigations", status_code=303)


@app.get("/investigations/{pid}/timeline", response_class=HTMLResponse)
def investigation_timeline(request: Request, pid: str):
    if not store_ok() or not STORE.get_project(pid):
        return RedirectResponse("/investigations", status_code=303)
    ctx = inv_base(request, "investigations")
    ctx["inv"] = STORE.get_project(pid)
    ctx["timeline"] = STORE.timeline(pid)
    return render("timeline.html", ctx)


@app.get("/investigations/{pid}/export")
def investigation_export(request: Request, pid: str):
    if not store_ok():
        return JSONResponse({"error": "persistence unavailable"},
                            status_code=503)
    try:
        pkg = STORE.export_project(pid)
    except store_mod.StoreError:
        return JSONResponse({"error": "investigation not found"},
                            status_code=404)
    body = json.dumps(pkg, indent=2, default=str)
    fn = f"investigation-{pid}.stproj.json"
    return PlainTextResponse(body, media_type="application/json",
                             headers={"Content-Disposition":
                                      f'attachment; filename="{fn}"'})


@app.post("/investigations/import")
async def investigation_import(request: Request):
    if not store_ok():
        return RedirectResponse("/investigations", status_code=303)
    form = await request.form()
    upload = form.get("package")
    try:
        raw = await upload.read() if hasattr(upload, "read") else b""
        pkg = json.loads(raw.decode("utf-8", "replace"))
        new_pid = STORE.import_project(pkg)
        return RedirectResponse(f"/investigations/{new_pid}", status_code=303)
    except (store_mod.StoreError, json.JSONDecodeError, AttributeError):
        return RedirectResponse("/investigations", status_code=303)


def _persist_report(pid, report, submission, event_id=None):
    """Save a session report + its artifacts into an investigation, then
    merge the graph with provenance pointing at the persisted artifacts."""
    items = submission.get("items", [])
    workflow = submission.get("workflow", "general_evidence")
    if event_id is None:
        event_id = STORE.create_event(
            pid, "Analysis " + (report.report_id or ""),
            summary=f"{len(items)} artifact(s), {workflow}",
            time_status="observed")
    # persist artifacts, mapping pipeline ids -> stored ids for provenance
    id_map = {}
    stored_ids = []
    analyses = report.artifact_analyses
    for i, item in enumerate(items):
        stored = STORE.add_artifact(
            pid, event_id, label=item.get("label", "artifact"),
            text=item.get("text", ""),
            evidence_type=item.get("evidence_type", "freeform_text"),
            event_category=item.get("event_category", ""),
            workflow=workflow, source=item.get("source", "manual"),
            claimed_sender=item.get("claimed_sender", ""),
            claimed_org=item.get("claimed_org", ""),
            requested_action=item.get("requested_action", ""))
        stored_ids.append(stored)
        if i < len(analyses):
            id_map[analyses[i].artifact_id] = stored
    xid = STORE.save_analysis(
        pid, kind="fused", report=report.to_dict(), artifact_ids=stored_ids,
        event_id=event_id, workflow=workflow,
        model=submission.get("model", ""), policy=submission.get("policy", ""),
        ledger_event_ids=[a.event_id for a in analyses if a.event_id],
        artifact_id_map=id_map)
    return xid


@app.post("/report/{report_id}/save")
def report_save(request: Request, report_id: str,
                investigation_id: str = Form(""),
                new_name: str = Form("")):
    """Analysis-first: save a session fused report to an existing
    investigation or a new one."""
    if not store_ok():
        return RedirectResponse("/analyze", status_code=303)
    report = FUSED_REPORTS.get(report_id)
    submission = FUSED_SUBMISSIONS.get(report_id)
    if not report or not submission:
        return RedirectResponse("/investigations", status_code=303)
    if new_name.strip():
        pid = STORE.create_project(new_name)
    elif investigation_id:
        pid = investigation_id
        if not STORE.get_project(pid):
            return RedirectResponse("/investigations", status_code=303)
    else:
        return RedirectResponse("/investigations", status_code=303)
    _persist_report(pid, report, submission)
    return RedirectResponse(f"/investigations/{pid}", status_code=303)


@app.post("/investigations/{pid}/analyze")
def investigation_analyze(request: Request, pid: str,
                          artifacts_json: str = Form("[]"),
                          profile_mode: str = Form("smb"),
                          workflow: str = Form("general_evidence"),
                          policy: str = Form("")):
    """Investigation-first: run fused analysis and persist it into the
    investigation in one step (same governed path)."""
    if not store_ok() or not STORE.get_project(pid):
        return RedirectResponse("/investigations", status_code=303)
    policy_ok, _ = keel_policy.validate_policy(policy)
    if not policy_ok:
        return RedirectResponse(f"/investigations/{pid}", status_code=303)
    locked_policy = keel_policy.enforced_policy(policy)
    if profile_mode not in analysis.PROFILE_MODES:
        profile_mode = "smb"
    try:
        items = json.loads(artifacts_json or "[]")
        assert isinstance(items, list)
    except (json.JSONDecodeError, AssertionError):
        return RedirectResponse(f"/investigations/{pid}", status_code=303)
    items = [i for i in items if isinstance(i, dict)
             and str(i.get("text", "")).strip()]
    if not items:
        return RedirectResponse(f"/investigations/{pid}", status_code=303)
    report = core_bridge.run_fused(
        items, profile_mode, lambda p: oc.generate(p, policy=locked_policy))
    _persist_report(pid, report, {
        "items": items, "workflow": workflow,
        "model": config.DEFAULT_MODEL, "policy": locked_policy})
    return RedirectResponse(f"/investigations/{pid}", status_code=303)


# ================= Media Evidence (Deepfake Defender workflow) =========

def media_ctx(request: Request):
    ctx = base_ctx(request, "media")
    ctx["status"] = node_status()
    ctx["can_save"] = store_ok()
    ctx["investigations"] = STORE.list_projects() if store_ok() else []
    ctx["network_allowed"] = keel_policy.policy_summary()["network_allowed"]
    ctx["media_pipeline_available"] = core_bridge.HAS_MEDIA_INGEST
    ctx["accept_attr"] = media_runner.ACCEPT_ATTR
    ctx["accepted_extensions"] = media_runner.ACCEPTED_EXTENSIONS
    allowed, reason = keel_policy.media_retrieval_allowed()
    ctx["retrieval_allowed"] = allowed
    ctx["retrieval_reason"] = reason
    esc_allowed, esc_reason = keel_policy.multimodal_escalation_allowed()
    pcfg = providers.provider_config()
    ctx["escalation_allowed"] = esc_allowed
    ctx["escalation_reason"] = esc_reason
    ctx["escalation_ready"] = esc_allowed and pcfg["ready"]
    ctx["escalation_unready_note"] = (
        "" if pcfg["ready"] else pcfg["reason"])
    ctx["provider"] = pcfg          # name, project, region, models, …
    return ctx


@app.get("/media", response_class=HTMLResponse)
def media_form(request: Request):
    ctx = media_ctx(request)
    return render("media.html", ctx)


@app.get("/api/media-demo")
def media_demo():
    """Synthetic media-evidence demonstration (offline, mock-labeled)."""
    try:
        return json.loads((SAMPLES_DIR / "media_demo_case.json")
                          .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return JSONResponse({"error": "demo unavailable"}, status_code=404)


@app.get("/api/media-classify")
def media_classify(url: str = ""):
    """Classify a media URL WITHOUT fetching it (adapter status only)."""
    net = keel_policy.policy_summary()["network_allowed"]
    return adapters.classify(url, network_allowed=net)


@app.post("/media", response_class=HTMLResponse)
def media_run(request: Request,
              media_url: str = Form(""),
              transcript: str = Form(""),
              captions: str = Form(""),
              metadata: str = Form(""),
              source_account: str = Form(""),
              claimed_date: str = Form(""),
              claimed_place: str = Form(""),
              claimed_context: str = Form(""),
              analyst_notes: str = Form(""),
              comparison: str = Form(""),
              policy: str = Form("")):
    ctx = media_ctx(request)
    net = ctx["network_allowed"]
    adapter = adapters.classify(media_url, network_allowed=net) \
        if media_url.strip() else None

    # Build artifacts from the textual/contextual components of the media
    # evidence bundle. Nothing is downloaded; the bundle carries references.
    items = []
    ctx_bits = []
    if claimed_date or claimed_place or claimed_context:
        ctx_bits.append(f"Claimed: {claimed_date} {claimed_place} "
                        f"{claimed_context}".strip())
    if source_account:
        ctx_bits.append(f"Source account: {source_account}")
    if media_url:
        ctx_bits.append(f"Source URL (not fetched): {media_url} "
                        f"[{adapter['status_label'] if adapter else 'n/a'}]")
    context_line = " | ".join(ctx_bits)
    if transcript.strip():
        items.append({"label": "Transcript", "evidence_type": "transcript",
                      "event_category": "media_transcript",
                      "text": (context_line + "\n\nTRANSCRIPT:\n" +
                               transcript).strip(),
                      "source": "manual"})
    if captions.strip():
        items.append({"label": "Captions", "evidence_type": "captions",
                      "text": captions.strip(), "source": "manual"})
    if metadata.strip():
        items.append({"label": "Metadata sidecar",
                      "evidence_type": "media_metadata",
                      "text": metadata.strip(), "source": "manual"})
    if comparison.strip():
        items.append({"label": "Comparison / reference",
                      "evidence_type": "freeform_text",
                      "text": comparison.strip(), "source": "manual"})
    if analyst_notes.strip() or (context_line and not items):
        items.append({"label": "Context & analyst notes",
                      "evidence_type": "freeform_text",
                      "text": (context_line + "\n\n" + analyst_notes).strip()
                      or context_line or "media evidence bundle",
                      "source": "manual"})
    if not items:
        ctx["form_error"] = ("Add at least one media evidence component "
                             "(transcript, captions, metadata, or context).")
        ctx["adapter"] = adapter
        return render("media.html", ctx)

    policy_ok, reason = keel_policy.validate_policy(policy)
    if not policy_ok:
        ctx["form_error"] = "Keel policy lock: " + reason
        return render("media.html", ctx)
    locked_policy = keel_policy.enforced_policy(policy)

    report = core_bridge.run_fused(
        items, "smb", lambda p: oc.generate(p, policy=locked_policy),
        workflow="media_authenticity")

    # media assessment lives in the first artifact analysis' raw output
    media_report = None
    for a in report.artifact_analyses:
        parsed = analysis.parse_result(a.raw_response,
                                       evidence_type="media_bundle")
        if parsed.get("media_panel"):
            media_report = parsed
            break

    store_report(report, submission={
        "items": items, "profile_mode": "smb",
        "workflow": "media_authenticity",
        "model": config.DEFAULT_MODEL, "policy": locked_policy})
    pol = keel_policy.policy_summary()
    ctx["page"] = "report"
    ctx["fused"] = report
    ctx["fused_dict"] = report.to_dict()
    ctx["media_report"] = media_report
    ctx["adapter"] = adapter
    ctx["bundle_summary"] = {
        "media_url": media_url, "source_account": source_account,
        "claimed_date": claimed_date, "claimed_place": claimed_place,
        "components": [i["label"] for i in items]}
    ctx["can_save"] = store_ok()
    ctx["investigations"] = STORE.list_projects() if store_ok() else []
    ctx["meta"] = {
        "profile_mode": "Media Evidence", "analysis_mode": "Local Only",
        "analysis_mode_executed": True, "executed_mode": "Local Only",
        "policy": locked_policy, "policy_source": pol["source"],
        "policy_version": pol["version"], "data_boundary": pol["data_boundary"],
        "tools_allowed": pol["tools_allowed"], "tools_denied": pol["tools_denied"],
        "evidence_role": pol["evidence_role"], "model": config.DEFAULT_MODEL,
        "provider": "ollama_local", "node": config.NODE_NAME,
        "core_source": core_bridge.CORE_SOURCE,
        "artifact_count": len(report.artifact_analyses),
    }
    return render("media_report.html", ctx)


@app.post("/media/upload", response_class=HTMLResponse)
async def media_upload(request: Request,
                       media_file: UploadFile = File(...),
                       investigation_id: str = Form(""),
                       new_investigation: str = Form(""),
                       source_account: str = Form(""),
                       claimed_date: str = Form(""),
                       claimed_place: str = Form(""),
                       claimed_context: str = Form(""),
                       escalate_multimodal: str = Form(""),
                       vertex_model: str = Form(""),
                       policy: str = Form("")):
    """Deepfake Defender local media pipeline (v0.3.0): file upload ->
    evidence extraction -> graded assessment -> Canonical Case, entirely
    on this node. Synchronous single-user job; temp media always
    deleted."""
    ctx = media_ctx(request)
    if not core_bridge.HAS_MEDIA_INGEST:
        ctx["form_error"] = ("Media pipeline unavailable: the vendored "
                             "core predates 0.5.0.")
        return render("media.html", ctx)
    policy_ok, reason = keel_policy.validate_policy(policy)
    if not policy_ok:
        ctx["form_error"] = "Keel policy lock: " + reason
        return render("media.html", ctx)
    locked_policy = keel_policy.enforced_policy(policy)

    orig_name = os.path.basename(media_file.filename or "upload")[:120]
    fd, tmp_path = tempfile.mkstemp(prefix="stnode-upload-")
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await media_file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        ctx["form_error"] = "Upload could not be written to local disk."
        return render("media.html", ctx)

    declared = {"filename": orig_name, "source_account": source_account,
                "claimed_date": claimed_date,
                "claimed_place": claimed_place,
                "claimed_context": claimed_context}
    case_ctx_bits = [f"{k.replace('_', ' ')}: {v}" for k, v in
                     declared.items() if v and k != "filename"]
    result = media_runner.run_media_job(
        tmp_path, orig_name,
        generate_fn=lambda p: oc.generate(p, policy=locked_policy),
        declared=declared,
        case_context="; ".join(case_ctx_bits),
        escalate=(escalate_multimodal == "1"),
        selected_model=vertex_model.strip() or None)

    return _finish_media_job(
        ctx, result, orig_name, investigation_id, new_investigation,
        locked_policy, source_kind="upload",
        bundle={"media_url": "", "source_account": source_account,
                "claimed_date": claimed_date,
                "claimed_place": claimed_place,
                "components": [f"Uploaded file: {orig_name}"]})


def _finish_media_job(ctx, result, media_label, investigation_id,
                      new_investigation, locked_policy, *,
                      source_kind="upload", bundle=None):
    """Shared tail of every local media job: persist (analysis +
    Canonical Case) when an investigation was chosen, cache the report
    for the session save panel, and render the media report."""
    saved = {"pid": None, "case_id": None, "analysis_id": None,
             "error": None}
    report = result.get("report")
    if result.get("ok") and store_ok() and \
            (investigation_id or new_investigation.strip()):
        try:
            pid = STORE.create_project(new_investigation) \
                if new_investigation.strip() else investigation_id
            if STORE.get_project(pid):
                event_id = STORE.create_event(
                    pid, f"Media analysis · {media_label}",
                    summary="Deepfake Defender local media pipeline "
                            f"({source_kind})",
                    time_status="observed")
                digest_text = core_bridge.media_package.evidence_digest(
                    result["package"])
                aid = STORE.add_artifact(
                    pid, event_id, label=media_label, text=digest_text,
                    evidence_type="media_evidence_package",
                    workflow="media_authenticity", source=source_kind,
                    media={"family": result.get("family"),
                           "extraction": result.get("extraction"),
                           "retrieval": result.get("retrieval_metadata")})
                xid = STORE.save_analysis(
                    pid, kind="fused", report=report.to_dict(),
                    artifact_ids=[aid], event_id=event_id,
                    workflow="media_authenticity",
                    model=config.DEFAULT_MODEL, policy=locked_policy,
                    ledger_event_ids=[a.event_id for a in
                                      report.artifact_analyses
                                      if a.event_id])
                case = canonical_case.build_case(
                    package=result["package"],
                    assessment=result["assessment"],
                    report_id=report.report_id, media_label=media_label,
                    family=result.get("family") or "",
                    router_meta=result.get("router_meta"),
                    node_version="0.3.4")
                cid = STORE.save_canonical_case(
                    pid, case=case, package=result["package"],
                    assessment=result["assessment"], analysis_id=xid,
                    event_id=event_id)
                saved.update({"pid": pid, "case_id": cid,
                              "analysis_id": xid})
        except store_mod.StoreError as e:
            saved["error"] = str(e)

    if report is not None and result.get("ok"):
        try:
            digest_text = core_bridge.media_package.evidence_digest(
                result["package"])
        except Exception:
            digest_text = ""
        store_report(report, submission={
            "items": [{"label": media_label, "text": digest_text,
                       "evidence_type": "media_evidence_package",
                       "source": source_kind}],
            "profile_mode": "smb", "workflow": "media_authenticity",
            "model": config.DEFAULT_MODEL, "policy": locked_policy})

    # Mode display: the header must never present cloud reasoning as
    # local. Escalation flips the executed mode to Governed Hybrid.
    rm = result.get("router_meta") or {}
    ctx["router_meta"] = rm
    executed_mode, cloud_escalation, cloud_used, provider_display = _mode_strings(rm)

    pol = keel_policy.policy_summary()
    ctx["page"] = "report"
    ctx["media_job"] = result
    ctx["assessment"] = result.get("assessment")
    ctx["stages"] = result.get("stages") or []
    ctx["package"] = result.get("package")
    ctx["saved"] = saved
    ctx["fused"] = report
    ctx["fused_dict"] = report.to_dict() if report else None
    ctx["job_error"] = result.get("error")
    ctx["retrieval_category"] = result.get("retrieval_category")
    ctx["confidence_effect"] = result.get("confidence_effect")
    ctx["multimodal_unavailable"] = bool(rm.get("multimodal_unavailable"))
    ctx["attempted_models"] = rm.get("attempted_models") or []
    ctx["provider_error"] = rm.get("provider_error")
    ctx["provider_status"] = rm.get("provider_status")
    # cache the completed job (serializable) so the report's export
    # actions (JSON / STIX / Report) can be served after render
    if report is not None:
        _cache_media_job(report.report_id, {
            "report_id": report.report_id,
            "media_label": media_label,
            "executed_mode": executed_mode,
            "cloud_used": cloud_used,
            "report": report.to_dict() if report else {},
            "assessment": result.get("assessment"),
            "package": result.get("package"),
            "provenance": result.get("analysis_provenance"),
            "router_meta": rm,
            "saved": saved,
            "case": (canonical_case.build_case(
                package=result["package"], assessment=result["assessment"],
                report_id=report.report_id, media_label=media_label,
                family=result.get("family") or "",
                router_meta=rm, node_version="0.3.4")
                if result.get("ok") and result.get("package") else None),
        })
    ctx["report_token"] = report.report_id if report else ""
    ctx["bundle_summary"] = bundle or {
        "media_url": "", "source_account": "", "claimed_date": "",
        "claimed_place": "", "components": [media_label]}
    ctx["meta"] = {
        "profile_mode": "Media Evidence", "analysis_mode": "Local Only",
        "analysis_mode_executed": True, "executed_mode": executed_mode,
        "cloud_escalation": cloud_escalation, "cloud_used": cloud_used,
        "policy": locked_policy, "policy_source": pol["source"],
        "policy_version": pol["version"],
        "data_boundary": pol["data_boundary"],
        "tools_allowed": pol["tools_allowed"],
        "tools_denied": pol["tools_denied"],
        "evidence_role": pol["evidence_role"],
        "model": config.DEFAULT_MODEL, "provider": provider_display,
        "node": config.NODE_NAME, "core_source": core_bridge.CORE_SOURCE,
        "artifact_count": len(report.artifact_analyses) if report else 0,
    }
    return render("media_report.html", ctx)


@app.post("/media/retrieve", response_class=HTMLResponse)
def media_retrieve(request: Request,
                   media_url: str = Form(...),
                   investigation_id: str = Form(""),
                   new_investigation: str = Form(""),
                   source_account: str = Form(""),
                   claimed_date: str = Form(""),
                   claimed_place: str = Form(""),
                   claimed_context: str = Form(""),
                   escalate_multimodal: str = Form(""),
                   vertex_model: str = Form(""),
                   policy: str = Form("")):
    """Governed media retrieval (parity with the public S5 pipeline):
    operator-initiated, adapter-validated, Keel-scoped egress — then the
    identical local pipeline. General network remains disabled."""
    ctx = media_ctx(request)
    if not core_bridge.HAS_MEDIA_INGEST:
        ctx["form_error"] = ("Media pipeline unavailable: the vendored "
                             "core predates 0.5.0.")
        return render("media.html", ctx)
    allowed, reason = keel_policy.media_retrieval_allowed()
    if not allowed:
        ctx["form_error"] = "Keel policy lock: " + reason
        return render("media.html", ctx)
    policy_ok, preason = keel_policy.validate_policy(policy)
    if not policy_ok:
        ctx["form_error"] = "Keel policy lock: " + preason
        return render("media.html", ctx)
    locked_policy = keel_policy.enforced_policy(policy)

    declared = {"submitted_url": media_url.strip()[:300],
                "source_account": source_account,
                "claimed_date": claimed_date,
                "claimed_place": claimed_place,
                "claimed_context": claimed_context}
    case_ctx_bits = [f"{k.replace('_', ' ')}: {v}" for k, v in
                     declared.items() if v and k != "submitted_url"]
    result = media_runner.run_youtube_job(
        media_url.strip(),
        generate_fn=lambda p: oc.generate(p, policy=locked_policy),
        declared=declared, case_context="; ".join(case_ctx_bits),
        escalate=(escalate_multimodal == "1"),
        selected_model=vertex_model.strip() or None)
    label = media_url.strip()[:80]
    md = result.get("retrieval_metadata") or {}
    if md.get("title"):
        label = md["title"][:80]
    return _finish_media_job(
        ctx, result, label, investigation_id, new_investigation,
        locked_policy, source_kind="youtube",
        bundle={"media_url": media_url.strip(),
                "source_account": source_account,
                "claimed_date": claimed_date,
                "claimed_place": claimed_place,
                "components": ["Retrieved media (governed egress)"]})


@app.post("/media/demo", response_class=HTMLResponse)
def media_demo_run(request: Request,
                   investigation_id: str = Form(""),
                   new_investigation: str = Form(""),
                   escalate_multimodal: str = Form(""),
                   vertex_model: str = Form(""),
                   policy: str = Form("")):
    """Guaranteed demo case: ffmpeg-synthesized specimen through the
    complete REAL pipeline — no external file or URL dependency."""
    ctx = media_ctx(request)
    if not core_bridge.HAS_MEDIA_INGEST:
        ctx["form_error"] = ("Media pipeline unavailable: the vendored "
                             "core predates 0.5.0.")
        return render("media.html", ctx)
    policy_ok, reason = keel_policy.validate_policy(policy)
    if not policy_ok:
        ctx["form_error"] = "Keel policy lock: " + reason
        return render("media.html", ctx)
    locked_policy = keel_policy.enforced_policy(policy)
    result = media_runner.run_demo_job(
        generate_fn=lambda p: oc.generate(p, policy=locked_policy),
        escalate=(escalate_multimodal == "1"),
        selected_model=vertex_model.strip() or None)
    return _finish_media_job(
        ctx, result, "swarmtracker-demo-specimen.mp4", investigation_id,
        new_investigation, locked_policy, source_kind="upload",
        bundle={"media_url": "", "source_account": "",
                "claimed_date": "", "claimed_place": "",
                "components": ["Synthesized demonstration specimen "
                               "(ffmpeg, local)"]})


def _stix_bundle(job):
    """Minimal, structurally-valid STIX 2.1 bundle for a media case:
    an identity (the analyzing node), an artifact SCO carrying the
    media's SHA-256, and a note SDO carrying the graded conclusion +
    escalation provenance and referencing the artifact."""
    import uuid as _uuid
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    case = job.get("case") or {}
    a = job.get("assessment") or {}
    rm = job.get("router_meta") or {}
    sha = ((case.get("media") or {}).get("sha256")
           or _sha_of_job(job) or ("0" * 64))
    ident_id = "identity--" + str(_uuid.uuid4())
    art_id = "artifact--" + str(_uuid.uuid4())
    note_id = "note--" + str(_uuid.uuid4())
    esc = case.get("escalation") or {}
    note_body = (
        f"SwarmTracker Node media authenticity assessment. "
        f"Conclusion: {a.get('assessment_conclusion', 'n/a')}. "
        f"Executed mode: {job.get('executed_mode')}. "
        f"Cloud escalation: {esc.get('state', 'not_authorized')}"
        + (f" (provider {esc.get('provider')}"
           + (f"/{esc.get('vertex_project')}/{esc.get('region')}"
              if esc.get('vertex_project') else "")
           + f", model {esc.get('model_used') or 'n/a'}, attempted "
           f"{', '.join(esc.get('attempted_models') or []) or 'n/a'}"
           + (f", HTTP {esc.get('provider_status')}"
              if esc.get('provider_status') else "")
           + (f", {esc.get('latency_ms')} ms"
              if esc.get('latency_ms') else "") + ")"
           if esc.get('authorized') else "")
        + ". Source media, evidence package, and canonical case retained "
          "locally.")
    objects = [
        {"type": "identity", "spec_version": "2.1", "id": ident_id,
         "created": now, "modified": now,
         "name": "SwarmTracker Node", "identity_class": "system"},
        {"type": "artifact", "spec_version": "2.1", "id": art_id,
         "mime_type": "application/octet-stream",
         "hashes": {"SHA-256": sha}},
        {"type": "note", "spec_version": "2.1", "id": note_id,
         "created": now, "modified": now,
         "abstract": "Deepfake Defender media authenticity assessment",
         "content": note_body, "created_by_ref": ident_id,
         "object_refs": [art_id]},
    ]
    return {"type": "bundle", "id": "bundle--" + str(_uuid.uuid4()),
            "objects": objects}


def _sha_of_job(job):
    for art in ((job.get("package") or {}).get("artifacts") or []):
        if art.get("role") == "source" and art.get("sha256"):
            return art["sha256"]
    return None


def _execution_markdown(rm):
    ex = governed_analysis.execution_record(rm)
    return ["## Cloud execution and audit", "",
            f"- State: {ex['state']}",
            f"- Provider SDK attempted: {ex['provider_attempted']}",
            f"- Blocked before invocation: {ex['blocked']}",
            f"- Authorization audit: {ex['authorization_audit']}",
            f"- Result audit: {ex['result_audit']}",
            f"- Decision audit: {ex['decision_audit']}",
            f"- Authorization event: {ex['authorization_event_id'] or 'none'}",
            f"- Reason: {ex['reason'] or 'none'}",
            "- An SDK invocation does not prove remote delivery or examination.", ""]


def _report_markdown(job):
    a = job.get("assessment") or {}
    rm = job.get("router_meta") or {}
    prov = job.get("provenance") or {}
    lines = [
        f"# SwarmTracker Node — Media Authenticity Report",
        "", f"**Report:** {job.get('report_id')}  ",
        f"**Media:** {job.get('media_label')}  ",
        f"**Executed mode:** {job.get('executed_mode')}  ",
        f"**Cloud used:** {job.get('cloud_used')}  ", "",
        "## Graded conclusion", "",
        f"**{a.get('assessment_conclusion', 'n/a')}**", "",
        a.get("conclusion_rationale", ""), "",
    ]
    lines += _execution_markdown(rm)
    if rm.get("multimodal_unavailable"):
        lines += ["> Multimodal interpretation unavailable — extracted "
                  "artifacts were preserved locally but not examined; no "
                  "authenticity conclusion drawn.",
                  f"> Provider status: HTTP {rm.get('provider_status')}; "
                  f"models attempted: "
                  f"{', '.join(rm.get('attempted_models') or []) or 'n/a'}.",
                  ""]
    if rm.get("escalation_result_write_failed"):
        auth_ref = rm.get("escalation_auth_event_id")
        lines += ["> AUDIT WARNING: The provider call outcome above is "
                  "truthful, but the result audit record could not be "
                  "appended to the ledger (write failed after the call). "
                  "The authorization record exists"
                  + (f" ({auth_ref})" if auth_ref else "")
                  + "; the inference was not retried to repair the audit "
                  "record.", ""]
    lines += ["## Analysis provenance", ""]
    for k, v in prov.items():
        if k != "sent_artifacts":
            lines.append(f"- **{k}:** {v}")
    lines += ["", "## Custody", ""]
    cust = rm.get("custody") or {}
    lines.append(f"- Source file: {cust.get('source_file', 'n/a')}")
    lines.append(f"- Artifacts retained locally: "
                 f"{len(cust.get('retained_local') or [])}")
    lines.append(f"- Submitted to provider SDK (successful response): "
                 f"{len(cust.get('sent_to_provider') or [])}")
    lines.append(f"- Submitted to provider SDK (failed; delivery unconfirmed): "
                 f"{len(cust.get('sent_but_provider_failed') or [])}")
    return "\n".join(lines) + "\n"


def _media_job_or_404(token):
    job = MEDIA_JOBS.get(token)
    if not job:
        return None
    return job


@app.get("/media/report/{token}/json")
def media_report_json(token: str):
    job = _media_job_or_404(token)
    if not job:
        return JSONResponse({"error": "report not found or expired"},
                            status_code=404)
    payload = {k: job.get(k) for k in
               ("report_id", "media_label", "executed_mode", "cloud_used",
                "assessment", "provenance", "router_meta", "case")}
    payload["execution"] = governed_analysis.execution_record(job.get("router_meta"))
    body = json.dumps(payload, indent=2, default=str)
    return PlainTextResponse(body, media_type="application/json",
                             headers={"Content-Disposition":
                                      f'attachment; filename='
                                      f'"media-{token}.json"'})


@app.get("/media/report/{token}/stix")
def media_report_stix(token: str):
    job = _media_job_or_404(token)
    if not job:
        return JSONResponse({"error": "report not found or expired"},
                            status_code=404)
    body = json.dumps(_stix_bundle(job), indent=2, default=str)
    return PlainTextResponse(body, media_type="application/json",
                             headers={"Content-Disposition":
                                      f'attachment; filename='
                                      f'"media-{token}.stix.json"'})


@app.get("/media/report/{token}/report")
def media_report_download(token: str):
    job = _media_job_or_404(token)
    if not job:
        return JSONResponse({"error": "report not found or expired"},
                            status_code=404)
    return PlainTextResponse(
        _report_markdown(job), media_type="text/markdown",
        headers={"Content-Disposition":
                 f'attachment; filename="media-{token}.md"'})


def _stix_from_fused(job):
    """STIX 2.1 bundle from a fused security report: indicator SDOs for
    extracted observables (domains/ips/urls/emails) + an identity + a
    note carrying the graded verdict and (if any) the cloud-assist
    provenance. STIX is meaningful here because the findings ARE
    cyber-threat objects. Never embeds evidence; never implies the cloud
    examined anything it did not."""
    import uuid as _uuid
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    rep = job.get("report") or {}
    rm = job.get("router_meta") or {}
    ident_id = "identity--" + str(_uuid.uuid4())
    objects = [{"type": "identity", "spec_version": "2.1", "id": ident_id,
                "created": now, "modified": now,
                "name": "SwarmTracker Node", "identity_class": "system"}]
    pattern_for = {
        "domain": lambda v: f"[domain-name:value = '{v}']",
        "ip": lambda v: f"[ipv4-addr:value = '{v}']",
        "url": lambda v: f"[url:value = '{v}']",
        "sender": lambda v: f"[email-addr:value = '{v}']",
        "email": lambda v: f"[email-addr:value = '{v}']",
    }
    ind_ids = []
    for e in rep.get("entities", []) or []:
        etype = e.get("type")
        val = e.get("label") or e.get("id")
        if etype in pattern_for and val:
            iid = "indicator--" + str(_uuid.uuid4())
            ind_ids.append(iid)
            objects.append({
                "type": "indicator", "spec_version": "2.1", "id": iid,
                "created": now, "modified": now,
                "name": f"{etype}: {val}",
                "pattern": pattern_for[etype](val),
                "pattern_type": "stix", "valid_from": now,
                "created_by_ref": ident_id})
    esc = "local only"
    if rm.get("audit_write_failed") or \
            rm.get("escalation_auth_write_failed"):
        esc = ("local only — authorization ledger write failed; "
               "no cloud escalation attempted")
    elif rm.get("escalated"):
        esc = ("cloud-assisted: " + str(rm.get("escalation_provider"))
               + "/" + str(rm.get("model_used") or "n/a")
               + (" (FAILED)" if not rm.get("escalation_ok") else "")
               + " — bounded excerpts only; local fusion")
    note = {"type": "note", "spec_version": "2.1",
            "id": "note--" + str(_uuid.uuid4()), "created": now,
            "modified": now, "created_by_ref": ident_id,
            "abstract": "SwarmTracker Node governed investigation",
            "content": (f"Verdict: {rep.get('fused_verdict', 'n/a')}. "
                        f"Overall risk: {rep.get('overall_risk','n/a')} "
                        f"({rep.get('overall_confidence','?')}/100). "
                        f"Analysis: {esc}. Evidence, package, and case "
                        "retained locally."),
            "object_refs": ind_ids or [ident_id]}
    objects.append(note)
    return {"type": "bundle", "id": "bundle--" + str(_uuid.uuid4()),
            "objects": objects}


def _generic_report_markdown(job):
    rep = job.get("report") or {}
    rm = job.get("router_meta") or {}
    ca = job.get("cloud_assist") or {}
    lines = ["# SwarmTracker Node — Governed Investigation Report", "",
             f"**Report:** {job.get('report_id')}  ",
             f"**Workflow:** {job.get('workflow')}  ",
             f"**Executed mode:** {job.get('executed_mode')}  ",
             f"**Cloud used:** {job.get('cloud_used')}  ", "",
             "## Local findings (fusion — always local)", "",
             f"**Verdict:** {rep.get('fused_verdict','n/a')}  ",
             f"**Overall risk:** {rep.get('overall_risk','n/a')} "
             f"({rep.get('overall_confidence','?')}/100)", ""]
    lines += _execution_markdown(rm)
    if governed_analysis.execution_record(rm)["provider_attempted"]:
        lines += ["## Cloud-assisted supplement", ""]
        if rm.get("escalation_ok"):
            lines += [f"Provider: {rm.get('escalation_provider')} · "
                      f"model {rm.get('model_used')} · "
                      f"{rm.get('latency_ms')} ms · bounded excerpts only.",
                      "", "> Cloud-assisted findings are a labeled "
                      "supplement; local fusion above is authoritative "
                      "and fully local.", "",
                      (ca.get("text") or "")[:4000]]
        else:
            lines += [f"> Cloud escalation was AUTHORIZED but FAILED "
                      f"({('HTTP ' + str(rm.get('provider_status'))) if rm.get('provider_status') else 'SDK/transport error'}). No cloud "
                      "findings were produced; local findings preserved. "
                      "No successful examination was confirmed.", ""]
    elif rm.get("audit_write_failed"):
        lines += ["> No cloud assist was attempted: the mandatory "
                  "authorization record could not be written to the "
                  "local ledger, so the escalation failed closed before "
                  "any provider call. The failure is shown in this "
                  "report. No cloud findings were produced.", ""]
    if rm.get("result_write_failed"):
        auth_ref = rm.get("authorization_event_id")
        lines += ["> AUDIT WARNING: The provider call outcome above is "
                  "truthful, but the result audit record could not be "
                  "appended to the ledger (write failed after the call). "
                  "The authorization record exists"
                  + (f" ({auth_ref})" if auth_ref else "")
                  + "; the inference was not retried to repair the audit "
                  "record."]
    lines += ["", "## Custody", "",
              "- Local findings, evidence, and Canonical Case: retained "
              "on this node.",
              "- Sent to provider: " + (
                  "bounded excerpts handed to the provider SDK; delivery is not independently confirmed"
                  if governed_analysis.execution_record(rm)["provider_attempted"]
                  else "nothing (no provider invocation).")]
    if rm.get("audit_write_failed"):
        lines += ["- Authorization ledger write failed before any cloud "
                  "call; nothing was authorized or sent."]
    return "\n".join(lines) + "\n"


def _generic_job_or_404(token):
    return GENERIC_JOBS.get(token)


@app.get("/report/{token}/json")
def generic_report_json(token: str):
    job = _generic_job_or_404(token)
    if not job:
        return JSONResponse({"error": "report not found or expired"},
                            status_code=404)
    payload = {k: job.get(k) for k in
               ("report_id", "workflow", "executed_mode", "cloud_used",
                "report", "cloud_assist", "router_meta", "provenance")}
    payload["execution"] = governed_analysis.execution_record(job.get("router_meta"))
    body = json.dumps(payload, indent=2, default=str)
    return PlainTextResponse(body, media_type="application/json",
                             headers={"Content-Disposition":
                                      f'attachment; filename='
                                      f'"investigation-{token}.json"'})


@app.get("/report/{token}/stix")
def generic_report_stix(token: str):
    job = _generic_job_or_404(token)
    if not job:
        return JSONResponse({"error": "report not found or expired"},
                            status_code=404)
    body = json.dumps(_stix_from_fused(job), indent=2, default=str)
    return PlainTextResponse(body, media_type="application/json",
                             headers={"Content-Disposition":
                                      f'attachment; filename='
                                      f'"investigation-{token}.stix.json"'})


@app.get("/report/{token}/report")
def generic_report_download(token: str):
    job = _generic_job_or_404(token)
    if not job:
        return JSONResponse({"error": "report not found or expired"},
                            status_code=404)
    return PlainTextResponse(
        _generic_report_markdown(job), media_type="text/markdown",
        headers={"Content-Disposition":
                 f'attachment; filename="investigation-{token}.md"'})


def _lookup_job(token):
    """Return a normalized job dict from either cache (media or generic),
    or None. Normalizes to: report_id, title, kind, report(dict),
    router_meta, executed_mode, cloud_used, case, assessment."""
    if token in MEDIA_JOBS:
        j = MEDIA_JOBS[token]
        return {"report_id": j.get("report_id"),
                "title": j.get("media_label") or "Media investigation",
                "kind": "media", "report": (j.get("report") if
                isinstance(j.get("report"), dict) else {}),
                "router_meta": j.get("router_meta") or {},
                "executed_mode": j.get("executed_mode"),
                "cloud_used": j.get("cloud_used"),
                "case": j.get("case") or {},
                "assessment": j.get("assessment") or {},
                "provenance": j.get("provenance") or {}}
    if token in GENERIC_JOBS:
        j = GENERIC_JOBS[token]
        return {"report_id": j.get("report_id"),
                "title": (j.get("workflow") or "Investigation")
                .replace("_", " ").title(),
                "kind": "generic", "report": j.get("report") or {},
                "router_meta": j.get("router_meta") or {},
                "executed_mode": j.get("executed_mode"),
                "cloud_used": j.get("cloud_used"),
                "case": {}, "assessment": {},
                "cloud_assist": j.get("cloud_assist") or {},
                "provenance": j.get("provenance") or {}}
    return None


def _exam_status(rm):
    if not rm or not rm.get("escalated"):
        return "local_only"
    return "examined_by_provider" if rm.get("escalation_ok") \
        else "cloud_failed_not_examined"


def _atlas_bundle(job):
    """Transform a completed case/report into Atlas-compatible records
    (same node/edge schema the shared renderer uses). Deterministic ->
    idempotent: re-exporting the same case yields the same ids. Never
    invents relationships; carries hashes, provenance, and local-vs-cloud
    examination status."""
    rep = job.get("report") or {}
    rm = job.get("router_meta") or {}
    case_id = (job.get("case") or {}).get("case_id") or job["report_id"]
    exam = _exam_status(rm)
    nodes = [{"id": case_id, "type": "canonical_case",
              "label": job.get("title") or case_id,
              "epistemic_status": "deterministic",
              "provenance": {"source_case_id": case_id,
                             "examination": exam,
                             "provider": rm.get("escalation_provider")
                             if rm.get("escalated") else None,
                             "model": rm.get("model_used"),
                             "executed_mode": job.get("executed_mode")}}]
    # reuse the report's already-Atlas-shaped entities/relationships —
    # these are the findings' own entities, not invented ones.
    seen = {case_id}
    for e in rep.get("entities", []) or []:
        eid = e.get("id")
        if not eid or eid in seen:
            continue
        seen.add(eid)
        nodes.append({"id": eid, "type": e.get("type", "artifact"),
                      "label": e.get("label", eid),
                      "epistemic_status": e.get("epistemic_status",
                                                "deterministic"),
                      "provenance": e.get("provenance", {})})
    edges = []
    for r in rep.get("relationships", []) or []:
        if r.get("source") and r.get("target"):
            edges.append({"source": r["source"], "target": r["target"],
                          "relation": r.get("relation", "references"),
                          "epistemic_status": r.get("epistemic_status",
                                                    "deterministic"),
                          "artifact_ids": r.get("artifact_ids", [])})
    # link case -> each top-level entity as "assessed" (supported: the
    # case did assess these observables); no invented cross-links.
    for n in nodes[1:]:
        edges.append({"source": case_id, "target": n["id"],
                      "relation": "assessed",
                      "epistemic_status": "deterministic",
                      "artifact_ids": []})
    return {"schema": "atlas_export/v1", "source_case_id": case_id,
            "examination_status": exam,
            "verdict": rep.get("fused_verdict"),
            "overall_risk": rep.get("overall_risk"),
            "overall_confidence": rep.get("overall_confidence"),
            "provider": rm.get("escalation_provider")
            if rm.get("escalated") else None,
            "model": rm.get("model_used"),
            "nodes": nodes, "edges": edges}


@app.get("/report/{token}/atlas")
def report_atlas_export(token: str, investigation_id: str = ""):
    job = _lookup_job(token)
    if not job:
        return JSONResponse({"ok": False,
                             "error": "report not found or expired"},
                            status_code=404)
    bundle = _atlas_bundle(job)
    merged = None
    # Update an existing investigation's Atlas graph rather than
    # duplicating: the store's upsert (ON CONFLICT) is idempotent.
    if investigation_id and store_ok() and \
            STORE.get_project(investigation_id):
        try:
            STORE.save_analysis(
                investigation_id, kind="fused",
                report=job["report"], artifact_ids=[],
                workflow="atlas_export",
                model=(job["router_meta"].get("model_used") or ""),
                policy="")
            merged = investigation_id
        except store_mod.StoreError as e:
            keel_events.append_event("atlas_export",
                                     {"ok": False, "error": str(e),
                                      "token": token})
            return JSONResponse({"ok": False, "error": str(e)},
                                status_code=500)
    keel_events.append_event("atlas_export",
                             {"ok": True, "token": token,
                              "source_case_id": bundle["source_case_id"],
                              "nodes": len(bundle["nodes"]),
                              "edges": len(bundle["edges"]),
                              "merged_investigation": merged})
    body = json.dumps({"ok": True, "merged_investigation": merged,
                       **bundle}, indent=2, default=str)
    return PlainTextResponse(body, media_type="application/json",
                             headers={"Content-Disposition":
                                      f'attachment; filename='
                                      f'"atlas-{token}.json"'})


def _briefing_fields(job):
    rep = job.get("report") or {}
    rm = job.get("router_meta") or {}
    exam = _exam_status(rm)
    if rm.get("audit_write_failed") or \
            rm.get("escalation_auth_write_failed"):
        cloud_line = ("Local Only — the mandatory authorization record "
                      "could not be written to the local ledger, so no "
                      "cloud escalation was attempted (fail closed). "
                      "No provider examined the evidence.")
    elif not rm.get("escalated"):
        cloud_line = "Local Only — no cloud provider was used."
    elif rm.get("escalation_ok"):
        cloud_line = (f"Cloud-assisted (authorized): "
                      f"{rm.get('escalation_provider')}/"
                      f"{rm.get('model_used')} examined bounded excerpts "
                      f"in {rm.get('latency_ms')} ms. Local fusion "
                      "remained authoritative.")
    else:
        cloud_line = (f"Cloud escalation was AUTHORIZED but FAILED "
                      f"(HTTP {rm.get('provider_status')}); the provider "
                      "did NOT examine the evidence. Findings are local "
                      "only.")
    findings = []
    for a in (rep.get("attribution") or [])[:3]:
        findings.append(f"{a.get('campaign_type','hypothesis')}: "
                        f"{a.get('hypothesis','')} "
                        f"(confidence {a.get('confidence','?')}/100; "
                        f"alternatives: "
                        f"{', '.join(a.get('alternatives') or []) or 'none'})")
    if not findings:
        findings.append(f"Overall: {rep.get('fused_verdict','n/a')}")
    audit_warning = None
    if rm.get("result_write_failed"):
        auth_ref = rm.get("authorization_event_id")
        audit_warning = (
            "AUDIT WARNING: the provider call outcome above is truthful, "
            "but the result audit record could not be appended to the "
            "ledger (write failed after the call). The authorization "
            "record exists"
            + (f" ({auth_ref})" if auth_ref else "")
            + "; the inference was not retried to repair the audit record.")
    return {
        "title": job.get("title"), "case_id": job.get("report_id"),
        "risk": rep.get("overall_risk", "n/a"),
        "confidence": rep.get("overall_confidence", "?"),
        "verdict": rep.get("fused_verdict", "n/a"),
        "findings": findings,
        "limitations": rep.get("limitations", []) or
        ["Bounded analysis; corroborate out-of-band before action."],
        "cloud_line": cloud_line, "exam": exam,
        "audit_warning": audit_warning,
        "executed_mode": job.get("executed_mode"),
        "recommendations": rep.get("recommendations", []) or
        ["Verify key claims out-of-band.", "Preserve evidence + hashes."],
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _briefing_markdown(job):
    f = _briefing_fields(job)
    L = [f"# Executive Briefing — {f['title']}", "",
         f"**Case ID:** {f['case_id']}  ",
         f"**Generated:** {f['generated']}  ",
         f"**Executed mode:** {f['executed_mode']}", "",
         "## Question investigated", "",
         "Assessment of the submitted evidence for risk, manipulation, "
         "or attribution indicators.", "",
         "## Executive summary", "",
         f"**Overall risk: {f['risk']}** (confidence "
         f"{f['confidence']}/100). {f['verdict']}", "",
         "## Highest-priority findings", ""]
    L += [f"- {x}" for x in f["findings"]]
    L += ["", "## Confidence & limitations", ""]
    L += [f"- {x}" for x in f["limitations"]]
    L += ["", "## Analysis disclosure (local vs cloud)", "",
          f["cloud_line"], ""]
    if f.get("audit_warning"):
        L += [f"> {f['audit_warning']}", ""]
    L += ["## Recommended next actions", ""]
    L += [f"- {x}" for x in f["recommendations"]]
    L += ["", "## Integrity", "",
          f"- Report id / integrity ref: `{f['case_id']}`",
          "- Evidence, Evidence Package, and Canonical Case retained "
          "locally.", ""]
    return "\n".join(L) + "\n"


def _briefing_html(job):
    import html as _html
    f = _briefing_fields(job)
    esc = _html.escape
    findings = "".join(f"<li>{esc(x)}</li>" for x in f["findings"])
    lims = "".join(f"<li>{esc(x)}</li>" for x in f["limitations"])
    recs = "".join(f"<li>{esc(x)}</li>" for x in f["recommendations"])
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Executive Briefing — {esc(str(f['title']))}</title>
<style>
@media print{{.noprint{{display:none}}@page{{margin:2cm}}}}
body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
max-width:8.5in;margin:1.5rem auto;padding:0 1rem;color:#111}}
h1{{font-size:20px;margin:0 0 2px}}h2{{font-size:14px;margin:16px 0 4px;
border-bottom:1px solid #ccc;padding-bottom:2px}}
.meta{{color:#555;font-size:12px}}.risk{{font-weight:700}}
.disc{{background:#f6f6f6;border-left:3px solid #888;padding:6px 10px}}
</style></head><body>
<button class="noprint" onclick="window.print()">Print / Save as PDF</button>
<h1>Executive Briefing — {esc(str(f['title']))}</h1>
<div class="meta">Case ID: {esc(str(f['case_id']))} · Generated:
{esc(f['generated'])} · Executed mode: {esc(str(f['executed_mode']))}</div>
<h2>Question investigated</h2>
<p>Assessment of the submitted evidence for risk, manipulation, or
attribution indicators.</p>
<h2>Executive summary</h2>
<p><span class="risk">Overall risk: {esc(str(f['risk']))}</span>
(confidence {esc(str(f['confidence']))}/100). {esc(str(f['verdict']))}</p>
<h2>Highest-priority findings</h2><ul>{findings}</ul>
<h2>Confidence &amp; limitations</h2><ul>{lims}</ul>
<h2>Analysis disclosure (local vs cloud)</h2>
<p class="disc">{esc(f['cloud_line'])}</p>
{'<p class="disc">' + esc(f['audit_warning']) + '</p>' if f.get('audit_warning') else ''}
<h2>Recommended next actions</h2><ul>{recs}</ul>
<h2>Integrity</h2><p>Report id / integrity ref:
<code>{esc(str(f['case_id']))}</code>. Evidence, Evidence Package, and
Canonical Case retained locally.</p>
</body></html>"""


@app.get("/report/{token}/briefing.md")
def report_briefing_md(token: str):
    job = _lookup_job(token)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    keel_events.append_event("briefing_generated",
                             {"token": token, "format": "markdown"})
    return PlainTextResponse(
        _briefing_markdown(job), media_type="text/markdown",
        headers={"Content-Disposition":
                 f'attachment; filename="briefing-{token}.md"'})


@app.get("/report/{token}/briefing.html", response_class=HTMLResponse)
def report_briefing_html(token: str):
    job = _lookup_job(token)
    if not job:
        return HTMLResponse("<p>Report not found or expired.</p>",
                            status_code=404)
    keel_events.append_event("briefing_generated",
                             {"token": token, "format": "html"})
    return HTMLResponse(_briefing_html(job))


@app.get("/atlas", response_class=HTMLResponse)
def atlas_page(request: Request, report: str = "", investigation: str = "",
               event: str = "", evidence_type: str = "",
               epistemic_status: str = ""):
    """Atlas — investigation context graph. Two modes:
    - investigation=<id>: persisted, project-wide aggregation with filters
      and epistemic edge styling (v0.2.0);
    - report=<id> / default: the latest session fused analysis."""
    ctx = base_ctx(request, "atlas")
    ctx["mode"] = "session"
    ctx["filters"] = {"event": event, "evidence_type": evidence_type,
                      "epistemic_status": epistemic_status,
                      "investigation": investigation}
    ctx["investigations"] = STORE.list_projects() if store_ok() else []

    if investigation and store_ok() and STORE.get_project(investigation):
        inv = STORE.get_project(investigation)
        g = STORE.project_graph(
            investigation, event_id=event or None,
            evidence_type=evidence_type or None,
            epistemic_status=epistemic_status or None)
        ctx["mode"] = "investigation"
        ctx["inv"] = inv
        ctx["events"] = STORE.list_events(investigation)
        ctx["evidence_types"] = sorted({a["evidence_type"] for a in
                                        STORE.list_artifacts(investigation)})
        ctx["epistemic_statuses"] = store_mod.EPISTEMIC_STATUSES
        ctx["selected"] = {"report_id": inv["name"],
                           "overall_risk": inv["status"],
                           "artifact_analyses": [],
                           "campaigns": [], "entities": g["entities"],
                           "relationships": g["relationships"]}
        ctx["selected_dict"] = {"report_id": inv["name"], **g, "campaigns": []}
        ctx["reports"] = []
        return render("atlas.html", ctx)

    reports = list(FUSED_REPORTS.values())
    selected = FUSED_REPORTS.get(report) or (reports[-1] if reports else None)
    ctx["reports"] = [
        {"id": r.report_id, "risk": r.overall_risk,
         "n": len(r.artifact_analyses), "campaigns": len(r.campaigns)}
        for r in reversed(reports)]
    ctx["selected"] = selected
    ctx["selected_dict"] = selected.to_dict() if selected else None
    return render("atlas.html", ctx)


@app.get("/agents", response_class=HTMLResponse)
def agents_page(request: Request):
    ctx = base_ctx(request, "agents")
    ctx["participant"] = agents.observe()
    try:
        ctx["monitor_runs"] = monitoring.participants(monitoring.database().recent())
        ctx["monitor_groups"] = {status: [r for r in ctx["monitor_runs"] if r["status"] == status]
                                 for status in ("Active", "Recently disconnected", "Status unknown")}
        ctx["monitor_error"] = False
    except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error, OSError):
        ctx["monitor_runs"] = []
        ctx["monitor_groups"] = {status: [] for status in ("Active", "Recently disconnected", "Status unknown")}
        ctx["monitor_error"] = True
    try:
        ctx["decision_runs"] = decision_routes.runs().recent()
    except Exception:
        ctx["decision_runs"] = [{"integrity_error": True}]
    try:
        ctx["report_actions"] = report_routes.actions().recent()
        ctx["report_action_error"] = False
    except (ValueError, sqlite3.Error, OSError):
        ctx["report_actions"] = []
        ctx["report_action_error"] = True
    try:
        ctx["work_guidance"] = workflows.records(workflows.coordinator().snapshot(), ctx["monitor_runs"])
    except (ValueError, sqlite3.Error, OSError):
        ctx["work_guidance"] = []
    try:
        from .customer_config import CustomerConfig
        from . import store
        customer = CustomerConfig(store.DB_PATH.with_name("customer-config.db"))
        from .agent_observations import configured_agents
        from .local_inference import LocalInference
        ctx["configured_agents"] = configured_agents(customer,LocalInference(store.DB_PATH.with_name("local-inference.db"),customer))
        ctx["configured_agents_error"] = False
    except (ValueError, sqlite3.Error, OSError):
        ctx["configured_agents"] = []
        ctx["configured_agents_error"] = True
    ctx["review_runtimes"] = workflows.coordinator().snapshot()["agent_registrations"]
    ctx["review_capabilities"] = workflows.CAPABILITIES["glm-review"]
    ctx["operator_token"] = decision_routes.TOKEN
    return render("agents.html", ctx)


@app.post("/agents/register-review")
async def register_review_runtime(request: Request):
    from fastapi import HTTPException
    form=await decision_routes.operator_form(request)
    try:
        workflows.coordinator().register_review_runtime(form.get("agent_id",""), endpoint=form.get("endpoint",""),
            model=form.get("model",""),provider=form.get("provider",""),provenance=form.get("provenance",""))
    except (ValueError,sqlite3.IntegrityError) as exc:
        raise HTTPException(409,str(exc)) from None
    return RedirectResponse("/agents",status_code=303)


@app.post("/agents/{agent_id}/check")
async def check_review_runtime(request: Request, agent_id: str):
    from fastapi import HTTPException
    await decision_routes.operator_form(request)
    try: workflows.coordinator().check_review_runtime(agent_id)
    except ValueError as exc: raise HTTPException(409,str(exc)) from None
    return RedirectResponse("/agents",status_code=303)


@app.post("/agents/{agent_id}/assign-review")
async def assign_review_runtime(request: Request, agent_id: str):
    from fastapi import HTTPException
    form=await decision_routes.operator_form(request)
    try:
        workflows.coordinator().assign_first_review(agent_id,workflow_id=form.get("workflow_id",""),
            task_id=form.get("task_id",""),purpose=form.get("purpose",""),
            acceptance=form.get("acceptance",""),expected=form.get("expected",""))
    except (ValueError,sqlite3.IntegrityError) as exc: raise HTTPException(409,str(exc)) from None
    return RedirectResponse("/workflows",status_code=303)


@app.get("/workflows", response_class=HTMLResponse)
def workflows_page(request: Request):
    decision_routes.local_request(request)
    snapshot=workflows.coordinator().snapshot()
    records=workflows.records(snapshot)
    for key in ("session", "agent", "state"):
        value=request.query_params.get(key)
        if value: records=[r for r in records if r[{"session":"session_id","agent":"agent_id","state":"state"}[key]]==value]
    if request.query_params.get("attention")=="1":
        records=[r for r in records if r["state"] not in {"finished","working"}]
    ctx=base_ctx(request,"agents")
    try: participants=monitoring.participants(monitoring.database().recent())
    except (ValueError,sqlite3.Error,OSError): participants=[]
    issues=workflows.attention(snapshot,participants=participants)
    latest_history={x["issue_id"]:x for x in snapshot["attention_history"]}
    ctx.update(work_guidance=records,work_snapshot=snapshot,capabilities=workflows.CAPABILITIES,
               operator_token=decision_routes.TOKEN,attention=issues,
               resolved_attention_candidates=[x for x in latest_history.values() if x["status"]=="acknowledged" and x["issue_id"] not in {i["id"] for i in issues}])
    response=render("workflows.html",ctx)
    response.headers["Cache-Control"]="no-store"
    return response


@app.get("/tasks", response_class=HTMLResponse)
def task_queue_page(request: Request):
    decision_routes.local_request(request)
    snapshot=workflows.coordinator().snapshot()
    controls={x["task_id"]:x for x in snapshot["task_controls"]}
    states={x["id"]:x["state"] for x in snapshot["tasks"]}
    entries=[]
    for task in snapshot["tasks"]:
        ctl=controls.get(task["id"])
        spec=json.loads(task["spec"])
        blockers=[d for d in spec["dependencies"] if states.get(d)!="independently_verified"]
        reason=("Legacy queued request needs adoption and scoped authorization under active policy" if not ctl else
                "Cancelled before dispatch" if task["state"]=="cancelled" else
                "Remote cancellation unsupported; reconcile submission" if ctl["cancellation_requested"] else
                "Paused" if ctl["paused"] else
                "Authorization renewal required" if ctl["authorization_hash"]!=workflows.coordinator()._spec_hash(spec) else
                "Dependencies awaiting independent verification: "+", ".join(blockers) if blockers else
                "Ready within session, worker and budget gates" if task["state"]=="queued" else task["state"])
        entries.append(dict(task=task,spec=spec,control=ctl,reason=reason))
    ctx=base_ctx(request,"agents")
    ctx.update(entries=entries,operator_token=decision_routes.TOKEN,model=workflows.MODEL if hasattr(workflows,"MODEL") else "glm-5.3-flash")
    response=render("task_queue.html",ctx);response.headers["Cache-Control"]="no-store";return response


@app.post("/tasks")
async def create_operator_task(request: Request):
    from fastapi import HTTPException
    form=await decision_routes.operator_form(request)
    if form.get("permissions")!="read-only-review": raise HTTPException(409,"Only fixed read-only review permission is supported")
    from .session_coordinator import MODEL
    spec=dict(id=form.get("id",""),purpose=form.get("purpose",""),
              dependencies=[x.strip() for x in form.get("dependencies","").split(",") if x.strip()],
              authorization="explicit-read-only-glm-review",acceptance_criteria=form.get("acceptance",""),
              expected_outputs=form.get("expected",""),validation_gates=form.get("validation",""),
              model_settings={"model":MODEL,"reasoning_effort":"low","chat_template_kwargs":{"reasoning_effort":"low"},"max_tokens":2048})
    try: workflows.coordinator().create_operator_task(spec,priority=int(form.get("priority","0")))
    except (ValueError,sqlite3.IntegrityError) as exc: raise HTTPException(409,str(exc)) from None
    return RedirectResponse("/tasks",status_code=303)


@app.post("/tasks/{task_id}/control")
async def control_operator_task(request: Request,task_id: str):
    from fastapi import HTTPException
    form=await decision_routes.operator_form(request)
    db=workflows.coordinator()
    try:
        action=form.get("action")
        if action=="authorize": db.authorize_operator_task(task_id,form.get("evidence",""))
        elif action=="adopt": db.adopt_queued_task(task_id)
        elif action=="cancel": db.cancel_operator_task(task_id)
        elif action in {"pause","resume","priority"}:
            task=next((x for x in db.snapshot()["tasks"] if x["id"]==task_id),None)
            if not task: raise ValueError("Unknown task")
            db.edit_operator_task(task_id,json.loads(task["spec"]),pause=True if action=="pause" else False if action=="resume" else None,
                                  priority=int(form.get("priority","0")) if action=="priority" else None)
        elif action=="edit":
            task=next((x for x in db.snapshot()["tasks"] if x["id"]==task_id),None)
            if not task: raise ValueError("Unknown task")
            spec=json.loads(task["spec"])
            for key,field in (("purpose","purpose"),("acceptance_criteria","acceptance"),("expected_outputs","expected"),("validation_gates","validation")):
                if field in form: spec[key]=form[field]
            if "dependencies" in form: spec["dependencies"]=[x.strip() for x in form["dependencies"].split(",") if x.strip()]
            db.edit_operator_task(task_id,spec)
        else: raise ValueError("Unknown queue control")
    except (ValueError,sqlite3.IntegrityError) as exc: raise HTTPException(409,str(exc)) from None
    return RedirectResponse("/tasks",status_code=303)


@app.get("/review-policy",response_class=HTMLResponse)
def review_policy_page(request: Request):
    decision_routes.local_request(request)
    snap=workflows.coordinator().snapshot()
    ctx=base_ctx(request,"governance")
    ctx.update(versions=snap["review_policy_versions"],active=snap["review_policy_active"],
               decisions=snap["review_policy_decisions"],operator_token=decision_routes.TOKEN,
               ceiling=snap["control"]["ceiling"] if snap["control"] else None)
    response=render("review_policy.html",ctx);response.headers["Cache-Control"]="no-store";return response


@app.post("/review-policy")
async def activate_review_policy(request: Request):
    from fastapi import HTTPException
    form=await decision_routes.operator_form(request)
    try:
        workflows.coordinator().activate_review_policy({"permitted_actions":["glm-review"] if form.get("allow_review")=="yes" else [],
            "protected_targets":["filesystem","shell"],"require_approval":form.get("require_approval")=="yes",
            "spending_limit":float(form.get("spending_limit","nan"))})
    except ValueError as exc: raise HTTPException(409,str(exc)) from None
    return RedirectResponse("/review-policy",status_code=303)


@app.get("/notifications",response_class=HTMLResponse)
def notifications_page(request: Request):
    decision_routes.local_request(request)
    snap=workflows.coordinator().snapshot()
    ctx=base_ctx(request,"agents")
    ctx.update(issues=workflows.attention(snap),operator_token=decision_routes.TOKEN,
               history=snap["attention_history"])
    response=render("notifications.html",ctx);response.headers["Cache-Control"]="no-store";return response


def _demo_stores():
    root=product_demo.profile_root()
    from . import decision_runs,report_actions
    decision_routes.RUNS=decision_runs.RunStore(root/"decisions.db")
    report_routes.STORE=report_actions.Store(root/"reports.db",root/"output")
    return decision_routes.RUNS,report_routes.STORE


@app.get("/demo",response_class=HTMLResponse)
def demo_page(request: Request):
    decision_routes.local_request(request)
    try:
        decision_store,report_store=_demo_stores()
        snap=workflows.coordinator().snapshot()
        items=[x for x in snap["work_items"] if x["id"].startswith("demo") and json.loads(x["spec"])["session_id"]=="disposable-demo"]
        error=None
    except ValueError as exc:
        items=[];error=str(exc)
    ctx=base_ctx(request,"agents");ctx.update(demo_items=items,demo_error=error,operator_token=decision_routes.TOKEN)
    response=render("product_demo.html",ctx);response.headers["Cache-Control"]="no-store";return response


@app.post("/demo/new")
async def new_demo(request: Request):
    from fastapi import HTTPException
    await decision_routes.operator_form(request)
    try:
        decisions,reports=_demo_stores()
        item=product_demo.setup(workflows.coordinator(),decisions,reports)
    except (ValueError,sqlite3.Error,OSError) as exc: raise HTTPException(409,str(exc)) from None
    return RedirectResponse("/workflows/"+item["workflow"],status_code=303)


@app.post("/demo/{work_id}/finish")
async def finish_demo(request: Request,work_id: str):
    from fastapi import HTTPException
    await decision_routes.operator_form(request)
    try:
        decisions,reports=_demo_stores()
        product_demo.finish(workflows.coordinator(),work_id,decisions,reports)
    except (ValueError,sqlite3.Error,OSError) as exc: raise HTTPException(409,str(exc)) from None
    return RedirectResponse("/demo",status_code=303)


@app.get("/demo/{work_id}/export/{part}")
def export_demo(request: Request,work_id: str,part: str):
    from fastapi import HTTPException
    decision_routes.local_request(request)
    if not work_id.startswith("demo") or not work_id.isalnum() or part not in {"history","checkpoint"}:
        raise HTTPException(404,"Unknown demo export")
    try:
        root=product_demo.profile_root()
        path=root/(work_id+(".json" if part=="history" else ".sha256"))
        if not path.is_file(): raise HTTPException(404,"Demo export not ready")
        return FileResponse(path,filename=path.name,headers={"Cache-Control":"no-store"})
    except ValueError as exc: raise HTTPException(409,str(exc)) from None


@app.get("/workflows/{work_id}", response_class=HTMLResponse)
def workflow_detail(request: Request, work_id: str):
    from fastapi import HTTPException
    decision_routes.local_request(request)
    snapshot=workflows.coordinator().snapshot()
    work=next((w for w in snapshot["work_items"] if w["id"]==work_id),None)
    if not work: raise HTTPException(404,"Workflow not found")
    links=[x for x in snapshot["workflow_links"] if x["workflow_id"]==work_id]
    demo_review=None
    if work_id.startswith("demo") and os.environ.get("NODE_UI_DEMO_PROFILE")=="1":
        try:
            review_path=product_demo.profile_root()/(work_id+".review.json")
            if review_path.is_file():demo_review=json.loads(review_path.read_text())
        except (ValueError,OSError):pass
    decisions=[]
    report_cases=[]
    for link in links:
        if link["kind"]=="decision":
            try:
                run=decision_routes.runs().get(link["target_id"])
                run["policy_sha256"]=hashlib.sha256(json.dumps(run["manifest"]["policy"],sort_keys=True,separators=(",",":")).encode()).hexdigest()
                decisions.append(run)
            except (decision_routes.decision_runs.NotFound,ValueError):
                decisions.append({"id":link["target_id"],"integrity_error":True})
        elif link["kind"]=="report_action":
            try:
                action=report_routes.actions().get(link["target_id"])
                observed=report_routes.actions().observed_file()
                action["output_observation"]=observed
                action["output_matches"]=bool(observed["present"] and observed["sha256"]==action["manifest"]["action"]["sha256"])
                action["policy_sha256"]=hashlib.sha256(json.dumps(action["manifest"]["policy"],sort_keys=True,separators=(",",":")).encode()).hexdigest()
                report_cases.append(action)
            except (KeyError,ValueError,OSError,sqlite3.Error):
                report_cases.append({"id":link["target_id"],"integrity_error":True})
    ctx=base_ctx(request,"agents")
    ctx.update(item=workflows.guidance(work),links=links,decisions=decisions,report_cases=report_cases,demo_review=demo_review,
               requests=[t for t in snapshot["tasks"] if any(s["workflow_id"]==work_id and s["task_id"]==t["id"] for s in snapshot["work_sources"]+snapshot["continuations"])],
               authorizations=snapshot["continuation_authorizations"],operator_token=decision_routes.TOKEN)
    response=render("workflow_detail.html",ctx)
    response.headers["Cache-Control"]="no-store"
    return response


@app.post("/workflows/{work_id}/continue")
async def continue_workflow(request: Request, work_id: str):
    from fastapi import HTTPException
    form=await decision_routes.operator_form(request)
    try:
        task_id=workflows.coordinator().continue_work(work_id,form.get("source_task",""),approval=form.get("approved")=="yes")
    except (ValueError, KeyError) as exc:
        raise HTTPException(409,str(exc)) from None
    return RedirectResponse("/workflows?continuation="+task_id,status_code=303)


@app.post("/workflows/{work_id}/followup")
async def configure_workflow_followup(request: Request, work_id: str):
    from fastapi import HTTPException
    form=await decision_routes.operator_form(request)
    try:
        mode=form.get("mode","")
        workflows.coordinator().configure_followup(work_id,mode=mode,
            grace=int(form.get("grace","300")),cooldown=int(form.get("cooldown","900")),
            limit_count=int(form.get("limit_count","2")))
    except (ValueError,KeyError) as exc:
        raise HTTPException(409,str(exc)) from None
    return RedirectResponse("/workflows?agent=glm-review",status_code=303)


@app.post("/workflows/attention/{issue_id:path}")
async def acknowledge_attention(request: Request, issue_id: str):
    from fastapi import HTTPException
    form=await decision_routes.operator_form(request)
    db=workflows.coordinator()
    try:
        active={x["id"] for x in workflows.attention(db.snapshot())}
        db.record_attention(issue_id,form.get("status",""),form.get("detail",""),active)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from None
    return RedirectResponse("/workflows?attention=1",status_code=303)


@app.get("/governance", response_class=HTMLResponse)
def governance_page(request: Request):
    ctx = base_ctx(request, "governance")
    ctx["gov"] = governance.assess()
    try:
        ctx["decision_runs"] = decision_routes.runs().recent()
        rid = request.query_params.get("run")
        ctx["run"] = decision_routes.runs().get(rid) if rid else None
    except Exception:
        ctx["decision_runs"] = [{"integrity_error": True}]
        ctx["run"] = None
    try:
        ctx["report_actions"] = report_routes.actions().recent()
        ctx["report_action_error"] = False
    except (ValueError, sqlite3.Error, OSError):
        ctx["report_actions"] = []
        ctx["report_action_error"] = True
    ctx["keel"] = keel_policy.policy_summary()
    ctx["selftest"] = keel_policy.self_test()
    ctx["media_ledger_count"] = keel_events.count_events()
    return render("governance.html", ctx)


@app.get("/governance/self-test")
def governance_self_test():
    """Node UI-side Keel policy self-test. Local checks only: no cloud,
    no network, no runtime calls."""
    return keel_policy.self_test()


@app.get("/ledger", response_class=HTMLResponse)
def ledger_page(request: Request):
    ctx = base_ctx(request, "ledger")
    ctx["events"] = ledger.read_events(limit=50)
    ctx["ledger_count"] = ledger.count_events()
    ctx["ledger_exists"] = config.LEDGER_PATH.exists()
    return render("ledger.html", ctx)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    ctx = base_ctx(request, "settings")
    ctx["status"] = node_status()
    try:
        ctx["report_actions"] = report_routes.actions().recent()
    except (ValueError, sqlite3.Error, OSError):
        ctx["report_actions"] = []
    ctx["keel"] = keel_policy.policy_summary()
    return render("settings.html", ctx)


@app.get("/healthz")
def ui_healthz():
    st = node_status()
    return {
        "status": "ok",
        "app": "swarmtracker_node_ui",
        "version": "0.5.0",
        "persistence": "sqlite" if STORE is not None else "unavailable",
        "verity_core": core_bridge.CORE_SOURCE,
        "keel_policy_version": keel_policy.policy_summary()["version"],
        "keel_policy_lock": "active",
        "mode": config.MODE,
        "keel_policy": config.KEEL_POLICY,
        "cloud_escalation": "disabled",
        "runtime_online": st["runtime_online"],
        "model_online": st["model_online"],
        "ledger_events": ledger.count_events(),
        "media_pipeline": core_bridge.HAS_MEDIA_INGEST,
        "media_retrieval": keel_policy.media_retrieval_allowed()[0],
        "multimodal_escalation_policy":
            keel_policy.multimodal_escalation_allowed()[0],
        "multimodal_escalation_ready":
            keel_policy.multimodal_escalation_allowed()[0] and
            providers.provider_config()["ready"],
        "cloud_provider": providers.provider_config()["provider"],
        "vertex_project": providers.provider_config()["project"],
        "vertex_region": providers.provider_config()["region"],
        "cloud_default_model": providers.provider_config()[
            "default_model_id"],
        "media_governance_events": keel_events.count_events(),
    }


# Governed decision routes share the existing app and successful report cache.
decision_routes.install(app, render, base_ctx, GENERIC_JOBS)

# Development telemetry is observational and cannot authorize execution.
monitoring.install(app, render, base_ctx)
sessions.install(app, render, base_ctx)
report_routes.install(app, render, base_ctx)

from . import setup_routes
setup_routes.install(app, render, base_ctx)
from . import customer_routes
customer_routes.install(app, render, base_ctx)

from . import company_routes
company_routes.install(app, render, base_ctx)

from . import context_routes
context_routes.install(app, render, base_ctx)

from . import workbench_routes
workbench_routes.install(app, render, base_ctx)

from . import environment_routes
environment_routes.install(app, render, base_ctx)

from . import model_connection_routes
model_connection_routes.install(app, render, base_ctx)
from . import ssh_tunnel_routes
ssh_tunnel_routes.install(app, render, base_ctx)

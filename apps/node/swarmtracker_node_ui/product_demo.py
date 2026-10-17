"""Disposable, explicitly synthetic product demonstration in an isolated profile."""
import hashlib
import json
import os
import uuid
from pathlib import Path

from verity_core.trust import decision
from verity_core.pilot_registry import save_private_new
from . import decision_runs, report_actions


def profile_root():
    root=os.environ.get("NODE_UI_DEMO_ROOT")
    db=os.environ.get("NODE_UI_PRODUCT_DB")
    if os.environ.get("NODE_UI_DEMO_PROFILE")!="1" or not root or not db:
        raise ValueError("Disposable demo profile must be explicitly configured")
    base=Path(root).resolve(); file=Path(db).resolve()
    if file.parent!=base or not base.is_dir():
        raise ValueError("Demo coordinator must live directly in the disposable profile")
    return base


def setup(coordinator, decision_store, report_store):
    root=profile_root()
    if decision_store.path.resolve().parent!=root or report_store.db_path.resolve().parent!=root or report_store.output_dir.resolve().parent!=root:
        raise ValueError("Demo evidence stores must remain in disposable profile")
    workflow="demo"+uuid.uuid4().hex[:12]
    coordinator.enqueue_work(dict(id=workflow,purpose="SYNTHETIC developer, reviewer and verifier demonstration",
        dependencies=[],authorization="explicit-product-development",
        acceptance_criteria="Conflicting review, approval pause, fixed-file output digest and independent export",
        expected_outputs="Synthetic summary.md in disposable profile",validation_gates="Independent file and chain checks",
        agent_id="synthetic-roles",session_id="disposable-demo"))
    conflicting=decision_store.create(decision_runs.synthetic_manifest())
    rejected=decision_store.authorize(conflicting["id"],"rejected","Conflicting synthetic directory evidence",
                                      "synthetic-operator",conflicting["head_hash"])
    pending=decision_store.create(decision_runs.synthetic_manifest())
    coordinator.link_evidence(workflow,"decision",rejected["id"],"SYNTHETIC rejected conflicting review")
    coordinator.link_evidence(workflow,"decision",pending["id"],"SYNTHETIC pending scoped approval")
    content="# Synthetic verified summary\nThe directory evidence conflicts with the request.\n"
    sha=hashlib.sha256(content.encode()).hexdigest()
    manifest=dict(schema=report_actions.SCHEMA,agent_id="synthetic-developer",task_id=workflow,
        model="glm-5.3-flash (role label only; deterministic fixture)",provider="synthetic fixture",
        source_name="synthetic message",source_sha256=hashlib.sha256(b"synthetic source").hexdigest(),
        runtime_event_id=workflow,proposal=content,
        action={"kind":"write_sanitized_summary","target":"summary.md","sha256":sha},policy=report_actions.POLICY)
    proposed=report_store.create(manifest)
    coordinator.link_evidence(workflow,"report_action",proposed["id"],"SYNTHETIC fixed-file proposal; approval pending")
    coordinator.update_work(workflow,"awaiting_approval")
    return {"workflow":workflow,"rejected":rejected["id"],"pending":pending["id"],"proposal":proposed["id"],"expected_sha256":sha}


def attach_review(coordinator,workflow,review):
    """Import one independently checked synthetic GLM review into this profile."""
    root=profile_root()
    if not workflow.startswith("demo") or not workflow.isalnum():raise ValueError("Invalid demo workflow")
    required={"schema","task_id","request_id","model","finish_reason","content","settings","verifier","evidence","usage"}
    if not isinstance(review,dict) or set(review)!=required or review["schema"]!="verity_demo_review/v1" or review["task_id"]!="demoConflictReview" or review["model"]!="glm-5.3-flash" or review["finish_reason"]!="stop":raise ValueError("Unsupported demo review")
    if not isinstance(review["request_id"],str) or len(review["request_id"])!=32 or any(c not in "0123456789abcdef" for c in review["request_id"]):raise ValueError("Invalid request ID")
    if review["settings"]!={"model":"glm-5.3-flash","reasoning_effort":"low","chat_template_kwargs":{"reasoning_effort":"low"},"max_tokens":2048}:raise ValueError("Unapproved model settings")
    if not all(isinstance(review[k],str) and 0<len(review[k])<=4000 for k in ("content","verifier","evidence")) or not isinstance(review["usage"],dict):raise ValueError("Incomplete reviewed output")
    state=next((x["state"] for x in coordinator.snapshot()["work_items"] if x["id"]==workflow),None)
    if state!="awaiting_approval":raise ValueError("Only an unfinished demo accepts a review")
    raw=json.dumps(review,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    if len(raw)>10000:raise ValueError("Review artifact too large")
    path=root/(workflow+".review.json")
    if path.exists():
        if path.is_symlink() or path.read_bytes()!=raw:raise ValueError("Existing review artifact differs; reconcile manually")
    else:save_private_new(path,raw)
    digest=hashlib.sha256(raw).hexdigest()
    links=[x for x in coordinator.snapshot()["workflow_links"] if x["workflow_id"]==workflow and x["kind"]=="session_task"]
    if links and (len(links)!=1 or links[0]["target_id"]!=review["request_id"]):raise ValueError("Different review already linked")
    if not links:coordinator.link_evidence(workflow,"session_task",review["request_id"],
        "Live GLM read-only synthetic review, independently checked; artifact SHA-256 "+digest)
    return path


def finish(coordinator, workflow, decision_store, report_store):
    root=profile_root()
    links=[x for x in coordinator.snapshot()["workflow_links"] if x["workflow_id"]==workflow]
    approved=[decision_store.get(x["target_id"]) for x in links if x["kind"]=="decision"]
    reports=[report_store.get(x["target_id"]) for x in links if x["kind"]=="report_action"]
    if not any(x["decision"]=="rejected" for x in approved) or not any(x["decision"]=="approved" for x in approved) or len(reports)!=1:
        raise ValueError("Conflicting review and scoped approval required")
    report=reports[0]; observed=report_store.observed_file()
    if report["state"]!="written" or not observed["present"] or observed["sha256"]!=report["manifest"]["action"]["sha256"]:
        raise ValueError("Real fixed-file result must match approved digest")
    review_path=root/(workflow+".review.json")
    review=json.loads(review_path.read_text()) if review_path.is_file() else None
    if review and not any(x["kind"]=="session_task" and x["target_id"]==review["request_id"] for x in links):
        raise ValueError("Model request provenance link missing")
    coordinator.approve_work(workflow,"synthetic-operator","Approval in decision and report action records checked")
    coordinator.update_work(workflow,"finished",checkpoint=observed["sha256"],
                            evidence="Independent disposable file digest and reviewed event chains")
    bundle={"schema":"verity-product-demo/v1","workflow":workflow,"roles":{
        "developer":"synthetic deterministic proposal; GLM model name is a label, no developer model request",
        "reviewer":"live GLM review of synthetic evidence, plus deterministic rejection fixture" if review else "synthetic deterministic conflicting review; no model request",
        "verifier":"trusted runner checks actual output and event chains"},
        "decisions":[x["record"] for x in approved],"report_action":report_actions.export(report["record"]),
        "output_sha256":observed["sha256"],"output_bytes":observed["bytes"],
        "model_review":review,"model_review_sha256":hashlib.sha256(review_path.read_bytes()).hexdigest() if review else None}
    encoded=json.dumps(bundle,sort_keys=True,separators=(",",":")).encode()
    export=root/(workflow+".json");anchor=root/(workflow+".sha256")
    export.write_bytes(encoded);anchor.write_text(hashlib.sha256(encoded).hexdigest()+"\n")
    return export,anchor


def verify_export(export,anchor):
    data=Path(export).read_bytes()
    if hashlib.sha256(data).hexdigest()!=Path(anchor).read_text().strip(): return False
    bundle=json.loads(data)
    if bundle.get("schema")!="verity-product-demo/v1" or not report_actions.verify(bundle["report_action"]): return False
    if not all(decision.inspect(x) for x in bundle["decisions"]): return False
    if bundle.get("model_review") is not None:
        review=bundle["model_review"]
        if review.get("finish_reason")!="stop" or not review.get("verifier") or hashlib.sha256(json.dumps(review,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()!=bundle.get("model_review_sha256"):
            return False
    return bundle["output_sha256"]==bundle["report_action"]["record"]["entries"][0]["payload"]["manifest"]["action"]["sha256"]

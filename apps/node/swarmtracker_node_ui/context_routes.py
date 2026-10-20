"""Local profile review and context-bound analysis; no authenticated approval."""
import sqlite3
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from verity_core.organization_context import ContextStore, Conflict
from verity_core.trust import evidence_check
from . import decision_routes, store, customer_routes
from .local_inference import LocalInference


def database():
    return ContextStore(store.DB_PATH.with_name("organization-context.db"))


def runs():
    return LocalInference(store.DB_PATH.with_name("local-inference.db"), customer_routes.database())


def install(app, render, base_ctx):
    @app.get("/contexts")
    def page(request: Request):
        customer_routes.local_peer(request)
        ctx = base_ctx(request, "contexts")
        try:
            profiles = database().profiles()
            by_id = {p["profile_id"]: p["state"] for p in profiles}
            visible = []
            for profile in profiles:
                state = profile["state"]
                versions = list(state["versions"].values())[-5:]
                active = state["active_version"]
                if active is not None and active not in {v["version"] for v in versions}:
                    versions.insert(0, state["versions"][active])
                visible.append(dict(profile_id=profile["profile_id"], generation=state["generation"],
                                    active_version=active, versions=versions))
            ctx["context_profiles"] = visible
            records = [r for r in runs().recent() if r.get("context_analysis")]
            for run in records:
                snap = run["context_analysis"]["capture"]["snapshot"]
                current = by_id.get(snap["profile_id"])
                run["context_status"] = ("unknown" if current is None else "withdrawn" if current["active_version"] is None
                    else "current" if current["generation"] == snap["generation"] else "superseded")
            ctx.update(context_runs=records, operator_token=decision_routes.TOKEN)
        except (sqlite3.Error, OSError, ValueError):
            raise HTTPException(503, "Context or analysis history unavailable; no fallback approval") from None
        response = render("organization_context.html", ctx)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/contexts/propose")
    async def propose(request: Request):
        customer_routes.local_peer(request)
        form = await decision_routes.operator_form(request, max_fields=16)
        fields = {"operator_token", "profile_id", "title", "entry_id", "kind", "status", "subject", "value", "source_ref", "checked_at", "review_before"}
        if not fields <= set(form) or set(form)-fields-{"missing", "conflicting"}:
            raise HTTPException(422, "Exact profile proposal fields required")
        try:
            flags = [name for name in ("missing", "conflicting") if name in form]
            if any(form[name] != "on" for name in flags):
                raise ValueError("Invalid qualification")
            entry = {k: form[k].strip() for k in ("kind", "status", "subject", "value", "source_ref", "checked_at", "review_before")}
            entry.update(id=form["entry_id"].strip(), source_sha256=evidence_check.digest(entry["value"]), qualifications=flags)
            database().propose(form["profile_id"].strip(), dict(title=form["title"].strip(), entries=[entry]), origin="operator")
        except (ValueError, TypeError):
            raise HTTPException(422, "Profile proposal invalid; check the bounded statement, source reference and UTC dates") from None
        except sqlite3.Error:
            raise HTTPException(409, "Profile storage busy; proposal not confirmed") from None
        return RedirectResponse("/contexts", status_code=303)

    @app.post("/contexts/{profile_id}/decision")
    async def decide(request: Request, profile_id: str):
        customer_routes.local_peer(request)
        form = await decision_routes.operator_form(request)
        required = {"operator_token", "action", "version", "sha256", "generation", "parent_digest", "actor", "reason"}
        if set(form) != required:
            raise HTTPException(422, "Exact context decision fields required")
        try:
            db = database();version = int(form["version"])
            if form["action"] == "approve":
                db.approve(profile_id, version, form["sha256"], int(form["generation"]), form["parent_digest"] or None,
                           actor=form["actor"], reason=form["reason"])
            elif form["action"] == "reject":
                db.reject(profile_id, version, form["sha256"], actor=form["actor"], reason=form["reason"])
            else:
                raise ValueError("Unknown context decision")
        except Conflict:
            raise HTTPException(409, "Proposal or parent changed; review the current profile") from None
        except (ValueError, TypeError):
            raise HTTPException(422, "Context decision invalid") from None
        except sqlite3.Error:
            raise HTTPException(409, "Context decision storage busy; decision not confirmed") from None
        return RedirectResponse("/contexts", status_code=303)

    @app.post("/contexts/{profile_id}/withdraw")
    async def withdraw(request: Request, profile_id: str):
        customer_routes.local_peer(request)
        form = await decision_routes.operator_form(request)
        if set(form) != {"operator_token", "generation", "sha256", "actor", "reason"}:
            raise HTTPException(422, "Exact withdrawal fields required")
        try:
            database().withdraw(profile_id, int(form["generation"]), form["sha256"], actor=form["actor"], reason=form["reason"])
        except Conflict:
            raise HTTPException(409, "Current approval changed; withdrawal not confirmed") from None
        except (ValueError, TypeError):
            raise HTTPException(422, "Withdrawal invalid") from None
        except sqlite3.Error:
            raise HTTPException(409, "Withdrawal storage busy; withdrawal not confirmed") from None
        return RedirectResponse("/contexts", status_code=303)

    @app.post("/contexts/analyze")
    async def analyze(request: Request):
        customer_routes.local_peer(request)
        form = await decision_routes.operator_form(request)
        required = {"operator_token", "profile_id", "agent_id", "task_id", "resource_id", "session_id", "prompt"}
        if set(form) != required or not form["profile_id"].strip():
            raise HTTPException(422, "An explicit context profile and exact analysis fields are required")
        try:
            runs().run(agent_id=form["agent_id"], task_id=form["task_id"], resource_id=form["resource_id"],
                session_id=form["session_id"], prompt=form["prompt"], purpose="generate_text",
                context_profile_id=form["profile_id"], context_store=database())
        except Conflict:
            raise HTTPException(409, "No current locally approved profile; analysis was not dispatched") from None
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Task ID already recorded; no duplicate dispatch") from None
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "Analysis refused; check profile size, specimen and configured local agent/resource") from None
        except sqlite3.Error:
            raise HTTPException(409, "Analysis storage busy or unavailable; completion not confirmed") from None
        return RedirectResponse("/contexts", status_code=303)

    @app.get("/contexts/runs/{run_id}/export")
    def export(request: Request, run_id: str):
        customer_routes.local_peer(request)
        try:
            bundle = runs().context_export(run_id)
        except KeyError:
            raise HTTPException(404, "Finished context analysis not found") from None
        except (ValueError, sqlite3.Error):
            raise HTTPException(503, "Context analysis evidence unavailable") from None
        return JSONResponse(bundle, headers={"Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="verity-context-analysis.json"'})

    @app.get("/contexts/runs/{run_id}/checkpoint")
    def checkpoint(request: Request, run_id: str):
        response = export(request, run_id)
        import json
        return JSONResponse(json.loads(response.body)["checkpoint"], headers={"Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="verity-context-analysis-checkpoint.json"'})

"""Local operator pages for real fixed-output report proposals."""
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import decision_routes, report_actions, store

STORE = None


def actions():
    global STORE
    if STORE is None:
        output = Path(os.environ.get("VERITY_REPORT_OUTPUT_DIR",
                                  str(store.DB_PATH.parent / "governed-report-output")))
        STORE = report_actions.Store(store.DB_PATH.with_name("report-actions.db"), output)
    return STORE


def install(app, render, base_ctx):
    router = APIRouter()

    def local_peer(request):
        decision_routes.local_request(request)
        if not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(403, "Loopback peer required for report actions")

    def read(rid):
        try:
            return actions().get(rid)
        except KeyError:
            raise HTTPException(404, "Report proposal not found") from None
        except (ValueError, TypeError, KeyError, sqlite3.Error, OSError):
            raise HTTPException(503, "Report action integrity or storage unavailable") from None

    @router.get("/report-actions")
    def index(request: Request):
        local_peer(request)
        try:
            runs = actions().recent()
        except (ValueError, sqlite3.Error, OSError):
            raise HTTPException(503, "Report action history unavailable") from None
        ctx = base_ctx(request, "governance")
        ctx.update(report_actions=runs)
        return render("report_actions.html", ctx)

    @router.get("/report-actions/{rid}")
    def detail(request: Request, rid: str):
        local_peer(request)
        view = read(rid)
        try:
            observed = actions().observed_file()
        except (ValueError, OSError):
            observed = {"present": False, "error": "Output could not be checked"}
        ctx = base_ctx(request, "governance")
        ctx.update(action=view, observed=observed, operator_token=decision_routes.TOKEN)
        response = render("report_action.html", ctx)
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.post("/report-actions/{rid}/decision")
    async def decide(request: Request, rid: str):
        local_peer(request)
        form = await decision_routes.operator_form(request)
        if set(form) != {"operator_token", "expected_head", "choice", "reason"}:
            raise HTTPException(422, "Unexpected decision parameters")
        read(rid)
        try:
            view = actions().decide(rid, form["choice"], form["reason"],
                                    "local-operator", form["expected_head"])
        except report_actions.Conflict:
            raise HTTPException(409, "Proposal changed; reload") from None
        except ValueError:
            raise HTTPException(422, "Decision rejected") from None
        except (sqlite3.Error, OSError):
            raise HTTPException(503, "Decision could not be recorded") from None
        return RedirectResponse("/report-actions/" + view["id"], status_code=303)

    @router.post("/report-actions/{rid}/revoke")
    async def revoke(request: Request, rid: str):
        local_peer(request)
        form = await decision_routes.operator_form(request)
        if set(form) != {"operator_token", "expected_head", "reason"}:
            raise HTTPException(422, "Unexpected revocation parameters")
        read(rid)
        try:
            view = actions().revoke(rid, form["reason"], "local-operator", form["expected_head"])
        except report_actions.Conflict:
            raise HTTPException(409, "Approval changed or execution began; reload") from None
        except ValueError:
            raise HTTPException(422, "Revocation rejected") from None
        except (sqlite3.Error, OSError):
            raise HTTPException(503, "Revocation could not be recorded") from None
        return RedirectResponse("/report-actions/" + view["id"], status_code=303)

    @router.post("/report-actions/{rid}/execute")
    async def execute(request: Request, rid: str):
        local_peer(request)
        form = await decision_routes.operator_form(request)
        if set(form) != {"operator_token", "expected_head"}:
            raise HTTPException(422, "Unexpected action parameters")
        read(rid)
        try:
            view = actions().execute(rid, "local-operator", form["expected_head"])
        except report_actions.Conflict:
            raise HTTPException(409, "Action changed or was already attempted") from None
        except (ValueError, sqlite3.Error, OSError):
            raise HTTPException(503, "Write outcome uncertain; inspect reserved record and disk") from None
        return RedirectResponse("/report-actions/" + view["id"], status_code=303)

    @router.get("/report-actions/{rid}/export.json")
    def export(request: Request, rid: str):
        local_peer(request)
        return JSONResponse(report_actions.export(read(rid)["record"]),
                            headers={"Cache-Control": "no-store"})

    @router.get("/report-actions/{rid}/checkpoint.json")
    def checkpoint(request: Request, rid: str):
        local_peer(request)
        return JSONResponse(report_actions.checkpoint(read(rid)["record"]),
                            headers={"Cache-Control": "no-store"})

    app.include_router(router)

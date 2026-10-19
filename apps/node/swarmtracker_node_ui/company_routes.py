"""Read-only company research trace; no implied operator approval."""
import sqlite3
import json
from pathlib import Path
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from . import decision_routes, store
from .company_work import CompanyWorkStore


def database():
    return CompanyWorkStore(store.DB_PATH.with_name("company-work.db"))


def local_peer(request):
    decision_routes.local_request(request)
    if not request.client or request.client.host not in ("127.0.0.1","::1","testclient"):
        raise HTTPException(403,"Loopback peer required")



def install(app,render,base_ctx):
    @app.get("/company-work")
    def page(request:Request):
        local_peer(request)
        ctx=base_ctx(request,"company_work")
        ctx["company_work_records"]=database().recent()
        try:
            ctx["evidence_checks"]={w["id"]: database().checks(w["id"]) for w in ctx["company_work_records"]}
        except (ValueError,TypeError,sqlite3.Error):
            raise HTTPException(503,"Evidence integrity or storage unavailable") from None
        ctx["operator_token"]=decision_routes.TOKEN
        response=render("company_work.html",ctx)
        response.headers["Cache-Control"]="no-store"
        return response

    @app.post("/company-work/{work_id}/decision")
    async def decide(request:Request,work_id:str):
        local_peer(request)
        form=await decision_routes.operator_form(request)
        if set(form)!={"operator_token","state","detail"}:
            raise HTTPException(422,"Exact decision fields required")
        try:
            database().decide(work_id,form["state"],form["detail"])
        except KeyError:
            raise HTTPException(404,"Research record not found") from None
        except ValueError:
            raise HTTPException(409,"Decision refused or already recorded") from None
        except sqlite3.Error:
            raise HTTPException(503,"Decision storage unavailable") from None
        return RedirectResponse("/company-work",status_code=303)

    @app.post("/company-work/{work_id}/assignment")
    async def assign(request:Request,work_id:str):
        local_peer(request)
        form=await decision_routes.operator_form(request)
        if set(form)!={"operator_token","task_id","assignee"}:
            raise HTTPException(422,"Exact assignment fields required")
        try:
            database().assign(work_id,form["task_id"],form["assignee"])
        except KeyError:
            raise HTTPException(404,"Research record not found") from None
        except ValueError:
            raise HTTPException(409,"Assignment refused or already recorded") from None
        except sqlite3.Error:
            raise HTTPException(503,"Assignment storage unavailable") from None
        return RedirectResponse("/company-work",status_code=303)

    @app.post("/company-work/{work_id}/commit")
    async def observe_commit(request:Request,work_id:str):
        local_peer(request)
        form=await decision_routes.operator_form(request)
        if set(form)!={"operator_token","commit_sha"}:
            raise HTTPException(422,"Exact commit observation fields required")
        try:
            database().observe_commit(work_id,form["commit_sha"],Path(__file__).resolve().parents[3])
        except KeyError:
            raise HTTPException(404,"Research record not found") from None
        except ValueError:
            raise HTTPException(409,"Commit observation refused or local checkout unavailable") from None
        except sqlite3.Error:
            raise HTTPException(503,"Observation storage unavailable") from None
        return RedirectResponse("/company-work",status_code=303)

    @app.post("/company-work/{work_id}/evidence")
    async def attach_evidence(request:Request,work_id:str):
        local_peer(request)
        form=await decision_routes.operator_form(request)
        if set(form)!={"operator_token","check_json"}:
            raise HTTPException(422,"Exact evidence fields required")
        try:
            database().attach_check(work_id,json.loads(form["check_json"]))
        except KeyError:
            raise HTTPException(404,"Research record not found") from None
        except (ValueError,TypeError,UnicodeError,RecursionError):
            raise HTTPException(422,"Invalid, duplicate, or unlinked evidence check") from None
        except sqlite3.Error:
            raise HTTPException(503,"Evidence storage unavailable") from None
        return RedirectResponse("/company-work",status_code=303)

    @app.get("/company-work/{work_id}/evidence.json")
    def export_evidence(request:Request,work_id:str):
        local_peer(request)
        try:
            bundles=database().checks(work_id)
        except KeyError:
            raise HTTPException(404,"Research record not found") from None
        except (ValueError,TypeError,sqlite3.Error):
            raise HTTPException(503,"Evidence integrity or storage unavailable") from None
        return JSONResponse({"schema":"research_evidence/v0.1","work_id":work_id,"checks":bundles},headers={"Cache-Control":"no-store"})

    @app.post("/company-work/{work_id}/quotation")
    async def check_quotation(request:Request,work_id:str):
        local_peer(request)
        form=await decision_routes.operator_form(request)
        if set(form)!={"operator_token","snapshot"}:
            raise HTTPException(422,"Exact quotation fields required")
        try:
            database().check_quotation(work_id,form["snapshot"])
        except KeyError:
            raise HTTPException(404,"Research record not found") from None
        except (ValueError,TypeError,UnicodeError):
            raise HTTPException(422,"Quotation check refused; snapshot or check allowance invalid") from None
        except sqlite3.Error:
            raise HTTPException(503,"Evidence storage unavailable") from None
        return RedirectResponse("/company-work",status_code=303)

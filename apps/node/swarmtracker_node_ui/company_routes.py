"""Read-only company research trace; no implied operator approval."""
import sqlite3
from pathlib import Path
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from . import decision_routes, store
from .company_work import CompanyWorkStore


def database():
    return CompanyWorkStore(store.DB_PATH.with_name("company-work.db"))


def install(app,render,base_ctx):
    @app.get("/company-work")
    def page(request:Request):
        decision_routes.local_request(request)
        ctx=base_ctx(request,"company_work")
        ctx["company_work_records"]=database().recent()
        ctx["operator_token"]=decision_routes.TOKEN
        response=render("company_work.html",ctx)
        response.headers["Cache-Control"]="no-store"
        return response

    @app.post("/company-work/{work_id}/decision")
    async def decide(request:Request,work_id:str):
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

"""Read-only company research trace; no implied operator approval."""
import sqlite3
import json
from contextlib import nullcontext
from pathlib import Path
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from . import decision_routes, store
from .company_work import CompanyWorkStore


def database():
    return CompanyWorkStore(store.DB_PATH.with_name("company-work.db"))


def local_peer(request):
    decision_routes.local_request(request)
    if not request.client or request.client.host not in ("127.0.0.1","::1","testclient"):
        raise HTTPException(403,"Loopback peer required")



def decision_refusal(request,message):
    if "text/html" in request.headers.get("accept", ""):
        from html import escape
        return HTMLResponse("<main><h1>Decision not recorded</h1><p>"+escape(message)+
            '</p><p><a href="/company-work">Return to research review</a> · <a href="/configuration">Review current settings</a></p></main>',
            status_code=409,headers={"Cache-Control":"no-store"})
    raise HTTPException(409,message)


def install(app,render,base_ctx):
    @app.get("/company-work")
    def page(request:Request):
        local_peer(request)
        ctx=base_ctx(request,"company_work")
        try:
            ctx["company_work_records"]=database().recent()
            ctx["evidence_checks"]={w["id"]: database().checks(w["id"]) for w in ctx["company_work_records"]}
        except (ValueError,TypeError,sqlite3.Error):
            raise HTTPException(503,"Evidence integrity or storage unavailable") from None
        ctx["operator_token"]=decision_routes.TOKEN
        from . import workbench_routes
        from .research_journey import ResearchJourney
        journey=ResearchJourney(store.DB_PATH.with_name("company-work.db"),workbench_routes.runs())
        ctx["research_intakes"]=journey.intakes()
        ctx["research_choices"]=[]
        cfg=workbench_routes.runs().configuration
        import hashlib
        from .customer_config import canonical
        for agent in [x for x in cfg.all() if x['kind']=='agent'][:20]:
            for resource in agent['body']['resources'][:20]:
                try:effective=cfg.effective(agent['id'],resource)
                except ValueError:continue
                ctx["research_choices"].append(dict(configuration=effective,sha256=hashlib.sha256(canonical(effective).encode()).hexdigest()))
                if len(ctx["research_choices"])>=30:break
            if len(ctx["research_choices"])>=30:break
        response=render("company_work.html",ctx)
        response.headers["Cache-Control"]="no-store"
        return response

    @app.post("/company-work/research")
    async def submit_research(request:Request):
        local_peer(request)
        form=await decision_routes.operator_form(request,max_fields=16)
        required={"operator_token","task_id","title","date","text","synthetic","source_url","source_claim","agent_id","resource_id","session_id","purpose","expected_configuration_sha256"}
        if set(form)!=required or form["synthetic"] not in ("true","false"):
            raise HTTPException(422,"Exact article and reviewed request fields required")
        from .research_journey import ResearchJourney
        from . import workbench_routes
        try:
            run=ResearchJourney(store.DB_PATH.with_name("company-work.db"),workbench_routes.runs()).submit(
                **{k:form[k] for k in ("task_id","source_url","source_claim","agent_id","resource_id","session_id","purpose","expected_configuration_sha256")},
                article=dict(title=form["title"],date=form["date"],text=form["text"],synthetic=form["synthetic"]=="true"))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Task already retained; recover the proposal without another inference") from None
        except (ValueError,KeyError,TypeError):
            raise HTTPException(422,"Research intake refused; inspect article and configured request. Retained tasks are never automatically retried.") from None
        except sqlite3.Error:
            raise HTTPException(503,"Research persistence unconfirmed; inspect retained task and recover without resubmitting") from None
        return RedirectResponse("/company-work" if run["state"]=="completed" else "/workbench/requests/"+run["id"],status_code=303)

    @app.post("/company-work/{work_id}/recover")
    async def recover_research(request:Request,work_id:str):
        local_peer(request)
        form=await decision_routes.operator_form(request)
        if set(form)!={"operator_token"}:raise HTTPException(422,"Exact recovery fields required")
        from .research_journey import ResearchJourney
        from . import workbench_routes
        try:ResearchJourney(store.DB_PATH.with_name("company-work.db"),workbench_routes.runs()).recover(work_id)
        except (KeyError,ValueError,sqlite3.Error):raise HTTPException(409,"Recovery not confirmed; inspect retained request. No inference was dispatched.") from None
        return RedirectResponse("/company-work",status_code=303)

    @app.get("/company-work/{work_id}/record/{artifact}.json")
    def research_export(request:Request,work_id:str,artifact:str):
        local_peer(request)
        if artifact not in ("bundle","checkpoint"):raise HTTPException(404,"Unknown research artifact")
        try:bundle=database().receipt(work_id)
        except KeyError:raise HTTPException(404,"Research receipt unavailable") from None
        except (ValueError,sqlite3.Error):raise HTTPException(503,"Research integrity unavailable") from None
        return JSONResponse(bundle if artifact=="bundle" else bundle["checkpoint"],headers={"Cache-Control":"no-store","Content-Disposition":f'attachment; filename="research.{artifact}.json"'})

    @app.post("/company-work/{work_id}/decision")
    async def decide(request:Request,work_id:str):
        local_peer(request)
        form=await decision_routes.operator_form(request)
        if set(form) not in ({"operator_token","state","detail"},{"operator_token","state","detail","expected_binding"}):
            raise HTTPException(422,"Exact decision fields required")
        try:
            work=database().get(work_id)
            from . import customer_routes
            cfg=work.get("configuration")
            guard=(customer_routes.database().dispatch_guard(cfg["agent_id"],cfg["resource_id"],cfg)
                   if cfg and form["state"]=="accepted" else nullcontext())
            with guard:
                database().decide(work_id,form["state"],form["detail"],expected_binding=form.get("expected_binding"))
        except KeyError:
            raise HTTPException(404,"Research record not found") from None
        except ValueError as exc:
            if str(exc)=="Required research receipt missing":
                return decision_refusal(request,"Required research evidence is missing. The decision remains pending; restore the retained evidence before review.")
            return decision_refusal(request,"Decision refused: content or configuration changed, routing paused, or decision already recorded. No new decision was recorded. Rejection remains available for a pending proposal.")
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

    @app.get("/company-work/{work_id}/evidence/{index}/{artifact}.json")
    def export_one_evidence(request:Request,work_id:str,index:int,artifact:str):
        local_peer(request)
        if artifact not in ("bundle","checkpoint"):
            raise HTTPException(404,"Unknown evidence artifact")
        try:
            bundles=database().checks(work_id)
        except KeyError:
            raise HTTPException(404,"Research record not found") from None
        except (ValueError,TypeError,sqlite3.Error):
            raise HTTPException(503,"Evidence integrity or storage unavailable") from None
        if index < 0 or index >= len(bundles):
            raise HTTPException(404,"Evidence check not found")
        payload=bundles[index] if artifact=="bundle" else bundles[index]["checkpoint"]
        return JSONResponse(payload,headers={"Cache-Control":"no-store",
            "Content-Disposition":f'attachment; filename="evidence-{index}.{artifact}.json"'})

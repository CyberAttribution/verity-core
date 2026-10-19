"""Manual customer configuration. A connection check establishes reachability only."""
import os
import sqlite3
import urllib.request
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import decision_routes, store
from .customer_config import CustomerConfig
from .local_inference import LocalInference


def database():
    return CustomerConfig(store.DB_PATH.with_name("customer-config.db"))


def install(app,render,base_ctx):
    @app.get("/configuration")
    def page(request:Request):
        decision_routes.local_request(request)
        db=database();ctx=base_ctx(request,"configuration")
        ctx.update(configurations=db.all(),observations=db.observations(),
                   disabled_connections={item["id"]:db.connection_disabled(item["id"]) for item in db.all() if item["kind"]=="connection"},operator_token=decision_routes.TOKEN,
                   inference_runs=LocalInference(store.DB_PATH.with_name("local-inference.db"),db).recent())
        response=render("customer_configuration.html",ctx)
        response.headers["Cache-Control"]="no-store"
        return response

    @app.post("/configuration")
    async def save(request:Request):
        form=await decision_routes.operator_form(request)
        kind=form.get("kind","");identifier=form.get("id","")
        try:
            if kind=="connection":
                body={k:form.get(k,"").strip() for k in ("provider","endpoint","model","revision","scope")}
                body["credential_ref"]="none"
            elif kind=="agent":
                body=dict(connection_id=form.get("connection_id","").strip(),instructions=form.get("instructions","").strip(),
                          allowed_tools=[x.strip() for x in form.get("allowed_tools","").split(",") if x.strip()],
                          resources=[x.strip() for x in form.get("resources","").split(",") if x.strip()],
                          external_sharing=form.get("external_sharing","none"),fallback_id=form.get("fallback_id","").strip(),
                          daily_request_limit=int(form.get("daily_request_limit","20")))
            elif kind in ("machine","resource"):
                body=dict(description=form.get("description",""),provenance="operator-entered",status="unknown")
            elif kind=="link":
                body=dict(from_kind=form.get("from_kind",""),from_id=form.get("from_id",""),
                          to_kind=form.get("to_kind",""),to_id=form.get("to_id",""),
                          permission=form.get("permission",""),provenance="operator-entered",status="unknown")
            else:raise ValueError("Unsupported configuration")
            database().save(kind,identifier,body)
        except (ValueError,TypeError,sqlite3.Error):
            raise HTTPException(422,"Configuration refused; check fields and referenced versions. Credentials cannot be entered here.") from None
        return RedirectResponse("/configuration",status_code=303)

    @app.post("/configuration/inference")
    async def inference(request:Request):
        decision_routes.local_request(request)
        if not request.client or request.client.host not in ("127.0.0.1","::1","testclient"):
            raise HTTPException(403,"Loopback peer required")
        form=await decision_routes.operator_form(request)
        if set(form)!={"operator_token","agent_id","task_id","resource_id","purpose","prompt","requested_model"}:
            raise HTTPException(422,"Exact inference fields required")
        try:
            run=LocalInference(store.DB_PATH.with_name("local-inference.db"),database()).run(
                agent_id=form["agent_id"],task_id=form["task_id"],
                resource_id=form["resource_id"],purpose=form["purpose"],
                prompt=form["prompt"],requested_model=form["requested_model"])
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Task ID already recorded") from None
        except (ValueError,KeyError):
            raise HTTPException(422,"Inference configuration invalid") from None
        return JSONResponse(run,headers={"Cache-Control":"no-store"})

    @app.post("/configuration/connections/{identifier}/disable")
    async def disable(request:Request, identifier:str):
        await decision_routes.operator_form(request)
        try:
            database().disable_connection(identifier)
        except ValueError:
            raise HTTPException(422, "Connection removal refused") from None
        return RedirectResponse("/configuration",status_code=303)

    @app.post("/configuration/connections/{identifier}/check")
    async def check(request:Request,identifier:str):
        await decision_routes.operator_form(request)
        db=database();entry=db.latest("connection",identifier)
        if not entry:raise HTTPException(404,"Unknown connection")
        if db.connection_disabled(identifier):raise HTTPException(409,"Connection was disabled")
        if entry["body"]["scope"]=="external":
            db.observe("connection",identifier,"unknown","External endpoint was not contacted; per-job data-sharing authorization and protected credentials are required")
        else:
            from .session_coordinator import LOCAL_OPENER
            try:
                endpoint=entry["body"]["endpoint"].rstrip("/")
                with LOCAL_OPENER.open(urllib.request.Request(endpoint+"/healthz"),timeout=2) as response:
                    if response.status!=200:raise ValueError("Unhealthy status")
                    response.read(4096)
                db.observe("connection",identifier,"reachable","Loopback HTTP health responded; host identity and model execution unconfirmed")
            except (OSError,ValueError):
                db.observe("connection",identifier,"unavailable","Loopback health request failed; configuration preserved")
        return RedirectResponse("/configuration",status_code=303)

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


def credential_database():
    from pathlib import Path
    from .credential_manager import CredentialManager
    from .credentials import CredentialStore
    manager=CredentialManager(store.DB_PATH.with_name("credentials.db"),database())
    helper=os.environ.get("VERITY_CREDENTIAL_HELPER","")
    if helper:
        path=Path(helper)
        if path.is_absolute() and path.is_file() and not path.is_symlink() and os.access(path,os.X_OK):
            manager.native=CredentialStore(path,manager.profile)
    return manager


def local_peer(request):
    decision_routes.local_request(request)
    if not request.client or request.client.host not in ("127.0.0.1","::1","testclient"):
        raise HTTPException(403,"Loopback peer required")


def install(app,render,base_ctx):
    @app.get("/agents/add")
    def add_agent_page(request:Request):
        local_peer(request)
        db=database();ctx=base_ctx(request,"agents")
        entries=db.all()
        ctx.update(operator_token=decision_routes.TOKEN,selected_connection=request.query_params.get("connection_id",""),
                   connections=[x for x in entries if x["kind"]=="connection" and not db.connection_disabled(x["id"])],
                   resources=[x for x in entries if x["kind"]=="resource"])
        response=render("add_agent.html",ctx)
        response.headers["Cache-Control"]="no-store"
        return response

    @app.post("/agents/add")
    async def add_agent(request:Request):
        local_peer(request)
        form=await decision_routes.operator_form(request)
        required={"operator_token","agent_id","adapter_kind","connection_id","resource_id",
                  "instructions","runtime_version","service_identity"}
        if set(form)!=required:
            raise HTTPException(422,"Exact agent registration fields required")
        try:
            body=dict(connection_id=form["connection_id"], instructions=form["instructions"],
                      allowed_tools=["generate_text"], resources=[form["resource_id"]],
                      external_sharing="none", fallback_id="", daily_request_limit=20,
                      session_request_limit=0, require_fresh_connection_observation=True, adapter_kind=form["adapter_kind"],
                      runtime_version=form["runtime_version"].strip(), service_identity=form["service_identity"].strip())
            database().save("agent",form["agent_id"],body,new_only=True)
        except sqlite3.OperationalError:
            raise HTTPException(409,"Configuration is busy; agent registration was not confirmed. Retry after the request finishes.") from None
        except (ValueError,KeyError,TypeError,sqlite3.Error):
            raise HTTPException(422,"Agent registration refused; choose a new ID, supported adapter and existing connection/resource. Do not enter credentials.") from None
        return RedirectResponse("/agents",status_code=303)

    @app.get("/configuration")
    def page(request:Request):
        local_peer(request)
        db=database();ctx=base_ctx(request,"configuration")
        recent=LocalInference(store.DB_PATH.with_name("local-inference.db"),db).recent()
        last_requests={}
        for run in recent:
            last_requests.setdefault(run["configuration"]["connection_id"],run)
        ctx.update(configurations=db.all(),observations=db.observations(),
                   disabled_connections={item["id"]:db.connection_disabled(item["id"]) for item in db.all() if item["kind"]=="connection"},operator_token=decision_routes.TOKEN,
                   inference_runs=recent,last_connection_requests=last_requests)
        response=render("customer_configuration.html",ctx)
        response.headers["Cache-Control"]="no-store"
        return response

    @app.get("/configuration/credentials")
    def credentials_page(request:Request):
        local_peer(request)
        manager=credential_database();entries={x["id"]:x for x in database().all() if x["kind"]=="connection"}
        ctx=base_ctx(request,"configuration")
        ctx.update(operator_token=decision_routes.TOKEN,credential_entries=entries,
                   credential_records=manager.records(),credential_helper_configured=manager.native is not None,
                   credential_disabled={identifier:database().connection_disabled(identifier) for identifier in entries})
        response=render("customer_credentials.html",ctx)
        response.headers["Cache-Control"]="no-store"
        return response

    @app.post("/configuration/credentials/{identifier}/save")
    async def save_credential(request:Request,identifier:str):
        from starlette.concurrency import run_in_threadpool
        from .credentials import CredentialUnavailable
        from .credential_manager import CredentialConflict
        local_peer(request)
        form=await decision_routes.operator_form(request,max_fields=3)
        if set(form)!={"operator_token","expected_digest","secret"}:
            raise HTTPException(422,"Exact credential fields required",headers={"Cache-Control":"no-store"})
        try:
            manager=credential_database()
            await run_in_threadpool(manager.create,identifier,expected_digest=form["expected_digest"],secret=form["secret"])
        except CredentialUnavailable:
            raise HTTPException(503,"Credential save is not confirmed. Review recovery status before trying another save.",headers={"Cache-Control":"no-store"}) from None
        except CredentialConflict:
            raise HTTPException(409,"Connection or credential state changed; reload the credential page.",headers={"Cache-Control":"no-store"}) from None
        except (sqlite3.Error,OSError):
            raise HTTPException(503,"Credential save is not confirmed. Review recovery status before trying another save.",headers={"Cache-Control":"no-store"}) from None
        except ValueError:
            raise HTTPException(422,"Credential save refused; check connection and credential format.",headers={"Cache-Control":"no-store"}) from None
        return RedirectResponse("/configuration/credentials",status_code=303,headers={"Cache-Control":"no-store"})

    @app.post("/configuration/credentials/{reference}/remove")
    async def remove_credential(request:Request,reference:str):
        from starlette.concurrency import run_in_threadpool
        from .credentials import CredentialUnavailable
        from .credential_manager import CredentialConflict
        local_peer(request)
        form=await decision_routes.operator_form(request,max_fields=2)
        if set(form)!={"operator_token","expected_digest"}:
            raise HTTPException(422,"Exact credential removal fields required",headers={"Cache-Control":"no-store"})
        try:
            manager=credential_database()
            await run_in_threadpool(manager.remove,reference,expected_digest=form["expected_digest"])
        except CredentialUnavailable:
            raise HTTPException(503,"Credential removal is not confirmed. The recovery reference is retained.",headers={"Cache-Control":"no-store"}) from None
        except CredentialConflict:
            raise HTTPException(409,"Connection or credential state changed; reload the credential page.",headers={"Cache-Control":"no-store"}) from None
        except (sqlite3.Error,OSError):
            raise HTTPException(503,"Credential removal is not confirmed. The recovery reference is retained.",headers={"Cache-Control":"no-store"}) from None
        except ValueError:
            raise HTTPException(422,"Credential removal refused.",headers={"Cache-Control":"no-store"}) from None
        return RedirectResponse("/configuration/credentials",status_code=303,headers={"Cache-Control":"no-store"})

    @app.post("/configuration")
    async def save(request:Request):
        local_peer(request)
        form=await decision_routes.operator_form(request, max_fields=12)
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
                          daily_request_limit=int(form.get("daily_request_limit","20")),
                          session_request_limit=int(form.get("session_request_limit","0")))
                if form.get("require_fresh_connection_observation", "false") not in {"true", "false"}:
                    raise ValueError("Invalid health observation requirement")
                previous=database().latest("agent",identifier)
                if "require_fresh_connection_observation" in form:
                    body["require_fresh_connection_observation"] = form["require_fresh_connection_observation"] == "true"
                elif previous and "require_fresh_connection_observation" in previous["body"]:
                    body["require_fresh_connection_observation"] = previous["body"]["require_fresh_connection_observation"]
                elif previous is None:
                    body["require_fresh_connection_observation"] = True
                if previous and "permitted_connection_ids" in previous["body"]:
                    body["permitted_connection_ids"]=previous["body"]["permitted_connection_ids"]
                if previous and "adapter_kind" in previous["body"]:
                    body.update({k:previous["body"][k] for k in ("adapter_kind","runtime_version","service_identity") if k in previous["body"]})
            elif kind in ("machine","resource"):
                body=dict(description=form.get("description",""),provenance="operator-entered",status="unknown")
            elif kind=="link":
                body=dict(from_kind=form.get("from_kind",""),from_id=form.get("from_id",""),
                          to_kind=form.get("to_kind",""),to_id=form.get("to_id",""),
                          permission=form.get("permission",""),provenance="operator-entered",status="unknown")
            else:raise ValueError("Unsupported configuration")
            database().save(kind,identifier,body)
        except sqlite3.OperationalError:
            raise HTTPException(409,"Configuration is busy with an in-flight request; change was not confirmed. Retry after it finishes.") from None
        except (ValueError,TypeError,sqlite3.Error):
            raise HTTPException(422,"Configuration refused; check fields and referenced versions. Credentials cannot be entered here.") from None
        return RedirectResponse("/configuration",status_code=303)

    @app.post("/configuration/agents/{identifier}/models")
    async def additional_models(request:Request,identifier:str):
        local_peer(request)
        form=await decision_routes.operator_form(request,max_fields=3)
        if set(form)!={"operator_token","permitted_connection_ids","expected_agent_digest"}:
            raise HTTPException(422,"Exact additional-model permission fields required")
        db=database()
        try:
            db.set_permitted_connections(identifier,[x.strip() for x in form["permitted_connection_ids"].split(",") if x.strip()],form["expected_agent_digest"])
        except sqlite3.OperationalError:
            raise HTTPException(409,"Configuration busy; model permissions were not confirmed") from None
        except (ValueError,TypeError,sqlite3.Error):
            raise HTTPException(422,"Model permissions refused; choose existing local OpenClaw connections distinct from primary") from None
        return RedirectResponse("/configuration",status_code=303)

    @app.post("/configuration/inference")
    async def inference(request:Request):
        local_peer(request)
        form=await decision_routes.operator_form(request)
        required={"operator_token","agent_id","task_id","resource_id","purpose","prompt","requested_model"}
        if set(form) not in (required,required|{"session_id"}):
            raise HTTPException(422,"Exact inference fields required")
        try:
            run=LocalInference(store.DB_PATH.with_name("local-inference.db"),database()).run(
                agent_id=form["agent_id"],task_id=form["task_id"],
                resource_id=form["resource_id"],purpose=form["purpose"],
                prompt=form["prompt"],requested_model=form["requested_model"],session_id=form.get("session_id", ""))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Task ID already recorded") from None
        except (ValueError,KeyError):
            raise HTTPException(422,"Inference configuration invalid") from None
        return JSONResponse(run,headers={"Cache-Control":"no-store"})

    @app.post("/configuration/connections/{identifier}/disable")
    async def disable(request:Request, identifier:str):
        local_peer(request)
        await decision_routes.operator_form(request)
        try:
            database().disable_connection(identifier)
        except sqlite3.OperationalError:
            raise HTTPException(409,"Connection removal is not confirmed while a request is in flight; retry after it finishes.") from None
        except ValueError:
            raise HTTPException(422, "Connection removal refused") from None
        return RedirectResponse("/configuration",status_code=303)

    @app.post("/configuration/connections/{identifier}/check")
    async def check(request:Request,identifier:str):
        local_peer(request)
        await decision_routes.operator_form(request)
        db=database();entry=db.latest("connection",identifier)
        if not entry:raise HTTPException(404,"Unknown connection")
        if db.connection_disabled(identifier):raise HTTPException(409,"Connection was disabled")
        if entry["body"]["scope"]=="external":
            observed_state="unknown"
            detail="External endpoint was not contacted; per-job data-sharing authorization and protected credentials are required"
        else:
            from .session_coordinator import LOCAL_OPENER
            try:
                endpoint=entry["body"]["endpoint"].rstrip("/")
                with LOCAL_OPENER.open(urllib.request.Request(endpoint+"/healthz"),timeout=2) as response:
                    if response.status!=200:raise ValueError("Unhealthy status")
                    response.read(4096)
                observed_state="reachable"
                detail="Loopback HTTP health responded; host identity and model execution unconfirmed"
            except (OSError,ValueError):
                observed_state="unavailable"
                detail="Loopback health request failed; configuration preserved"
        try:
            db.observe("connection",identifier,observed_state,detail,expected_digest=entry["digest"],expected_version=entry["version"])
        except ValueError:
            raise HTTPException(409,"Connection changed during health check; recheck the current version") from None
        except sqlite3.OperationalError:
            raise HTTPException(409,"Configuration is busy; health observation was not confirmed") from None
        return RedirectResponse("/configuration",status_code=303)

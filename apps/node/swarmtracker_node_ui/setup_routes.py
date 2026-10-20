"""Pilot enrollment and local first-launch configuration.

No activation server, background cloud sharing, or credential storage. A signed
revocation list must be imported locally to revoke an otherwise valid offline
code. Clock rollback detection is best effort against the retained local state.
"""
import json
import os
import socket
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from verity_core import activation
from verity_core.json_safety import bounded_json_loads
from . import config, decision_routes, store, runtime_diagnostics

LOCK=RLock()


def path():return store.DB_PATH.with_name("installation.json")


def write(state):
    target=path();target.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=".installation-",dir=target.parent)
    try:
        with os.fdopen(fd,"w") as f:
            os.fchmod(f.fileno(),0o600);json.dump(state,f);f.flush();os.fsync(f.fileno())
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)


def state():
    with LOCK:
        if not path().exists():
            value={"installation_id":"install-"+uuid.uuid4().hex,"settings":{},"max_seen":0,"revocations":None,"activation":None}
            write(value);return value
        value=bounded_json_loads(path().read_text())
        if not isinstance(value,dict) or set(value)!={"installation_id","settings","max_seen","revocations","activation"}:
            raise ValueError("Installation state is invalid")
        activation._id(value["installation_id"])
        if not isinstance(value["settings"],dict) or type(value["max_seen"]) not in (int,float):raise ValueError("Installation state is invalid")
        return value


def public_key():
    value=os.environ.get("VERITY_ACTIVATION_PUBLIC_KEY")
    if not value:raise ValueError("Issuer key has not been bundled")
    return Path(value).read_bytes()


def status(value):
    now=time.time()
    if now+300<value["max_seen"]:raise ValueError("System clock moved backward; correct it before activation")
    key=public_key();revoked=[]
    if value["revocations"]:
        revoked=activation.revocations(value["revocations"],key,now)["license_ids"]
    return activation.entitlement(value["activation"],key,value["installation_id"],now,revoked)


def validate_settings(settings):
    allowed={"operator","model","openclaw_url","ollama_url","runtime_kind"}
    if set(settings)-allowed:raise ValueError("Unknown first-launch setting")
    for key,value in settings.items():
        if not isinstance(value,str) or not value.strip() or len(value)>240 or any(ord(c)<32 for c in value):raise ValueError("Invalid first-launch setting")
        if key == "runtime_kind" and value not in ("openclaw","ollama"):raise ValueError("Invalid runtime kind")
        if key.endswith("_url"):config.assert_local(value)
    return settings


def apply_settings(settings):
    validate_settings(settings)
    mapping={"operator":"OPERATOR","model":"DEFAULT_MODEL","openclaw_url":"OPENCLAW_URL","ollama_url":"OLLAMA_URL","runtime_kind":"RUNTIME_KIND"}
    for key,value in settings.items():setattr(config,mapping[key],value)


def install(app,render,base_ctx):
    @app.middleware("http")
    async def activation_gate(request,call_next):
        if os.environ.get("VERITY_REQUIRE_ACTIVATION") == "1" and request.url.path not in ("/setup","/setup/registry") and not request.url.path.startswith("/static/"):
            try:
                with LOCK:
                    value=state();status(value)
                    if time.time()-value["max_seen"]>60:value["max_seen"]=time.time();write(value)
            except OSError:
                return JSONResponse({"detail":"Installation state could not be read or saved. Check local storage and restart Verity."},status_code=503)
            except (ValueError,TypeError,KeyError):
                if request.url.path.startswith("/api/"):
                    return JSONResponse({"detail":"Pilot activation is required. Open Installation & Runtime."},status_code=403)
                return RedirectResponse("/setup",status_code=303)
        return await call_next(request)

    def setup_page(request:Request, diagnostics=None, setup_error=None, status_code=200, release_preview=None, runtime=False):
        decision_routes.local_request(request)
        try:
            value=state()
            try:
                claims=status(value);active=True
                message="Activation active until "+datetime.fromtimestamp(claims["expires_at"],timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

            except (ValueError,TypeError,OSError):active=False;message="Pilot activation is required, expired, revoked, or not configured."
        except (ValueError,TypeError,OSError):raise HTTPException(503,"Installation state is unavailable; existing state was preserved") from None
        ctx=base_ctx(request,"runtime_setup" if runtime else "setup");ctx.update(installation={"installation_id":value["installation_id"],"settings":value["settings"]},
                   active=active,message=message,license_id=claims["license_id"] if active else None,
                   diagnostics=diagnostics,setup_error=setup_error,operator_token=decision_routes.TOKEN,
                   installed_version=os.environ.get("VERITY_APP_VERSION","0.2.0 (development)"),
                   registry_available=bool(os.environ.get("VERITY_PILOT_REGISTRY")),release_preview=release_preview,
                   release_available=bool(os.environ.get("VERITY_PILOT_REGISTRY") and os.environ.get("VERITY_RELEASE_PUBLIC_KEY")))
        response=render("runtime_setup.html" if runtime else "setup.html",ctx);response.status_code=status_code;response.headers["Cache-Control"]="no-store";return response

    @app.get("/setup")
    def setup(request:Request):
        return setup_page(request)

    @app.get("/setup/runtime")
    def runtime_setup(request:Request):
        return setup_page(request, runtime=True)

    @app.get("/setup/registry")
    def installation_registry(request:Request):
        decision_routes.local_request(request)
        registry_path=os.environ.get("VERITY_PILOT_REGISTRY")
        if not registry_path:raise HTTPException(503,"Private operator registry is not configured on this profile")
        from verity_core.pilot_registry import Registry
        try:installations=Registry(registry_path).installations()
        except (ValueError,OSError) as error:raise HTTPException(503,"Operator registry unavailable") from error
        ctx=base_ctx(request,"setup");ctx.update(installations=installations)
        response=render("installation_registry.html",ctx);response.headers["Cache-Control"]="no-store";return response

    @app.post("/setup/runtime")
    @app.post("/setup")
    async def configure(request:Request):
        form=await decision_routes.operator_form(request)
        if request.url.path == "/setup/runtime" and form.get("action") not in {"configure", "test_connection"}:
            raise HTTPException(422,"Runtime settings cannot activate an installation")
        if form.get("action")=="test_connection":
            return setup_page(request,diagnostics=runtime_diagnostics.check(),runtime=True)
        if form.get("action")=="inspect_release":
            try:
                from verity_core.pilot_registry import Registry
                from verity_core.release_update import inspect
                status(state())
                registry=os.environ["VERITY_PILOT_REGISTRY"]
                key=os.environ["VERITY_RELEASE_PUBLIC_KEY"]
                manifest=Path(form.get("manifest_path", ""));artifact=Path(form.get("artifact_path", ""))
                if not manifest.is_file() or manifest.stat().st_size>16384:raise ValueError("Invalid manifest file")
                preview=inspect(manifest.read_text().strip(),Path(key).read_bytes(),artifact,
                    installation_id=state()["installation_id"],
                    installed_version=os.environ.get("VERITY_APP_VERSION","0.2.0"),schema_version=1,
                    authorization=Registry(registry).release_allowed)
                return setup_page(request,release_preview=preview)
            except (ValueError,OSError,KeyError,TypeError):
                return setup_page(request,setup_error="Release inspection failed: check publisher signature, digest, compatibility and this installation's authorization.",status_code=422)
        try:
            with LOCK:
                value=state();action=form.get("action")
                if time.time()+300<value["max_seen"]:raise ValueError("System clock moved backward")
                if action=="activate":
                    candidate=dict(value,activation=form.get("code"));status(candidate);value=candidate
                elif action=="revoke":
                    old=activation.revocations(value["revocations"],public_key(),time.time())["revision"] if value["revocations"] else 0
                    fresh=activation.revocations(form.get("code"),public_key(),time.time(),old)
                    previous=activation.revocations(value["revocations"],public_key(),time.time())["license_ids"] if value["revocations"] else []
                    if not set(previous).issubset(fresh["license_ids"]):raise ValueError("Revocation lists must be cumulative")
                    value["revocations"]=form["code"]
                elif action=="configure":
                    settings={k:form.get(k,"").strip() for k in ("operator","model","openclaw_url","ollama_url")}
                    settings["runtime_kind"]=form.get("runtime_kind","openclaw")
                    validate_settings(settings)
                    value["settings"]=settings
                else:raise ValueError("Unknown setup action")
                value["max_seen"]=max(time.time(),value["max_seen"]);write(value)
                if action=="configure":apply_settings(settings)
        except (ValueError,TypeError,KeyError,OSError):
            return setup_page(request,setup_error=("The change was not saved. Check the installation ID, code signature, expiry, imported revocation list and system clock. Runtime addresses must use HTTP on 127.0.0.1 or localhost."),status_code=422,runtime=form.get("action") in {"configure", "test_connection"})
        return RedirectResponse("/setup/runtime" if form.get("action")=="configure" else "/setup",status_code=303)

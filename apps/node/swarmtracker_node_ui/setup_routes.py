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
from pathlib import Path
from threading import RLock

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from verity_core import activation
from verity_core.json_safety import bounded_json_loads
from . import config, decision_routes, store

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
        if os.environ.get("VERITY_REQUIRE_ACTIVATION") == "1" and request.url.path!="/setup" and not request.url.path.startswith("/static/"):
            try:
                with LOCK:
                    value=state();status(value)
                    if time.time()-value["max_seen"]>60:value["max_seen"]=time.time();write(value)
            except (ValueError,TypeError,KeyError,OSError):
                return RedirectResponse("/setup",status_code=303)
        return await call_next(request)

    @app.get("/setup")
    def setup(request:Request):
        decision_routes.local_request(request)
        try:
            value=state()
            try:claims=status(value);active=True;message="Pilot activation valid until Unix time "+str(claims["expires_at"])
            except (ValueError,TypeError,OSError):active=False;message="Pilot activation is required, expired, revoked, or not configured."
        except (ValueError,TypeError,OSError):raise HTTPException(503,"Installation state is unavailable; existing state was preserved") from None
        ctx=base_ctx(request,"setup");ctx.update(installation={"installation_id":value["installation_id"],"settings":value["settings"]},active=active,message=message,operator_token=decision_routes.TOKEN)
        response=render("setup.html",ctx);response.headers["Cache-Control"]="no-store";return response

    @app.post("/setup")
    async def configure(request:Request):
        form=await decision_routes.operator_form(request)
        try:
            with LOCK:
                value=state();action=form.get("action")
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
        except (ValueError,TypeError,KeyError,OSError):raise HTTPException(422,"Setup change rejected; verify the code, clock and loopback settings") from None
        return RedirectResponse("/setup",status_code=303)

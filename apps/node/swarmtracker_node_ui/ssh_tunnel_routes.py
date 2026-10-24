"""Explicit local operator controls for existing authenticated SSH identities."""
import subprocess
from fastapi import Request,HTTPException
from fastapi.responses import RedirectResponse,JSONResponse
from starlette.concurrency import run_in_threadpool
from . import customer_routes,decision_routes
from .managed_ssh_tunnel import start_connection,stop_connection,MANAGED,TunnelError


def install(app,render,base_ctx):
    async def reviewed(request,identifier):
        customer_routes.local_peer(request)
        form=await decision_routes.operator_form(request,max_fields=2)
        if set(form)!={'operator_token','expected_digest'}:raise HTTPException(422,'Exact reviewed connection fields required')
        cfg=customer_routes.database();entry=cfg.latest('connection',identifier)
        if not entry:raise HTTPException(404,'Unknown connection')
        if entry['digest']!=form['expected_digest']:raise HTTPException(409,'Connection changed; reload before controlling transport')
        if not entry['body'].get('transport'):raise HTTPException(422,'This connection has no managed SSH transport')
        return cfg,entry

    @app.post('/configuration/connections/{identifier}/ssh/start')
    async def start(request:Request,identifier:str):
        cfg,entry=await reviewed(request,identifier)
        try:await run_in_threadpool(start_connection,cfg,identifier,entry['digest'])
        except (OSError,ValueError,subprocess.SubprocessError):
            raise HTTPException(409,'Authenticated SSH forward not established. Check the existing login, pinned known_hosts and local port. No keys or trust settings were changed. Return to Models & Connections.') from None
        return RedirectResponse('/configuration',status_code=303)

    @app.post('/configuration/connections/{identifier}/ssh/stop')
    async def stop(request:Request,identifier:str):
        await reviewed(request,identifier)
        await run_in_threadpool(stop_connection,identifier)
        return RedirectResponse('/configuration',status_code=303)

    @app.get('/configuration/connections/{identifier}/ssh/status')
    async def status(request:Request,identifier:str):
        customer_routes.local_peer(request)
        entry=customer_routes.database().latest('connection',identifier)
        if not entry:raise HTTPException(404,'Unknown connection')
        tunnel=MANAGED.get(identifier)
        try:view=await run_in_threadpool(tunnel.observation) if tunnel else dict(state='unknown',meaning='No owned tunnel in this app process; start and check explicitly')
        except (OSError,ValueError,subprocess.SubprocessError):view=dict(state='unknown',meaning='SSH observation unavailable; recheck')
        return JSONResponse(dict(view,model_readiness='separate health and governed request required'),headers={'Cache-Control':'no-store'})

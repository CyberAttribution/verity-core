"""Manual environment inventory linked to observed routed requests; no discovery scan."""
from fastapi import HTTPException,Request
import sqlite3
from . import customer_routes,workbench_routes


def install(app,render,base_ctx):
    @app.get('/environment')
    def page(request:Request):
        customer_routes.local_peer(request)
        try:
            cfg=customer_routes.database();items=cfg.all();observations=cfg.observations()
            inventory=[x for x in items if x['kind'] in ('machine','resource')]
            connections=[x for x in items if x['kind']=='connection']
            agents=[x for x in items if x['kind']=='agent']
            links=[x for x in items if x['kind']=='link']
            disabled={x['id']:cfg.connection_disabled(x['id']) for x in connections}
        except (ValueError,sqlite3.Error,OSError):
            raise HTTPException(503,'Environment inventory unavailable; no current map asserted') from None
        ctx=base_ctx(request,'environment');ctx.update(inventory=inventory[:100],connections=connections[:100],configured_agents=agents[:100],
            environment_links=links[:100],observations=observations,disabled_connections=disabled,routed_requests=workbench_routes.summary(request))
        response=render('environment.html',ctx);response.headers['Cache-Control']='no-store';return response

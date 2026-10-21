"""Manual environment inventory linked to observed routed requests; no discovery scan."""
from fastapi import HTTPException,Request
from fastapi.responses import RedirectResponse
import sqlite3,json
from . import customer_routes,workbench_routes
from .customer_config import canonical


def install(app,render,base_ctx):
    @app.get('/environment')
    def page(request:Request):
        customer_routes.local_peer(request)
        try:
            cfg=customer_routes.database();items=cfg.all();observations=cfg.observations()
            inventory=[x for x in items if x['kind'] in ('machine','resource')]
            connections=[x for x in items if x['kind']=='connection']
            agents=[x for x in items if x['kind']=='agent']
            links=[cfg.relationship(x) for x in items if x['kind']=='link']
            disabled={x['id']:cfg.connection_disabled(x['id']) for x in connections}
        except (ValueError,sqlite3.Error,OSError):
            raise HTTPException(503,'Environment inventory unavailable; no current map asserted') from None
        ctx=base_ctx(request,'environment');ctx.update(inventory=inventory[:100],connections=connections[:100],configured_agents=agents[:100],
            environment_links=links[:100],observations=observations,disabled_connections=disabled,routed_requests=workbench_routes.summary(request))
        response=render('environment.html',ctx);response.headers['Cache-Control']='no-store';return response

    def relationship_form(request,identifier=None):
        customer_routes.local_peer(request)
        cfg=customer_routes.database();entry=cfg.latest('link',identifier) if identifier else None
        if identifier and entry is None:raise HTTPException(404,'Relationship not found')
        endpoints=[dict(kind=x['kind'],id=x['id'],version=x['version'],digest=x['digest'])
                   for x in cfg.all() if x['kind']!='link']
        ctx=base_ctx(request,'environment')
        ctx.update(operator_token=customer_routes.decision_routes.TOKEN,endpoints=endpoints,canonical=canonical,
                   entry=entry,history=[cfg.relationship(x) for x in cfg.history('link',identifier)] if entry else [])
        response=render('relationship.html',ctx);response.headers['Cache-Control']='no-store';return response

    @app.get('/environment/relationships/new')
    def new_relationship(request:Request):return relationship_form(request)

    @app.get('/environment/relationships/{identifier}')
    def edit_relationship(identifier:str,request:Request):return relationship_form(request,identifier)

    @app.post('/environment/relationships')
    async def save_relationship(request:Request):
        customer_routes.local_peer(request)
        form=await customer_routes.decision_routes.operator_form(request,max_fields=6)
        if set(form)!={'operator_token','id','from_endpoint','to_endpoint','permission','expected_digest'}:
            raise HTTPException(422,'Exact reviewed relationship fields required')
        try:
            start,end=json.loads(form['from_endpoint']),json.loads(form['to_endpoint'])
            body=dict(from_kind=start['kind'],from_id=start['id'],to_kind=end['kind'],to_id=end['id'],
                      permission=form['permission'],provenance='operator-entered',status='unknown')
            customer_routes.database().save('link',form['id'],body,expected_digest=form['expected_digest'],expected_endpoints=[start,end])
        except (ValueError,KeyError,TypeError,sqlite3.Error):
            raise HTTPException(409,'Relationship or endpoints changed or invalid; reload and review current versions') from None
        return RedirectResponse('/environment/relationships/'+form['id'],status_code=303)

"""Configured local request -> durable observation -> inspect/export journey."""
import hashlib
import json
import sqlite3
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from . import customer_routes, decision_routes, store
from .customer_config import canonical
from .local_inference import LocalInference


def runs():
    return LocalInference(store.DB_PATH.with_name('local-inference.db'),customer_routes.database())


def install(app,render,base_ctx):
    @app.get('/workbench')
    def page(request:Request):
        customer_routes.local_peer(request)
        cfg=customer_routes.database();all_items=cfg.all();choices=[]
        for agent in [x for x in all_items if x['kind']=='agent'][:20]:
            for resource in agent['body']['resources'][:20]:
                effective=cfg.effective(agent['id'],resource)
                choices.append(dict(configuration=effective,sha256=hashlib.sha256(canonical(effective).encode()).hexdigest(),
                    registered_only=effective.get('adapter_kind')=='custom_unverified'))
                if len(choices)>=50:break
            if len(choices)>=50:break
        ctx=base_ctx(request,'workbench');ctx.update(choices=choices,inference_runs=runs().recent(),operator_token=decision_routes.TOKEN)
        response=render('workbench.html',ctx);response.headers['Cache-Control']='no-store';return response

    @app.post('/workbench/requests')
    async def submit(request:Request):
        customer_routes.local_peer(request)
        form=await decision_routes.operator_form(request,max_fields=10)
        required={'operator_token','agent_id','resource_id','task_id','session_id','purpose','prompt','expected_configuration_sha256'}
        if set(form)!=required or not form['expected_configuration_sha256']:
            raise HTTPException(422,'Exact reviewed request fields required')
        try:
            run=runs().run(agent_id=form['agent_id'],resource_id=form['resource_id'],task_id=form['task_id'],session_id=form['session_id'],
                purpose=form['purpose'],prompt=form['prompt'],expected_configuration_sha256=form['expected_configuration_sha256'])
        except sqlite3.IntegrityError:
            raise HTTPException(409,'Task ID already recorded; inspect its outcome before any new request') from None
        except (ValueError,KeyError,TypeError):
            raise HTTPException(422,'Request refused; review configured agent, resource and bounded specimen') from None
        except sqlite3.Error:
            raise HTTPException(503,'Request storage unavailable; completion is unconfirmed. Reconcile before resubmitting.') from None
        return RedirectResponse('/workbench/requests/'+run['id'],status_code=303)

    @app.get('/workbench/requests/{run_id}')
    def detail(request:Request,run_id:str):
        customer_routes.local_peer(request)
        ledger=runs()
        try:run=ledger.get(run_id)
        except KeyError:raise HTTPException(404,'Request not found') from None
        try:ledger.request_export(run_id);available=True
        except KeyError:available=False
        except (ValueError,sqlite3.Error):raise HTTPException(503,'Request evidence unavailable; no verified receipt claimed') from None
        ctx=base_ctx(request,'workbench');ctx.update(run=run,receipt_available=available)
        response=render('request_detail.html',ctx);response.headers['Cache-Control']='no-store';return response

    @app.get('/workbench/requests/{run_id}/export')
    def export(request:Request,run_id:str):
        customer_routes.local_peer(request)
        try:bundle=runs().request_export(run_id)
        except KeyError:raise HTTPException(404,'No retained finished-request receipt; reconcile unfinished or historical requests separately') from None
        except (ValueError,sqlite3.Error):raise HTTPException(503,'Request evidence unavailable') from None
        return JSONResponse(bundle,headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="verity-request-receipt.json"'})

    @app.get('/workbench/requests/{run_id}/checkpoint')
    def checkpoint(request:Request,run_id:str):
        response=export(request,run_id)
        return JSONResponse(json.loads(response.body)['checkpoint'],headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="verity-request-checkpoint.json"'})

"""Guided registration for the tested local OpenClaw v0 text route only."""
import sqlite3
from urllib.parse import urlencode
from fastapi import HTTPException,Request
from fastapi.responses import RedirectResponse
from . import customer_routes,decision_routes


def install(app,render,base_ctx):
    @app.get('/models/connect')
    def page(request:Request):
        customer_routes.local_peer(request)
        ctx=base_ctx(request,'configuration');ctx['operator_token']=decision_routes.TOKEN
        response=render('model_connection.html',ctx);response.headers['Cache-Control']='no-store';return response

    @app.post('/models/connect')
    async def connect(request:Request):
        customer_routes.local_peer(request)
        form=await decision_routes.operator_form(request,max_fields=6)
        required={'operator_token','connection_id','endpoint','model','revision'}
        if set(form) not in (required,required|{'provider'}):
            raise HTTPException(422,'Exact local model connection fields required')
        try:
            provider=form.get('provider','openclaw')
            if provider not in ('openclaw','ollama_local'):raise ValueError('Unsupported local text adapter')
            customer_routes.database().save('connection',form['connection_id'].strip(),dict(provider=provider,endpoint=form['endpoint'].strip(),
                model=form['model'].strip(),revision=form['revision'].strip(),scope='local',credential_ref='none'),new_only=True)
        except sqlite3.OperationalError:
            raise HTTPException(409,'Configuration is busy; connection registration was not confirmed') from None
        except (ValueError,TypeError,sqlite3.Error):
            raise HTTPException(422,'Connection not registered. Use a new ID, an HTTP loopback OpenClaw endpoint and exact model name. Do not enter credentials.') from None
        return RedirectResponse('/agents/add?'+urlencode(dict(connection_id=form['connection_id'].strip())),status_code=303)

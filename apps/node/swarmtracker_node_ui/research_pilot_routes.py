"""Existing Company Research entry point for bounded multi-subject research."""
import json
import sqlite3
import time
from fastapi import Request,HTTPException
from fastapi.responses import RedirectResponse,JSONResponse,HTMLResponse
from . import company_routes,customer_routes,context_routes,decision_routes,store,workbench_routes
from .research_pilot import PilotStore,digest
from verity_core.trust.research_finance import effective_observations


def database():return PilotStore(store.DB_PATH.with_name('company-work.db'))


def install(app,render,base_ctx):
    def refuse(message):
        from html import escape
        return HTMLResponse('<main><h1>Research action not completed</h1><p>'+escape(message)+'</p><p>No automatic retry or external action was performed.</p><a href="/company-work/pilot">Review assignments</a></main>',status_code=409,headers={'Cache-Control':'no-store'})

    @app.get('/company-work/pilot')
    def index(request:Request):
        company_routes.local_peer(request)
        ctx=base_ctx(request,'company_work');cfg=customer_routes.database();choices=[]
        for agent in [x for x in cfg.all() if x['kind']=='agent']:
            for resource in agent['body']['resources']:
                try:
                    selected=cfg.resolve_pre_dispatch(agent['id'],resource,'generate_text');choices.append(dict(configuration=selected,sha256=digest(selected)))
                except (ValueError,KeyError):pass
        ctx.update(assignments=database().assignments(),choices=choices,contexts=context_routes.database().profiles(),operator_token=decision_routes.TOKEN)
        response=render('research_pilot.html',ctx);response.headers['Cache-Control']='no-store';return response

    @app.post('/company-work/pilot')
    async def create(request:Request):
        company_routes.local_peer(request);f=await decision_routes.operator_form(request,max_fields=18)
        required={'operator_token','task_id','title','questions','subjects','source_hosts','confidentiality','agent_id','resource_id','session_id','configuration_sha256','context_id','max_observations','max_requests','duration_minutes','destination'}
        if set(f)!=required:raise HTTPException(422,'Exact assignment fields required')
        try:
            cfg=customer_routes.database().resolve_pre_dispatch(f['agent_id'],f['resource_id'],'generate_text')
            if digest(cfg)!=f['configuration_sha256']:raise ValueError('Configuration changed; reload assignment settings')
            capture=context_routes.database().capture_active(f['context_id'])
            subjects=[]
            for line in f['subjects'].splitlines():
                if line.strip():
                    identity,title,reason=line.split('|',2);subjects.append(dict(id=identity.strip(),title=title.strip(),rationale=reason.strip()))
            duration=int(f['duration_minutes'])
            if not 1<=duration<=1440:raise ValueError('Assignment duration must be1–1440 minutes')
            a=dict(task_id=f['task_id'],title=f['title'],questions=f['questions'],subjects=subjects,source_hosts=[h.strip() for h in f['source_hosts'].splitlines() if h.strip()],confidentiality=f['confidentiality'],agent_id=f['agent_id'],resource_id=f['resource_id'],session_id=f['session_id'],configuration=cfg,context_capture=capture,limits=dict(max_observations=int(f['max_observations']),max_requests=int(f['max_requests']),expires_at=time.time()+duration*60,model_spending_usd='0'),destination=f['destination'],permitted_actions=['record_public_excerpt','calculate','generate_text','export'])
            database().create_assignment(a)
        except (ValueError,KeyError,TypeError,sqlite3.Error) as e:return refuse(str(e))
        return RedirectResponse('/company-work/pilot/'+a['task_id'],303)

    @app.get('/company-work/pilot/{task_id}')
    def detail(request:Request,task_id:str):
        company_routes.local_peer(request)
        try:
            b=database().get_assignment(task_id);a=b['record']['entries'][0]['payload']['assignment'];entries=b['record']['entries']
            observations=effective_observations(entries);selected=request.query_params.get('correct');correction=None
            if selected is not None:
                matches=[o for o in observations if o['id']==selected]
                if len(matches)!=1:raise ValueError('Unknown or ambiguous observation; no correction prefilled')
                correction=matches[0]
            ctx=base_ctx(request,'company_work');ctx.update(bundle=b,assignment=a,observations=observations,correction=correction,operator_token=decision_routes.TOKEN,subjects=a['subjects']+[e['payload']['body'] for e in entries if e['payload']['kind']=='subject_added'])
            response=render('research_pilot_detail.html',ctx);response.headers['Cache-Control']='no-store';return response
        except (ValueError,KeyError,TypeError,sqlite3.Error) as e:return refuse(str(e))

    @app.get('/company-work/pilot/{task_id}/report')
    def report(request:Request,task_id:str):
        company_routes.local_peer(request)
        try:
            from verity_core.trust.research_finance import comparisons,disagreements
            b=database().get_assignment(task_id);entries=b['record']['entries'];a=entries[0]['payload']['assignment']
            subjects=a['subjects']+[e['payload']['body'] for e in entries if e['payload']['kind']=='subject_added']
            rows=comparisons(effective_observations(entries),subjects)
            calculations=[e for e in entries if e['payload']['kind']=='calculation']
            current=bool(calculations and calculations[-1]['payload']['body']['comparables']==rows)
            from verity_core.trust.research_finance import number
            summary=[]
            for row in rows:
                claims={}
                for name,category,territory in [('budget','production_budget',None),('domestic','theatrical_gross','domestic'),('worldwide','theatrical_gross','worldwide')]:
                    selected=[o for o in row['observations'] if o['category']==category and (territory is None or o['territory']==territory)]
                    claims[name]=[dict(id=o['id'],display='missing' if o['value'] is None else o['currency']+' '+format(number(o['value']),',f'),url=o['source']['url']) for o in selected]
                summary.append(dict(subject=row['subject'],claims=claims))
            request_views=[]
            for e in entries:
                if e['payload']['kind']!='request_reconciled':continue
                rid=e['payload']['body']['request_id']
                intended=[i for i,x in enumerate(entries) if x['payload']['kind']=='request_intent' and x['payload']['body']['request_id']==rid]
                view=dict(e)
                view['inputs_current']=len(intended)==1 and effective_observations(entries[:intended[0]])==effective_observations(entries)
                request_views.append(view)
            ctx=base_ctx(request,'company_work');ctx.update(assignment=a,bundle=b,rows=rows,summary=summary,disagreements=disagreements(effective_observations(entries)),export_ready=entries[-1]['payload']['kind']=='human_review',observations_used=sum(e['payload']['kind']=='observation' for e in entries),requests_used=sum(e['payload']['kind']=='request_intent' for e in entries),assignment_expired=time.time()>=a['limits']['expires_at'],calculations=calculations,calculations_current=current,corrections=[e for e in entries if e['payload']['kind']=='correction'],requests=request_views)
            response=render('research_pilot_report.html',ctx);response.headers['Cache-Control']='no-store';return response
        except (ValueError,KeyError,TypeError,sqlite3.Error) as e:return refuse(str(e))

    @app.post('/company-work/pilot/{task_id}/action')
    async def action(request:Request,task_id:str):
        company_routes.local_peer(request);f=await decision_routes.operator_form(request,max_fields=30)
        try:
            if not {'operator_token','expected_head','action'}<=set(f):raise ValueError('Bound reviewed action required')
            db=database();kind=f['action'];head=f['expected_head']
            if kind=='observation' or kind=='correction':
                fields={'operator_token','expected_head','action','id','subject_id','category','value','currency','territory','period','classification','url','publisher','published_at','retrieved_at','passage','collection','dependence_status','origin','basis','uncertainty'}
                fields|={'reason'}
                if set(f)!=fields:raise ValueError('Exact financial fields required')
                o={k:f[k] for k in ('id','subject_id','category','currency','territory','period','classification','uncertainty')};o['value']=f['value'] if f['value'] else None
                o.update(source={k:f[k] for k in ('url','publisher','retrieved_at','passage','collection')},dependence=dict(status=f['dependence_status'],origin=f['origin'],basis=f['basis']),transformations=[]);o['source']['published_at']=f['published_at'] or None
                body=o if kind=='observation' else dict(observation_id=o['id'],replacement=o,reason=f['reason'])
                db.append(task_id,kind,body,head)
            elif kind in ('pause','resume','denial','human_review'):
                if set(f)!={'operator_token','expected_head','action','reason'}:raise ValueError('Exact control fields required')
                db.append(task_id,kind,dict(reason=f['reason']),head)
            elif kind=='calculate':
                if set(f)!={'operator_token','expected_head','action','budgets','low_gross','base_gross','high_gross','exhibitor_share','distribution_fee','marketing_distribution','ancillary_producer_receipts','currency','basis'}:raise ValueError('Exact calculation fields required')
                assumptions=[]
                for budget in f['budgets'].splitlines():
                    if not budget.strip():continue
                    for case in ('low','base','high'):
                        a={k:f[k] or None for k in ('exhibitor_share','distribution_fee','marketing_distribution','ancillary_producer_receipts')}
                        a.update(name=case+' / '+budget.strip(),production_budget=budget.strip(),theatrical_gross=f[case+'_gross'] or None,currency=f['currency'],basis=f['basis']);assumptions.append(a)
                db.calculate(task_id,assumptions,head)
            elif kind=='subject_added':
                if set(f)!={'operator_token','expected_head','action','id','title','rationale'}:raise ValueError('Exact subject fields required')
                db.append(task_id,kind,{k:f[k] for k in ('id','title','rationale')},head)
            elif kind in ('analyze','recover'):
                if set(f)!={'operator_token','expected_head','action','subject_id'}:raise ValueError('Exact request fields required')
                if db.get_assignment(task_id)['checkpoint']['head_hash']!=head:raise ValueError('Assignment changed; reload')
                if kind=='analyze':db.analyze(task_id,workbench_routes.runs(),context_routes.database(),head,f['subject_id'])
                else:db.reconcile(task_id,workbench_routes.runs(),expected_head=head)
            else:
                # Record harmless boundary refusal; never execute submitted action text.
                db.append(task_id,'denial',dict(reason='Out-of-scope requested action refused: '+kind[:100]),head)
                return refuse('Action outside assignment authority; refusal recorded. No external action performed')
        except (ValueError,KeyError,TypeError,sqlite3.Error) as e:return refuse(str(e))
        return RedirectResponse('/company-work/pilot/'+task_id,303)

    @app.get('/company-work/pilot/{task_id}/export/{artifact}.json')
    def export(request:Request,task_id:str,artifact:str):
        company_routes.local_peer(request)
        if artifact not in ('bundle','checkpoint'):raise HTTPException(404,'Unsupported artifact')
        try:
            b=database().get_assignment(task_id)
            if b['record']['entries'][-1]['payload']['kind']!='human_review':raise ValueError('Review current evidence and qualifications before export; later changes require new review')
        except (ValueError,KeyError,TypeError,sqlite3.Error) as e:return refuse(str(e))
        return JSONResponse(b if artifact=='bundle' else b['checkpoint'],headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="governed-research.'+artifact+'.json"'})

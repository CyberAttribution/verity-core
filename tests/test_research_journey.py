import copy
import hashlib
import json
import sqlite3
import pytest
from fastapi.testclient import TestClient
from swarmtracker_node_ui import app as ui, customer_routes, decision_routes, store
from swarmtracker_node_ui.customer_config import CustomerConfig, canonical
from swarmtracker_node_ui.local_inference import LocalInference
from swarmtracker_node_ui.research_journey import ResearchJourney
from verity_core.trust import research_record


@pytest.fixture
def setup(tmp_path,monkeypatch):
    cfg=CustomerConfig(tmp_path/'customer-config.db')
    cfg.save('resource','research',dict(description='Research record',provenance='operator-entered',status='unknown'))
    cfg.save('connection','local',dict(provider='openclaw',endpoint='http://127.0.0.1:9',model='fixture',revision='fixture-v1',scope='local',credential_ref='none'))
    cfg.save('agent','researcher',dict(connection_id='local',instructions='Research only',allowed_tools=['generate_text'],resources=['research'],external_sharing='none',fallback_id=''))
    calls=[]
    def dispatch(c,p,purpose):
        calls.append(p)
        return dict(model='fixture',decision='allowed',event_id='event-1',response='Summary: source https://example.com/article. Implication: evaluate. Uncertainty: unverified scope.',done=True,done_reason='stop')
    monkeypatch.setattr(LocalInference,'_dispatch',staticmethod(dispatch))
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'verity.db');monkeypatch.setattr(customer_routes,'database',lambda:cfg)
    runs=LocalInference(tmp_path/'local-inference.db',cfg)
    j=ResearchJourney(tmp_path/'company-work.db',runs)
    form=dict(operator_token=decision_routes.TOKEN,task_id='article-1',title='Fixture source',date='2026-10-22',text='Synthetic article about a dependency incident.',synthetic='true',source_url='https://example.com/article',source_claim='A dependency incident is described.',agent_id='researcher',resource_id='research',session_id='session-1',purpose='generate_text',expected_configuration_sha256=hashlib.sha256(canonical(cfg.effective('researcher','research')).encode()).hexdigest())
    return cfg,j,calls,TestClient(ui.app,base_url='http://127.0.0.1'),form


def test_full_research_route_decision_restart_and_export(setup):
    cfg,j,calls,c,form=setup
    assert c.post('/company-work/research',data=form,follow_redirects=False).status_code==303
    w=j.work.get('article-1');assert w['operator_decision']['state']=='pending'
    assert 'Fixture source' in c.get('/company-work').text
    pending=j.work.receipt('article-1');assert research_record.verify(pending,pending['checkpoint'])
    choice=dict(operator_token=decision_routes.TOKEN,state='accepted',detail='Record this finding; no execution authority',expected_binding=w['proposal_binding'])
    choice['expected_binding']='0'*64
    assert c.post('/company-work/article-1/decision',data=choice).status_code==409
    choice['expected_binding']=w['proposal_binding']
    assert c.post('/company-work/article-1/decision',data=choice,follow_redirects=False).status_code==303
    assert choice['detail'] in c.get('/company-work').text
    assert c.post('/company-work/article-1/decision',data=choice).status_code==409
    restored=ResearchJourney(j.path,LocalInference(j.runs.path,cfg))
    assert restored.recover('article-1')['operator_decision']['state']=='accepted'
    assert c.post('/company-work/research',data=form).status_code==409
    bundle=c.get('/company-work/article-1/record/bundle.json').json();anchor=c.get('/company-work/article-1/record/checkpoint.json').json()
    assert research_record.verify(bundle,anchor) and len(calls)==1
    assert bundle['record']['entries'][1]['payload']['authority']=='company record inclusion only'
    assert not research_record.verify(bundle,pending['checkpoint'])
    damaged=copy.deepcopy(bundle);damaged['record']['entries'][0]['payload']['proposal']['article']['text']='changed'
    assert not research_record.verify(damaged,anchor)


def test_configuration_change_refuses_acceptance_but_allows_rejection(setup):
    cfg,j,calls,c,form=setup;c.post('/company-work/research',data=form)
    w=j.work.get('article-1');a=cfg.latest('agent','researcher');cfg.set_routing('researcher',False,a['digest'])
    choice=dict(operator_token=decision_routes.TOKEN,state='accepted',detail='Reviewed',expected_binding=w['proposal_binding'])
    assert c.post('/company-work/article-1/decision',data=choice).status_code==409
    assert j.work.get('article-1')['operator_decision']['state']=='pending'
    refused=c.post('/company-work/article-1/decision',data=choice,headers={'Accept':'text/html'})
    assert refused.status_code==409 and 'Return to research review' in refused.text
    assert 'application/json' not in refused.headers['content-type']
    choice['state']='rejected';assert c.post('/company-work/article-1/decision',data=choice,follow_redirects=False).status_code==303
    form['task_id']='paused';form['expected_configuration_sha256']=hashlib.sha256(canonical(cfg.effective('researcher','research')).encode()).hexdigest()
    r=c.post('/company-work/research',data=form,follow_redirects=False)
    assert r.status_code==303 and '/workbench/requests/' in r.headers['location']
    assert len(calls)==1
    assert c.post('/company-work/paused/recover',data=dict(operator_token=decision_routes.TOKEN)).status_code==409


def test_crash_between_inference_and_proposal_recovers_without_dispatch(setup,monkeypatch):
    cfg,j,calls,c,form=setup
    from swarmtracker_node_ui.company_work import CompanyWorkStore
    original=CompanyWorkStore.create
    def crash(*a,**kw):raise sqlite3.OperationalError('simulated crash before proposal transaction')
    monkeypatch.setattr(CompanyWorkStore,'create',crash)
    assert c.post('/company-work/research',data=form).status_code==503
    assert len(calls)==1
    monkeypatch.setattr(CompanyWorkStore,'create',original)
    assert c.post('/company-work/article-1/recover',data=dict(operator_token=decision_routes.TOKEN),follow_redirects=False).status_code==303
    assert c.post('/company-work/article-1/recover',data=dict(operator_token=decision_routes.TOKEN),follow_redirects=False).status_code==303
    assert len(calls)==1 and len(j.work.recent())==1


def test_unpermitted_purpose_and_model_are_denied_before_runtime(setup):
    cfg,j,calls,c,form=setup
    form['purpose']='draft_text'
    r=c.post('/company-work/research',data=form,follow_redirects=False);assert r.status_code==303
    assert calls==[]
    denied=j.runs.recent()[0];assert denied['state']=='denied' and 'Purpose denied' in denied['reason']
    assert 'Inspect denied request' in c.get('/company-work').text
    run=j.runs.run(agent_id='researcher',task_id='wrong-model',resource_id='research',purpose='generate_text',prompt='Fixture',requested_model='unpermitted')
    assert run['state']=='denied' and calls==[]


def test_decision_and_receipt_roll_back_together_on_storage_failure(setup):
    cfg,j,calls,c,form=setup;c.post('/company-work/research',data=form)
    original=j.work.receipt('article-1');w=j.work.get('article-1')
    with j.work._db() as db:
        db.execute("CREATE TRIGGER fail_decision BEFORE UPDATE ON research BEGIN SELECT RAISE(ABORT,'storage failure'); END")
    choice=dict(operator_token=decision_routes.TOKEN,state='accepted',detail='Reviewed',expected_binding=w['proposal_binding'])
    assert c.post('/company-work/article-1/decision',data=choice).status_code==503
    assert j.work.receipt('article-1')==original
    assert j.work.get('article-1')['operator_decision']['state']=='pending'


def test_mutated_display_record_is_refused_and_export_remains_original(setup):
    cfg,j,calls,c,form=setup;c.post('/company-work/research',data=form)
    original=j.work.receipt('article-1')
    with j.work._db() as db:
        row=db.execute('SELECT record FROM research WHERE id=?',('article-1',)).fetchone();record=json.loads(row[0])
        record['article']['title']='Substituted title'
        db.execute('UPDATE research SET record=? WHERE id=?',(canonical(record),'article-1'))
    assert c.get('/company-work').status_code==503
    assert j.work.receipt('article-1')==original


def test_missing_required_receipt_refuses_direct_decision_and_real_route(setup):
    cfg,j,calls,c,form=setup;c.post('/company-work/research',data=form)
    work=j.work.get('article-1')
    with j.work._db() as db:db.execute('DELETE FROM research_receipts WHERE work_id=?',('article-1',))
    with pytest.raises(ValueError,match='Required research receipt missing'):
        j.work.decide('article-1','accepted','Review',expected_binding=work['proposal_binding'])
    choice=dict(operator_token=decision_routes.TOKEN,state='accepted',detail='Review',expected_binding=work['proposal_binding'])
    response=c.post('/company-work/article-1/decision',data=choice)
    assert response.status_code==409
    assert 'evidence is missing' in response.json()['detail']
    assert 'decision remains pending' in response.json()['detail']
    assert c.get('/company-work').status_code==503
    with j.work._db() as db:
        retained=json.loads(db.execute('SELECT record FROM research WHERE id=?',('article-1',)).fetchone()[0])
        assert retained['operator_decision']['state']=='pending'
        assert db.execute('SELECT COUNT(*) FROM research_receipts').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM research_events').fetchone()[0]==0
    assert len(calls)==1


def test_displayed_operator_qualifications_must_match_retained_decision(setup):
    cfg,j,calls,c,form=setup;c.post('/company-work/research',data=form)
    w=j.work.get('article-1')
    c.post('/company-work/article-1/decision',data=dict(operator_token=decision_routes.TOKEN,state='accepted',detail='Qualified research only',expected_binding=w['proposal_binding']))
    original=j.work.receipt('article-1')
    with j.work._db() as db:
        row=db.execute('SELECT record FROM research WHERE id=?',('article-1',)).fetchone();record=json.loads(row[0])
        record['operator_decision']['detail']='Invented execution approval'
        db.execute('UPDATE research SET record=? WHERE id=?',(canonical(record),'article-1'))
    assert c.get('/company-work').status_code==503
    assert j.work.receipt('article-1')==original
    assert original['record']['entries'][-1]['payload']['reason']=='Qualified research only'


def test_exact_research_detail_survives_recent_limit_and_links_only_matching_request(setup):
    cfg,j,calls,c,form=setup;c.post('/company-work/research',data=form)
    original=j.work.get('article-1');request_id=original['request_id']
    from test_company_work import completed_run
    for n in range(21):
        run=completed_run();run['task_id']='archive-'+str(n)
        j.work.create('archive-'+str(n),'https://example.com/archive','Archived fixture',run)
    assert all(w['id']!='article-1' for w in j.work.recent())
    page=c.get('/company-work/article-1')
    assert page.status_code==200 and page.headers['Cache-Control']=='no-store'
    assert 'Fixture source' in page.text and 'Qualified research' not in page.text
    assert '<h2>Research an article</h2>' not in page.text
    assert '/workbench/requests/'+request_id in page.text
    evidence=c.get('/workbench/requests/'+request_id)
    assert 'href="/company-work/article-1"' in evidence.text
    other=j.runs.run(agent_id='researcher',resource_id='research',task_id='unrelated',purpose='generate_text',prompt='Other fixture')
    assert 'href="/company-work/article-1"' not in c.get('/workbench/requests/'+other['id']).text
    assert c.get('/company-work/not-found').status_code==404
    # Reading either page does not repeat model work or alter the pending decision.
    assert len(calls)==2 and j.work.get('article-1')==original


def test_direct_research_detail_keeps_loopback_boundary(setup):
    cfg,j,calls,c,form=setup;c.post('/company-work/research',data=form)
    remote=TestClient(ui.app,base_url='http://127.0.0.1',client=('203.0.113.15',1234))
    assert remote.get('/company-work/article-1').status_code==403

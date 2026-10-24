import hashlib
import sqlite3
import pytest
from test_research_journey import setup
from swarmtracker_node_ui.customer_config import canonical
from verity_core.trust.research_record import prompt

def test_duplicate_refusal_cannot_adopt_other_agent(setup):
    cfg,j,calls,c,form=setup
    cfg.save('agent','other',dict(connection_id='local',instructions='Research only',allowed_tools=['generate_text'],resources=['research'],external_sharing='none',fallback_id=''))
    article=dict(title=form['title'],date=form['date'],text=form['text'],synthetic=True)
    article['sha256']=hashlib.sha256(article['text'].encode()).hexdigest()
    prior=j.runs.run(agent_id='other',task_id=form['task_id'],resource_id='research',session_id='older-session',purpose='generate_text',prompt=prompt(article,form['source_url']),expected_configuration_sha256=hashlib.sha256(canonical(cfg.effective('other','research')).encode()).hexdigest())
    assert prior['state']=='completed'
    response=c.post('/company-work/research',data=form,follow_redirects=False)
    assert response.status_code==409
    with pytest.raises(ValueError, match='binding|intake|identity'):
        j.recover(form['task_id'])
    assert len(calls)==1 and j.work.recent()==[]


def _body(j):
    with j.work._db() as db:
        return __import__('json').loads(db.execute('SELECT body FROM research_intake').fetchone()[0])


def _put(j,body):
    with j.work._db() as db:
        db.execute('UPDATE research_intake SET body=?',(canonical(body),))


@pytest.mark.parametrize('field,value',[
    ('request_id','00000000-0000-0000-0000-000000000000'),('agent_id','other'),
    ('task_id','foreign-intake'),('resource_id','other-resource'),('session_id','other-session'),
    ('purpose','draft_text'),('expected_configuration_sha256','0'*64),
    ('requested_connection_id','foreign-connection'),('context_ref','unapproved-context')])
def test_foreign_binding_refused_without_mutation(setup,field,value):
    cfg,j,calls,c,form=setup
    c.post('/company-work/research',data=form)
    original=j.work.get('article-1');receipt=j.work.receipt('article-1')
    body=_body(j);body['intake_identity'][field]=value;_put(j,body)
    with pytest.raises(ValueError):j.recover('article-1')
    assert j.work.get('article-1')==original and j.work.receipt('article-1')==receipt
    assert j.intakes()[0]['request'] is None and len(calls)==1
    page=c.post('/company-work/article-1/recover',data={'operator_token':form['operator_token']},headers={'Accept':'text/html'})
    assert page.status_code==409 and 'No inference was dispatched' in page.text


def test_same_agent_preexisting_same_prompt_still_wrong_intake(setup):
    cfg,j,calls,c,form=setup
    article=dict(title=form['title'],date=form['date'],text=form['text'],synthetic=True)
    article['sha256']=hashlib.sha256(article['text'].encode()).hexdigest()
    old=j.runs.run(agent_id='researcher',task_id=form['task_id'],resource_id='research',session_id='session-1',purpose='generate_text',prompt=prompt(article,form['source_url']),expected_configuration_sha256=form['expected_configuration_sha256'])
    assert c.post('/company-work/research',data=form).status_code==409
    with pytest.raises(ValueError):j.recover('article-1')
    assert j.runs.get(old['id'])==old and len(calls)==1 and not j.work.recent()


def test_historical_intake_readable_but_no_inferred_authority(setup):
    cfg,j,calls,c,form=setup;c.post('/company-work/research',data=form)
    receipt=j.work.receipt('article-1');body=_body(j);del body['intake_identity'];_put(j,body)
    assert c.get('/company-work').status_code==200
    assert 'Historical intake identity unavailable' in c.get('/company-work').text
    with pytest.raises(ValueError,match='Historical'):j.recover('article-1')
    assert j.work.receipt('article-1')==receipt and len(calls)==1


def test_uncertain_reservation_never_replayed(setup,monkeypatch):
    cfg,j,calls,c,form=setup
    def lose(*args,**kwargs):raise SystemExit('lost acknowledgement')
    monkeypatch.setattr(j.runs,'_dispatch',lose)
    kwargs={k:form[k] for k in ('task_id','source_url','source_claim','agent_id','resource_id','session_id','purpose','expected_configuration_sha256')}
    with pytest.raises(SystemExit):j.submit(**kwargs,article=dict(title=form['title'],date=form['date'],text=form['text'],synthetic=True))
    assert j.runs.recent()[0]['state']=='reserved'
    with pytest.raises(ValueError,match='unfinished'):j.recover('article-1')
    assert not calls and not j.work.recent()


def test_recovered_export_binds_intake_and_refuses_substitution(setup):
    import copy
    from verity_core.trust import research_record
    cfg,j,calls,c,form=setup;c.post('/company-work/research',data=form)
    bundle=j.work.receipt('article-1');assert research_record.verify(bundle,bundle['checkpoint'])
    p=bundle['record']['entries'][0]['payload']['proposal']
    assert p['intake_identity']['request_id']==p['request_id']
    damaged=copy.deepcopy(p);damaged['intake_identity']['session_id']='foreign'
    with pytest.raises(ValueError):research_record.validate(damaged)
    from swarmtracker_node_ui.research_journey import ResearchJourney
    from swarmtracker_node_ui.local_inference import LocalInference
    restored=ResearchJourney(j.path,LocalInference(j.runs.path,cfg))
    assert restored.recover('article-1')==j.work.get('article-1') and len(calls)==1


def test_ambiguous_candidates_refused_even_if_import_broke_unique_index(setup):
    import json
    cfg,j,calls,c,form=setup;c.post('/company-work/research',data=form)
    original=j.work.receipt('article-1')
    with j.runs._db() as db:
        db.execute('CREATE TABLE imported_runs AS SELECT * FROM runs')
        db.execute('INSERT INTO imported_runs SELECT * FROM runs')
        db.execute('DROP TABLE runs');db.execute('ALTER TABLE imported_runs RENAME TO runs')
    with pytest.raises(ValueError,match='ambiguous'):j.recover('article-1')
    assert j.work.receipt('article-1')==original and len(calls)==1


def test_concurrent_matching_recovery_no_receipt_replacement(setup):
    from concurrent.futures import ThreadPoolExecutor
    cfg,j,calls,c,form=setup;c.post('/company-work/research',data=form)
    original=j.work.receipt('article-1')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(j.recover,['article-1','article-1']))
    assert results[0]==results[1] and j.work.receipt('article-1')==original and len(calls)==1
    with j.work._db() as db:assert db.execute('SELECT COUNT(*) FROM research_events').fetchone()[0]==0


def test_changed_config_recovery_retains_historical_output_acceptance_stays_refused(setup,monkeypatch):
    from swarmtracker_node_ui.company_work import CompanyWorkStore
    cfg,j,calls,c,form=setup
    original=CompanyWorkStore.create
    monkeypatch.setattr(CompanyWorkStore,'create',lambda *a,**kw: (_ for _ in ()).throw(sqlite3.OperationalError('crash')))
    assert c.post('/company-work/research',data=form).status_code==503
    monkeypatch.setattr(CompanyWorkStore,'create',original)
    a=cfg.latest('agent','researcher');cfg.set_routing('researcher',False,a['digest'])
    work=j.recover('article-1');assert work['operator_decision']['state']=='pending'
    choice=dict(operator_token=form['operator_token'],state='accepted',detail='stale authority',expected_binding=work['proposal_binding'])
    assert c.post('/company-work/article-1/decision',data=choice).status_code==409
    assert len(calls)==1 and j.work.get('article-1')['operator_decision']['state']=='pending'

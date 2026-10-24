import copy
import sqlite3
import pytest
from test_research_journey import setup
from verity_core.trust import evidence_profile as profile
from verity_core.trust import research_record, record as trust


def accepted(setup):
    cfg,j,calls,c,form=setup
    assert c.post('/company-work/research',data=form,follow_redirects=False).status_code==303
    work=j.work.get('article-1')
    assert c.post('/company-work/article-1/decision',data=dict(operator_token=form['operator_token'],state='accepted',detail='Qualified lead only; exposure unknown. No execution authority.',expected_binding=work['proposal_binding']),follow_redirects=False).status_code==303
    return setup


def denial(cfg,j,session='session-1'):
    a=cfg.latest('agent','researcher');cfg.set_routing('researcher',False,a['digest'])
    run=j.runs.run(agent_id='researcher',resource_id='research',task_id='denied-1',session_id=session,purpose='generate_text',prompt='Denied action')
    assert run['state']=='denied'
    return j.runs.request_export(run['id'])


def make_profile(setup):
    cfg,j,calls,c,form=accepted(setup)
    decision=j.work.receipt('article-1');denied=denial(cfg,j)
    correction=profile.correction(decision,[],'Unsupported deployment assumption corrected; exposure unknown.',kind='correction')
    return profile.assemble(decision,denied,[correction]),decision,denied,correction


def test_real_routes_correction_profile_restart_and_original_hashes(setup):
    cfg,j,calls,c,form=accepted(setup)
    original=j.work.get('article-1');receipt=j.work.receipt('article-1');denied=denial(cfg,j)
    post=dict(operator_token=form['operator_token'],text='Correction: deployment assumed; source dependence unknown.',kind='correction',expected_binding=original['proposal_binding'],expected_head='')
    assert c.post('/company-work/article-1/correction',data=post,follow_redirects=False).status_code==303
    assert c.post('/company-work/article-1/correction',data=post).status_code==409
    assert j.work.get('article-1')==original and j.work.receipt('article-1')==receipt
    assert research_record.verify(receipt,receipt['checkpoint']) and len(j.work.corrections('article-1'))==1
    form2=dict(operator_token=form['operator_token'],denied_request_id=denied['record']['entries'][0]['payload']['run_id'])
    assert c.post('/company-work/article-1/profile',data=form2,follow_redirects=False).status_code==303
    exported=j.work.profiles('article-1')[0];head=exported['checkpoint']['head_hash']
    assert profile.verify(exported,exported['checkpoint'])
    assert c.get('/company-work/article-1/profile/'+head+'/bundle.json').json()==exported
    assert c.get('/company-work/article-1/profile/'+head+'/checkpoint.json').json()==exported['checkpoint']
    page=c.get('/company-work/article-1');assert page.status_code==200
    assert 'deployment assumed' in page.text and 'Qualified lead only' in page.text and 'Internal provisional' in page.text
    assert c.post('/company-work/article-1/profile',data=form2,follow_redirects=False).status_code==303
    from swarmtracker_node_ui.company_work import CompanyWorkStore
    restored=CompanyWorkStore(j.path)
    assert restored.profiles('article-1')==[exported] and len(calls)==1 and restored.receipt('article-1')==receipt


@pytest.mark.parametrize('field',['schema','critical','mapping','disclosure','decision','denial','corrections'])
def test_tamper_or_removed_qualification_fails_retained_checkpoint(setup,field):
    bundle,*_=make_profile(setup);damaged=copy.deepcopy(bundle)
    damaged['record']['entries'][0]['payload'][field]='removed or substituted'
    assert not profile.verify(damaged,bundle['checkpoint'])


def test_unsupported_semantics_fail_even_with_new_self_consistent_chain(setup):
    bundle,decision,denied,correction=make_profile(setup)
    payload=copy.deepcopy(bundle['record']['entries'][0]['payload']);payload['critical'].append('unknown-grant')
    record=trust.new_record(bundle['record']['subject']);trust.append_entry(record,'note',payload=payload)
    bad=dict(schema=profile.SCHEMA,record=record,checkpoint=research_record.checkpoint(record))
    assert not profile.verify(bad,bad['checkpoint'])
    with pytest.raises(ValueError):profile.assemble(decision,denied,[])
    with pytest.raises(ValueError):profile.assemble(decision,decision['record']['entries'][0]['payload']['proposal']['request_bundle'],[correction])
    with pytest.raises(ValueError):profile.assemble(decision,denied,[correction,correction])


def test_wrong_session_denial_refused(setup):
    cfg,j,calls,c,form=accepted(setup);decision=j.work.receipt('article-1')
    denied=denial(cfg,j,session='foreign');correction=profile.correction(decision,[],'Qualification')
    with pytest.raises(ValueError,match='same agent/resource/session'):profile.assemble(decision,denied,[correction])


def test_correction_chain_rollback_withdrawal_preserve_original(setup):
    cfg,j,calls,c,form=accepted(setup);original=j.work.receipt('article-1');work=j.work.get('article-1')
    first=j.work.correct('article-1','Do not rely on unsupported implications',kind='withdrawal',expected_binding=work['proposal_binding'],expected_head='')
    second=j.work.correct('article-1','No independently retrieved source snapshot',kind='qualification',expected_binding=work['proposal_binding'],expected_head=first['checkpoint']['head_hash'])
    assert j.work.corrections('article-1')==[first,second]
    with pytest.raises(ValueError):profile.validate_corrections(original,[second,first])
    with j.work._db() as db:db.execute("CREATE TRIGGER fail_correction BEFORE INSERT ON research_events BEGIN SELECT RAISE(ABORT,'storage failure'); END")
    with pytest.raises(sqlite3.IntegrityError):j.work.correct('article-1','Will fail atomically',kind='correction',expected_binding=work['proposal_binding'],expected_head=second['checkpoint']['head_hash'])
    assert j.work.corrections('article-1')==[first,second] and j.work.receipt('article-1')==original and len(calls)==1


def test_profile_cannot_be_reconciled_under_foreign_work_id(setup):
    bundle,decision,denied,correction=make_profile(setup)
    cfg,j,calls,c,form=setup
    with j.work._db() as db:
        db.execute("INSERT INTO research_events(work_id,at,action,detail) VALUES (?,?,?,?)",('foreign-work',1,'provisional_profile_exported',__import__('json').dumps(bundle)))
    with pytest.raises((ValueError,KeyError)):j.work.profiles('foreign-work')


def test_resealed_wrong_correction_link_and_removal_refused(setup):
    bundle,decision,denied,correction=make_profile(setup)
    p=copy.deepcopy(correction['record']['entries'][0]['payload']);p['previous_correction_head']='0'*64
    r=trust.new_record(correction['record']['subject']);trust.append_entry(r,'note',actor='local operator',payload=p)
    changed=dict(schema=profile.CORRECTION,record=r,checkpoint=research_record.checkpoint(r))
    with pytest.raises(ValueError,match='binding'):profile.assemble(decision,denied,[changed])
    payload=copy.deepcopy(bundle['record']['entries'][0]['payload']);payload['corrections']=[]
    r=trust.new_record(bundle['record']['subject']);trust.append_entry(r,'note',payload=payload)
    stripped=dict(schema=profile.SCHEMA,record=r,checkpoint=research_record.checkpoint(r))
    assert not profile.verify(stripped,stripped['checkpoint']) and not profile.verify(stripped,bundle['checkpoint'])


def test_concurrent_correction_one_winner_and_no_receipt_replacement(setup):
    from concurrent.futures import ThreadPoolExecutor
    cfg,j,calls,c,form=accepted(setup);work=j.work.get('article-1');original=j.work.receipt('article-1')
    def attempt(text):
        try:return j.work.correct('article-1',text,kind='correction',expected_binding=work['proposal_binding'],expected_head='')
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,['First correction','Concurrent correction']))
    assert sum(x is not None for x in results)==1
    assert len(j.work.corrections('article-1'))==1 and j.work.receipt('article-1')==original and len(calls)==1


def test_wrong_agent_denial_refused(setup):
    cfg,j,calls,c,form=accepted(setup);decision=j.work.receipt('article-1')
    body=cfg.latest('agent','researcher')['body'];cfg.save('agent','other',body)
    a=cfg.latest('agent','other');cfg.set_routing('other',False,a['digest'])
    run=j.runs.run(agent_id='other',resource_id='research',task_id='denied-other',session_id='session-1',purpose='generate_text',prompt='Denied elsewhere')
    other=j.runs.request_export(run['id']);correction=profile.correction(decision,[],'Qualification')
    with pytest.raises(ValueError,match='same agent/resource/session'):profile.assemble(decision,other,[correction])

import copy
import datetime as dt
import hashlib
import time
import pytest
from test_research_journey import setup
from swarmtracker_node_ui.research_pilot import PilotStore,verify,digest
from verity_core.trust.research_finance import validate_observation,scenarios,comparisons,effective_observations
from verity_core.organization_context import ContextStore


def context(tmp_path):
    c=ContextStore(tmp_path/'organization-context.db')
    value='Approved public research context; no confidential material or outreach.'
    now=dt.datetime.now(dt.timezone.utc)
    b=dict(title='Public pilot context',entries=[dict(id='brief',kind='policy',status='fact',subject='Public research',value=value,source_ref='Explicit operator brief',source_sha256=hashlib.sha256(value.encode()).hexdigest(),checked_at=now.strftime('%Y-%m-%dT%H:%M:%SZ'),review_before=(now+dt.timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ'),qualifications=[])])
    p=c.propose('pilot-public',b);c.approve(p['profile_id'],p['version'],p['sha256'],p['parent_generation'],p['parent_digest'],actor='fixture operator',reason='Fixture explicit approval')
    return c


def assignment(cfg,c):
    return dict(task_id='pilot-1',title='Pilot',questions='Describe uncertainty without invented facts.',subjects=[dict(id='film1',title='Film one',rationale='Fixture')],source_hosts=['example.com'],permitted_actions=['record_public_excerpt','calculate','generate_text','export'],confidentiality='No private material',agent_id='researcher',resource_id='research',session_id='session-1',configuration=cfg.effective('researcher','research'),context_capture=c.capture_active('pilot-public'),limits=dict(max_observations=10,max_requests=3,expires_at=time.time()+3600,model_spending_usd='0'),destination='Local private review')


def observation():
    return dict(id='o1',subject_id='film1',category='production_budget',value='5000000',currency='USD',territory='worldwide',period='2019 nominal reporting year',classification='reported',source=dict(url='https://example.com/film',publisher='Fixture publisher',published_at=None,retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat(),passage='Reported budget $5 million.',collection='synthetic_fixture'),dependence=dict(status='unknown',origin='unknown',basis='Publisher not independent corroboration'),uncertainty='Reported budget not independently corroborated',transformations=[])


def append(db,b,kind,body):return db.append('pilot-1',kind,body,b['checkpoint']['head_hash'])


def test_revision_correction_stale_refusal_and_restart(setup,tmp_path):
    cfg,j,calls,client,form=setup;c=context(tmp_path);db=PilotStore(j.path);b=db.create_assignment(assignment(cfg,c));original=copy.deepcopy(b)
    o=observation();b=append(db,b,'observation',o)
    with pytest.raises(ValueError,match='changed'):append(db,original,'pause',dict(reason='stale'))
    corrected=dict(o,value='6000000');b=append(db,b,'correction',dict(observation_id='o1',replacement=corrected,reason='Fixture correction, not live discovery'))
    assert b['record']['entries'][1]['payload']['body']['value']=='5000000'
    assert effective_observations(b['record']['entries'])[0]['value']=='6000000'
    assert b['record']['entries'][-1]['payload']['body']['previous_effective_sha256']==digest(o)
    assert PilotStore(j.path).get_assignment('pilot-1')==b and verify(b,b['checkpoint'])
    assert not verify(b,original['checkpoint'])
    tampered=copy.deepcopy(b);tampered['record']['entries'][1]['payload']['body']['value']='1';assert not verify(tampered,b['checkpoint'])
    wrong=dict(corrected,currency='EUR')
    with pytest.raises(ValueError,match='identity'):append(db,b,'correction',dict(observation_id='o1',replacement=wrong,reason='wrong'))
    b=append(db,b,'pause',dict(reason='Operator interruption'))
    with pytest.raises(ValueError,match='paused'):append(db,b,'observation',dict(o,id='o2'))
    b=append(db,b,'resume',dict(reason='Operator resumes same assignment'))
    b=append(db,b,'subject_added',dict(id='film2',title='Second film',rationale='Expansion without source change'))
    assert len(b['record']['entries'])==6 and calls==[]


def test_decimal_missing_conflicting_and_scope():
    o=observation();missing=dict(o,id='missing',value=None,classification='missing');assert validate_observation(missing)['value'] is None
    with pytest.raises(ValueError):validate_observation(dict(o,value='NaN'))
    with pytest.raises(ValueError):validate_observation(dict(o,value=None))
    gross=dict(o,id='g',category='theatrical_gross',value='10000000',territory='domestic')
    rows=comparisons([o,dict(o,id='conflict',value='6000000'),gross],[dict(id='film1',title='Fixture')]);assert len(rows[0]['comparisons'])==2 and rows[0]['comparisons'][0]['scope_mismatch']
    a=dict(name='base',production_budget='5000000',theatrical_gross='10000000',exhibitor_share='0.5',distribution_fee='0.3',marketing_distribution='1000000',ancillary_producer_receipts='0',currency='USD',basis='Explicit synthetic assumptions; not forecast')
    result=scenarios([a]);assert result['scenarios'][0]['result']['modeled_cash_after_listed_costs']=='-2500000.00'
    assert scenarios([dict(a,ancillary_producer_receipts=None)])['scenarios'][0]['result'] is None
    with pytest.raises(ValueError):scenarios([dict(a,exhibitor_share='1.1')])


def test_fabricated_calculation_and_reconciliation_refused(setup,tmp_path):
    cfg,j,calls,client,form=setup;db=PilotStore(j.path);b=db.create_assignment(assignment(cfg,context(tmp_path)))
    with pytest.raises(ValueError):append(db,b,'calculation',dict(result='invented'))
    with pytest.raises(ValueError):append(db,b,'request_reconciled',dict(request_id='invented'))
    assert db.get_assignment('pilot-1')==b


def test_routes_no_shadowing_and_export_requires_current_review(setup,tmp_path):
    cfg,j,calls,client,form=setup;c=context(tmp_path);db=PilotStore(j.path);b=db.create_assignment(assignment(cfg,c))
    assert client.get('/company-work/pilot').status_code==200
    page=client.get('/company-work/pilot/pilot-1');assert page.status_code==200 and 'Financial observations' in page.text
    assert client.get('/company-work/pilot/pilot-1/export/bundle.json').status_code==409
    from swarmtracker_node_ui import decision_routes
    f=dict(operator_token=decision_routes.TOKEN,expected_head=b['checkpoint']['head_hash'],action='human_review',reason='Reviewed with qualifications')
    assert client.post('/company-work/pilot/pilot-1/action',data=f,follow_redirects=False).status_code==303
    b=db.get_assignment('pilot-1');assert verify(client.get('/company-work/pilot/pilot-1/export/bundle.json').json(),b['checkpoint'])
    denied=dict(f,expected_head=b['checkpoint']['head_hash'],action='external_outreach')
    assert client.post('/company-work/pilot/pilot-1/action',data=denied).status_code==409
    assert client.get('/company-work/pilot/pilot-1/export/bundle.json').status_code==409 and calls==[]


def test_real_ledger_stub_inference_recovery_and_changed_context(setup,tmp_path,monkeypatch):
    cfg,j,calls,client,form=setup;c=context(tmp_path);db=PilotStore(j.path);b=db.create_assignment(assignment(cfg,c))
    original=db.reconcile
    def interrupt(*a,**kw):raise RuntimeError('Fixture interruption after actual ledger completion')
    monkeypatch.setattr(db,'reconcile',interrupt)
    with pytest.raises(RuntimeError):db.analyze('pilot-1',j.runs,c,b['checkpoint']['head_hash'])
    assert len(calls)==1
    with pytest.raises(ValueError,match='Unreconciled'):db.analyze('pilot-1',j.runs,c,db.get_assignment('pilot-1')['checkpoint']['head_hash'])
    monkeypatch.setattr(db,'reconcile',original);b=db.reconcile('pilot-1',j.runs)
    assert len(calls)==1 and b['record']['entries'][-1]['payload']['kind']=='request_reconciled'
    snap=c.capture_active('pilot-public')['snapshot'];c.withdraw('pilot-public',snap['generation'],snap['sha256'],actor='fixture',reason='Withdraw')
    with pytest.raises(ValueError):db.analyze('pilot-1',j.runs,c,b['checkpoint']['head_hash'])
    assert len(calls)==1


def test_offline_reproduction_uses_historical_correction_cutoff(setup,tmp_path):
    from verity_core.trust.research_reproduction import verify as reproduce
    cfg,j,calls,client,form=setup;db=PilotStore(j.path);b=db.create_assignment(assignment(cfg,context(tmp_path)));o=observation();b=append(db,b,'observation',o)
    a=dict(name='base',production_budget='3000000',theatrical_gross='10000000',exhibitor_share='0.5',distribution_fee='0.3',marketing_distribution='1000000',ancillary_producer_receipts='0',currency='USD',basis='Synthetic fixture assumptions, noforecast')
    b=db.calculate('pilot-1',[a],b['checkpoint']['head_hash'])
    b=append(db,b,'correction',dict(observation_id='o1',replacement=dict(o,value='6000000'),reason='Fixture correction'))
    b=db.calculate('pilot-1',[dict(a,name='revised')],b['checkpoint']['head_hash'])
    assert reproduce(b,b['checkpoint'])['calculation_revisions']==2
    bad=copy.deepcopy(b);bad['record']['entries'][-1]['payload']['body']['analysis']['scenarios'][0]['result']['modeled_cash_after_listed_costs']='9999999.00'
    assert not reproduce(bad,b['checkpoint'])['integrity']


def test_assignment_context_change_denies_before_intent_or_dispatch(setup,tmp_path):
    cfg,j,calls,client,form=setup;c=context(tmp_path);db=PilotStore(j.path);b=db.create_assignment(assignment(cfg,c))
    snapshot=c.capture_active('pilot-public')['snapshot'];body=copy.deepcopy(snapshot['body']);body['entries'][0]['value']='Changed reviewed context'
    p=c.propose('pilot-public',body);c.approve(p['profile_id'],p['version'],p['sha256'],p['parent_generation'],p['parent_digest'],actor='fixture',reason='New context')
    with pytest.raises(ValueError,match='context changed'):db.analyze('pilot-1',j.runs,c,b['checkpoint']['head_hash'])
    assert db.get_assignment('pilot-1')==b and calls==[]


def test_missing_ledger_recovery_does_not_adopt_other_request(setup,tmp_path):
    import uuid
    cfg,j,calls,client,form=setup;c=context(tmp_path);db=PilotStore(j.path);b=db.create_assignment(assignment(cfg,c));a=b['record']['entries'][0]['payload']['assignment'];rid=str(uuid.uuid4())
    intent=dict(request_id=rid,task_id='research-'+rid,agent_id=a['agent_id'],resource_id=a['resource_id'],session_id=a['session_id'],purpose='generate_text',configuration_sha256=digest(a['configuration']),context_identity={k:a['context_capture']['snapshot'][k] for k in ('profile_id','version','generation','sha256')},prompt='Reserved but process interrupted before request ledger insertion')
    b=append(db,b,'request_intent',intent)
    with pytest.raises(ValueError,match='no retained ledger entry'):db.reconcile('pilot-1',j.runs)
    with pytest.raises(ValueError,match='Unreconciled'):db.analyze('pilot-1',j.runs,c,b['checkpoint']['head_hash'])
    assert calls==[] and j.runs.recent()==[]


def test_valid_rehashed_receipt_with_changed_actual_configuration_refused(setup,tmp_path):
    from verity_core.trust import inference_receipt,record as trust,research_record
    cfg,j,calls,client,form=setup;c=context(tmp_path);db=PilotStore(j.path);b=db.create_assignment(assignment(cfg,c));b=db.analyze('pilot-1',j.runs,c,b['checkpoint']['head_hash'])
    entries=b['record']['entries'];body=copy.deepcopy(entries[-1]['payload']['body']);r=body['receipt'];p=copy.deepcopy(r['record']['entries'][0]['payload']);p['configuration']['connection_hash']='0'*64
    record=trust.new_record(dict(kind='inference_receipt',identifier=p['run_id']));trust.append_entry(record,'note',payload=p);body['receipt']=dict(schema=r['schema'],record=record,checkpoint=research_record.checkpoint(record))
    assert inference_receipt.verify(body['receipt'],body['receipt']['checkpoint'])
    with pytest.raises(ValueError,match='Actual receipt configuration'):PilotStore._validate_reconciliation(entries[:-1],body)
    assert len(calls)==1


def test_readable_report_retains_cited_claims_and_marks_stale_calculations(setup,tmp_path):
    cfg,j,calls,client,form=setup;db=PilotStore(j.path);b=db.create_assignment(assignment(cfg,context(tmp_path)));o=observation();b=append(db,b,'observation',o)
    a=dict(name='base',production_budget='3000000',theatrical_gross='10000000',exhibitor_share='0.5',distribution_fee='0.25',marketing_distribution='2000000',ancillary_producer_receipts='0',currency='USD',basis='Synthetic explicit assumptions')
    b=db.calculate('pilot-1',[a],b['checkpoint']['head_hash']);r=client.get('/company-work/pilot/pilot-1/report')
    assert r.status_code==200 and r.headers['cache-control']=='no-store' and 'Fixture publisher' in r.text and 'https://example.com/film' in r.text and 'Comparable inputs match the current dataset' in r.text
    b=append(db,b,'correction',dict(observation_id='o1',replacement=dict(o,value='6000000'),reason='Synthetic correction reason'))
    r=client.get('/company-work/pilot/pilot-1/report');assert 'outdated relative to current observations' in r.text and 'Synthetic correction reason' in r.text and 'Historical results; inputs have changed' in r.text and calls==[]


def test_disagreement_retains_scopes_and_does_not_count_decimal_spelling_or_missing():
    from verity_core.trust.research_finance import disagreements
    o=observation();same=dict(o,id='same',value='5000000.0');missing=dict(o,id='unknown',value=None,classification='missing')
    assert disagreements([o,same,missing])==[]
    changed=dict(o,id='other',value='5000001');groups=disagreements([o,changed,dict(o,id='territory',territory='domestic',value='1'),dict(o,id='euro',currency='EUR',value='2'),dict(o,id='other-period',period='2020',value='3')])
    assert len(groups)==1 and [x['value'] for x in groups[0]['claims']]==['5000000','5000001']


def test_readable_report_marks_model_interpretation_historical_after_new_evidence(setup,tmp_path):
    cfg,j,calls,client,form=setup;c=context(tmp_path);db=PilotStore(j.path);b=db.create_assignment(assignment(cfg,c));b=db.analyze('pilot-1',j.runs,c,b['checkpoint']['head_hash'])
    assert 'Financial inputs match the current dataset' in client.get('/company-work/pilot/pilot-1/report').text
    b=append(db,b,'observation',observation())
    page=client.get('/company-work/pilot/pilot-1/report').text
    assert 'Historical interpretation: newer evidence or corrections differ' in page and len(calls)==1

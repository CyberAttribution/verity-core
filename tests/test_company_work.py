import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from apps.node.swarmtracker_node_ui.company_work import CompanyWorkStore


def completed_run():
    response='Source facts and a labeled inference.'
    return dict(task_id='research-01',agent_id='existingOpenClaw',model='qwen2.5:7b-instruct',
        decision='allowed',state='completed',configuration={'agent_version':1,'connection_version':1},
        result=dict(response=response,response_sha256=hashlib.sha256(response.encode()).hexdigest(),
            runtime_event_id='event-01',runtime_decision='allowed'))


def test_research_stages_are_distinct_durable_and_no_duplicate_agent_task(tmp_path):
    path=tmp_path/'company-work.db';store=CompanyWorkStore(path)
    url='https://www.docker.com/blog/docker-hub-security-advisory-cve-2026-105445/'
    record=store.create('docker-brief',url,'Docker reported token owner reassignment.',completed_run())
    assert record['source_claim']['text'] != record['agent_interpretation']['response']
    assert record['operator_decision']['state']=='pending'
    assert record['assigned_task'] is record['observed_work'] is record['verified_result'] is None
    assert CompanyWorkStore(path).get('docker-brief')==record
    with pytest.raises(sqlite3.IntegrityError):
        store.create('different-id',url,'Same run must not be imported twice.',completed_run())
    wrong=completed_run();wrong['result']['response_sha256']='0'*64
    with pytest.raises(ValueError):store.create('bad-hash',url,'Claim',wrong)
    denied=completed_run();denied['state']='denied'
    with pytest.raises(ValueError):store.create('bad-decision',url,'Claim',denied)
    assert len(store.recent())==1


def test_realistic_research_is_visible_across_four_pages_without_decision(tmp_path,monkeypatch):
    from swarmtracker_node_ui import app as ui, store
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'profile.db')
    CompanyWorkStore(tmp_path/'company-work.db').create('docker-brief',
        'https://www.docker.com/blog/docker-hub-security-advisory-cve-2026-105445/',
        'Docker reported token owner reassignment.',completed_run())
    client=TestClient(ui.app,base_url='http://127.0.0.1')
    for path in ('/company-work','/agents','/governance','/sessions'):
        response=client.get(path)
        assert response.status_code==200,path
        assert 'docker-brief' in response.text,path
        assert 'pending' in response.text,path
    detail=client.get('/company-work').text
    assert 'Docker reported token owner reassignment.' in detail
    assert 'Source facts and a labeled inference.' in detail
    assert 'Verified result:' in detail and 'none' in detail
    assert client.post('/company-work').status_code==405


def test_operator_decision_and_assignment_require_explicit_local_forms(tmp_path,monkeypatch):
    from swarmtracker_node_ui import app as ui, decision_routes, store
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'profile.db')
    work=CompanyWorkStore(tmp_path/'company-work.db')
    work.create('docker-brief','https://www.docker.com/blog/docker-hub-security-advisory-cve-2026-105445/',
                'Docker reported token owner reassignment.',completed_run())
    client=TestClient(ui.app,base_url='http://127.0.0.1')
    token=decision_routes.TOKEN
    assignment='/company-work/docker-brief/assignment'
    choice='/company-work/docker-brief/decision'
    assert client.post(assignment,data=dict(operator_token=token,task_id='fix-01',assignee='human-01')).status_code==409
    assert client.post(choice,data=dict(operator_token='bad',state='accepted',detail='Review')).status_code==403
    assert client.post(choice,data=dict(operator_token=token,state='accepted',detail='')).status_code==409
    accepted=client.post(choice,data=dict(operator_token=token,state='accepted',detail='Prioritize review.'),follow_redirects=False)
    assert accepted.status_code==303
    assert client.post(choice,data=dict(operator_token=token,state='rejected',detail='Change mind')).status_code==409
    record=CompanyWorkStore(tmp_path/'company-work.db').get('docker-brief')
    assert record['operator_decision']['state']=='accepted'
    assert record['assigned_task'] is None and record['observed_work'] is None and record['verified_result'] is None
    assert client.post(assignment,data=dict(operator_token=token,task_id='fix-01',assignee='human-01'),follow_redirects=False).status_code==303
    assert client.post(assignment,data=dict(operator_token=token,task_id='fix-02',assignee='human-01')).status_code==409
    after=work.get('docker-brief')
    assert after['assigned_task']['task_id']=='fix-01'
    assert after['observed_work'] is after['verified_result'] is None
    assert [x['action'] for x in work.events('docker-brief')]==['operator_decision','task_assigned']
    import subprocess
    from pathlib import Path
    sha=subprocess.check_output(['git','-C',str(Path(__file__).resolve().parents[1]),'rev-parse','HEAD'],text=True).strip()
    assert client.post('/company-work/docker-brief/commit',data=dict(operator_token=token,commit_sha=sha),follow_redirects=False).status_code==303
    assert work.get('docker-brief')['verified_result'] is None
    for path in ('/company-work','/agents','/governance','/sessions'):
        detail=client.get(path).text
        assert 'fix-01' in detail and sha in detail

def test_rejected_proposal_cannot_be_assigned(tmp_path):
    work=CompanyWorkStore(tmp_path/'company-work.db')
    work.create('docker-brief','https://www.docker.com/blog/docker-hub-security-advisory-cve-2026-105445/',
                'Docker reported token owner reassignment.',completed_run())
    work.decide('docker-brief','rejected','No task approved.')
    with pytest.raises(ValueError):work.assign('docker-brief','task-01','human-01')
    assert work.get('docker-brief')['assigned_task'] is None


def test_observed_commit_requires_assignment_and_keeps_result_pending(tmp_path):
    import subprocess
    from pathlib import Path
    work=CompanyWorkStore(tmp_path/'company-work.db')
    work.create('docker-brief','https://www.docker.com/blog/docker-hub-security-advisory-cve-2026-105445/',
                'Docker reported token owner reassignment.',completed_run())
    root=Path(__file__).resolve().parents[1]
    commit=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    with pytest.raises(ValueError):work.observe_commit('docker-brief',commit,root)
    work.decide('docker-brief','accepted','Fixture approval.')
    work.assign('docker-brief','fixture-task','local-test')
    with pytest.raises(ValueError):work.observe_commit('docker-brief','0'*40,root)
    observed=work.observe_commit('docker-brief',commit,root)
    assert observed['observed_work']['commit_sha']==commit
    assert observed['verified_result'] is None
    with pytest.raises(ValueError):work.observe_commit('docker-brief',commit,root)
    assert [x['action'] for x in work.events('docker-brief')]==[
        'operator_decision','task_assigned','commit_observed']


def test_remote_peer_cannot_read_form_token_or_submit_with_loopback_host(tmp_path,monkeypatch):
    from swarmtracker_node_ui import app as ui, decision_routes, store
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'profile.db')
    CompanyWorkStore(tmp_path/'company-work.db').create('docker-brief',
        'https://www.docker.com/blog/docker-hub-security-advisory-cve-2026-105445/',
        'Docker reported token owner reassignment.',completed_run())
    remote=TestClient(ui.app,base_url='http://127.0.0.1',client=('203.0.113.15',43700))
    assert remote.get('/company-work').status_code==403
    assert remote.post('/company-work/docker-brief/decision',
        data=dict(operator_token=decision_routes.TOKEN,state='accepted',detail='Spoofed host')).status_code==403
    assert CompanyWorkStore(tmp_path/'company-work.db').get('docker-brief')['operator_decision']['state']=='pending'


def source_check(work_id='fixture-research'):
    from verity_core.trust import evidence_check as e
    claim='The maintenance window is Monday.'
    return dict(schema=e.SCHEMA,id='check-01',claim=dict(id=work_id+':source-claim',text=claim,sha256=e.digest(claim)),
        sources=[dict(id='notice',reference='https://example.com/notice',revision='1',retrieved_at='2026-10-19T18:00:00Z',text=claim,sha256=e.digest(claim))],
        assessment=dict(outcome='supported',citations=[dict(source_id='notice',start=0,end=len(claim),passage=claim)],reason='Exact quotation present.',unresolved=False),
        method=dict(kind='exact_quote_presence',version='1',checker_model='deterministic',checker_revision='1',request_id='fixture',attempt=1,max_requests=1,latency_ms=0,usage=dict(prompt_tokens=0,completion_tokens=0),billing_source='fixture; no model call'),
        checked_at='2026-10-19T18:01:00Z',context_ref=None,limitations=['Quotation presence does not establish factual truth.'])


def test_evidence_attachment_preserves_authorization_and_reconstructs_after_restart(tmp_path):
    from verity_core.trust import evidence_check as e
    path=tmp_path/'company-work.db';work=CompanyWorkStore(path)
    original=work.create('fixture-research','https://example.com/notice',source_check()['claim']['text'],completed_run())
    check=source_check();bundle=work.attach_check('fixture-research',check)
    anchor=json.loads(json.dumps(bundle['checkpoint']))
    check['assessment']['reason']='Caller mutation'
    restored=CompanyWorkStore(path)
    assert restored.get('fixture-research')==original
    assert e.verify(restored.checks('fixture-research')[0],anchor)
    with pytest.raises(ValueError,match='Duplicate'):restored.attach_check('fixture-research',source_check())
    second=source_check();second['id']='check-02';second['assessment']['outcome']='conflicting'
    with pytest.raises(ValueError):restored.attach_check('fixture-research',second)
    assert len(restored.checks('fixture-research'))==1


@pytest.mark.parametrize('change',['claim_id','claim_text','url','context'])
def test_unlinked_or_unapproved_context_checks_are_refused(tmp_path,change):
    from verity_core.trust import evidence_check as e
    work=CompanyWorkStore(tmp_path/'company-work.db')
    work.create('fixture-research','https://example.com/notice',source_check()['claim']['text'],completed_run())
    check=source_check()
    if change=='claim_id':check['claim']['id']='other:source-claim'
    elif change=='claim_text':
        check['claim']['text']='Another claim';check['claim']['sha256']=e.digest('Another claim')
        check['method']['kind']='model_interpretation'
    elif change=='url':check['sources'][0]['reference']='https://other.example/notice'
    else:check['context_ref']=dict(profile_id='fiction',version=1,sha256='a'*64,approval_status='approved')
    with pytest.raises(ValueError):work.attach_check('fixture-research',check)
    assert work.checks('fixture-research')==[]


def test_evidence_ui_export_and_local_security_boundaries(tmp_path,monkeypatch):
    from swarmtracker_node_ui import app as ui, decision_routes, store
    from verity_core.trust import evidence_check as e
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'profile.db')
    work=CompanyWorkStore(tmp_path/'company-work.db')
    work.create('fixture-research','https://example.com/notice',source_check()['claim']['text'],completed_run())
    client=TestClient(ui.app,base_url='http://127.0.0.1')
    path='/company-work/fixture-research/evidence'
    form=dict(operator_token=decision_routes.TOKEN,check_json=json.dumps(source_check()))
    assert client.post(path,data={**form,'operator_token':'bad'}).status_code==403
    assert client.post(path,data={**form,'extra':'x'}).status_code==422
    assert client.post(path,data={**form,'check_json':'{' }).status_code==422
    assert client.post(path,data={**form,'check_json':'x'*9000}).status_code==413
    assert client.post(path,data=form,headers={'Origin':'https://evil.example'}).status_code==403
    remote=TestClient(ui.app,base_url='http://127.0.0.1',client=('203.0.113.15',40000))
    assert remote.post(path,data=form).status_code==403
    assert remote.get(path+'.json').status_code==403
    assert client.post(path,data=form,follow_redirects=False).status_code==303
    assert client.post(path,data=form).status_code==422
    page=client.get('/company-work')
    assert 'check-01' in page.text and 'supported' in page.text
    assert 'does not prove truth' in page.text and 'pending' in page.text
    export=client.get(path+'.json')
    assert export.headers['cache-control']=='no-store'
    bundle=export.json()['checks'][0]
    assert e.verify(bundle,bundle['checkpoint'])
    assert work.get('fixture-research')['operator_decision']['state']=='pending'
    assert work.get('fixture-research')['verified_result'] is None
    with work._db() as db:
        db.execute("UPDATE research_events SET detail='{}' WHERE action='evidence_check_attached'")
    assert client.get(path+'.json').status_code==503
    assert client.get('/company-work').status_code==503


def test_quotation_form_records_both_outcomes_without_approving_and_escapes_source(tmp_path,monkeypatch):
    from swarmtracker_node_ui import app as ui, decision_routes, store
    from verity_core.trust import evidence_check as e
    monkeypatch.setattr(store,'DB_PATH',tmp_path/'profile.db')
    work=CompanyWorkStore(tmp_path/'company-work.db')
    work.create('fixture-research','https://example.com/notice',source_check()['claim']['text'],completed_run())
    client=TestClient(ui.app,base_url='http://127.0.0.1')
    url='/company-work/fixture-research/quotation'
    form=dict(operator_token=decision_routes.TOKEN,snapshot='Preface '+source_check()['claim']['text']+' <script>alert(1)</script>')
    assert client.post(url,data={**form,'operator_token':'bad'}).status_code==403
    assert client.post(url,data=form,follow_redirects=False).status_code==303
    assert client.post(url,data={**form,'snapshot':'Notice mentions another topic.'},follow_redirects=False).status_code==303
    bundles=work.checks('fixture-research')
    checks=[b['record']['entries'][0]['payload']['check'] for b in bundles]
    assert [c['assessment']['outcome'] for c in checks]==['supported','insufficient']
    assert [c['assessment']['unresolved'] for c in checks]==[False,True]
    assert all(e.verify(b,b['checkpoint']) for b in bundles)
    assert all(c['method']['usage']==dict(prompt_tokens=0,completion_tokens=0) for c in checks)
    assert work.get('fixture-research')['operator_decision']['state']=='pending'
    assert work.get('fixture-research')['verified_result'] is None
    text=client.get('/company-work').text
    assert 'unresolved' in text and '<script>alert(1)</script>' not in text
    assert client.post(url,data={**form,'snapshot':'x'*2001}).status_code==422

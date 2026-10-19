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

"""Acceptance regressions for the controlled simulator and its integrity boundary."""
import copy
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from verity_core.trust import decision, record as trust
from swarmtracker_node_ui import decision_runs as runs, decision_routes as routes
from swarmtracker_node_ui import app as ui


@pytest.fixture
def db(tmp_path):
    return runs.RunStore(tmp_path / 'decisions.db')


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(routes, 'RUNS', db)
    return TestClient(ui.app, base_url='http://127.0.0.1')


def new(db, mode='require_approval'):
    return db.create(runs.synthetic_manifest(mode))


def approve(db, r, choice='approved'):
    return db.authorize(r['id'], choice, 'Reviewed evidence', 'operator', r['head_hash'])


def form(r=None, **extra):
    return dict(operator_token=routes.TOKEN, **({'expected_head': r['head_hash']} if r else {}), **extra)


def test_direct_execute_without_approval_is_denied_and_preserved(db):
    r = new(db)
    out, allowed = db.execute(r['id'], 'operator', r['head_hash'])
    assert not allowed and not out['executed']
    assert out['decision'] == 'pending' and out['attempts'] == 1
    assert out['record']['entries'][-1]['payload']['outcome'] == 'denied'
    assert runs.RunStore(db.path).get(r['id']) == out


def test_rejection_is_final_and_blocks_execution(db):
    r = approve(db, new(db), 'rejected')
    assert not db.execute(r['id'], 'operator', r['head_hash'])[1]
    r = db.get(r['id'])
    with pytest.raises(runs.Conflict):
        approve(db, r)


def test_approval_allows_once_and_retry_is_recorded_denied(db):
    r = approve(db, new(db))
    out, allowed = db.execute(r['id'], 'operator', r['head_hash'])
    assert allowed and out['executed']
    retry, allowed = db.execute(r['id'], 'operator', out['head_hash'])
    assert not allowed and retry['attempts'] == 2
    assert sum(e['payload'].get('outcome') == 'simulated' for e in retry['record']['entries']) == 1


def test_changed_policy_creates_distinct_preserved_runs(db):
    old = approve(db, new(db))
    denied = approve(db, new(db, 'deny'))
    assert db.execute(old['id'], 'operator', old['head_hash'])[1]
    assert not db.execute(denied['id'], 'operator', denied['head_hash'])[1]
    assert db.get(old['id'])['manifest']['policy']['version'] == '1'
    assert db.get(denied['id'])['manifest']['policy']['version'] == '2'


def test_concurrent_attempts_have_only_one_success(db):
    r = approve(db, new(db))
    def call(_):
        try:
            return runs.RunStore(db.path).execute(r['id'], 'operator', r['head_hash'])[1]
        except runs.Conflict:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(call, range(4))).count(True) == 1
    assert db.get(r['id'])['attempts'] == 1


def test_stale_page_cannot_authorize_or_execute(db):
    r = new(db)
    current, _ = db.execute(r['id'], 'operator', r['head_hash'])
    with pytest.raises(runs.Conflict):
        approve(db, r)
    r = approve(db, current)
    with pytest.raises(runs.Conflict):
        db.execute(r['id'], 'operator', current['head_hash'])


def test_failed_audit_write_rolls_back_simulation_and_approval(db, monkeypatch):
    r = new(db)
    def fail(*_): raise sqlite3.OperationalError('disk full')
    with monkeypatch.context() as m:
        m.setattr(db, '_save', fail)
        with pytest.raises(sqlite3.Error): approve(db, r)
    assert db.get(r['id']) == r
    r = approve(db, r)
    with monkeypatch.context() as m:
        m.setattr(db, '_save', fail)
        with pytest.raises(sqlite3.Error): db.execute(r['id'], 'operator', r['head_hash'])
    assert db.get(r['id']) == r


def test_corrupt_record_prevents_execution_and_export(db):
    r = new(db)
    record = r['record']
    record['entries'][0]['payload']['manifest']['recommendation'] = 'changed'
    with sqlite3.connect(db.path) as con:
        con.execute('UPDATE decision_runs SET record=?', (json.dumps(record),))
    with pytest.raises(ValueError): db.execute(r['id'], 'operator', r['head_hash'])
    assert db.recent()[0]['integrity_error']


@pytest.mark.parametrize('mutation', ['evidence', 'finding', 'policy', 'subject', 'head', 'reorder', 'drop'])
def test_export_tampering_is_detected(db, mutation):
    r = approve(db, new(db))
    r, _ = db.execute(r['id'], 'operator', r['head_hash'])
    bundle = decision.export(r['record'])
    m = bundle['record']['entries'][0]['payload']['manifest']
    if mutation == 'evidence': m['evidence'][0]['text'] += '!'
    if mutation == 'finding': m['findings'][0]['statement'] += '!'
    if mutation == 'policy': m['policy']['mode'] = 'deny'
    if mutation == 'subject': bundle['record']['subject']['identifier'] = 'changed'
    if mutation == 'head': bundle['checkpoint']['head_hash'] = '0' * 64
    if mutation == 'reorder': bundle['record']['entries'][1:3] = reversed(bundle['record']['entries'][1:3])
    if mutation == 'drop': bundle['record']['entries'].pop()
    assert not decision.verify(bundle)['verified']


def test_retained_anchor_detects_validly_rewritten_chain(db):
    r = new(db)
    anchor = decision.checkpoint(r['record'])
    altered = runs.synthetic_manifest()
    altered['recommendation'] = 'A different recommendation'
    rewritten = decision.export(decision.new_run(altered))
    assert decision.verify(rewritten)['verified']
    assert not decision.verify(rewritten, anchor)['verified']


def test_semantically_invalid_but_rehashed_chain_is_refused(db):
    r = new(db)['record']
    attempt = trust.append_entry(r, 'action_attempted', payload={
        'event': 'action_attempted', 'allowed': True,
        'binding': decision.binding(r['entries'][0]['payload']['manifest'])})
    trust.append_entry(r, 'action_result', payload={
        'event': 'action_result', 'attempt_hash': attempt['entry_hash'],
        'outcome': 'simulated', 'external_action_executed': False})
    assert trust.chain_valid(r)
    with pytest.raises(ValueError): decision.inspect(r)


def test_portable_verifier_subprocess_with_retained_anchor(db, tmp_path):
    r = approve(db, new(db))
    r, _ = db.execute(r['id'], 'operator', r['head_hash'])
    bundle = tmp_path / 'run.json'
    anchor = tmp_path / 'anchor.json'
    bundle.write_text(json.dumps(decision.export(r['record'])))
    anchor.write_text(json.dumps(decision.checkpoint(r['record'])))
    p = subprocess.run([sys.executable, '-m', 'verity_core.trust.verify_decision', str(bundle), '--checkpoint', str(anchor)], capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)['anchored']
    obj = json.loads(bundle.read_text())
    obj['record']['entries'].pop()
    bundle.write_text(json.dumps(obj))
    p = subprocess.run([sys.executable, '-m', 'verity_core.trust.verify_decision', str(bundle), '--checkpoint', str(anchor)], capture_output=True, text=True)
    assert p.returncode == 1


def test_http_approval_denial_export_and_agent_links(client, db, monkeypatch):
    response = client.post('/decisions/demo', data=form(), follow_redirects=False)
    assert response.status_code == 303
    path = response.headers['location']
    r = db.get(path.split('/')[-1])
    denied = client.post(path + '/execute', data=form(r))
    assert denied.status_code == 403
    assert 'Execution denied by the backend' in denied.text
    r = db.get(r['id'])
    assert client.post(path + '/authorization', data=form(r, decision='approved', reason='Reviewed')).status_code == 200
    r = db.get(r['id'])
    assert client.post(path + '/execute', data=form(r)).status_code == 200
    bundle = client.get(path + '/export.json').json()
    assert decision.verify(bundle)['verified']
    monkeypatch.setattr(ui.agents, 'observe', lambda: ui.agents.participant_snapshot({}, [], []))
    assert path in client.get('/agents').text
    assert path in client.get('/governance').text
    assert 'controlled-simulation' in client.get('/governance?run=' + r['id']).text


@pytest.mark.parametrize('headers,data', [
    ({}, {"decision": "approved"}), ({'Origin': 'https://evil.example'}, {'operator_token': 'valid'}),
    ({'Sec-Fetch-Site': 'cross-site'}, {'operator_token': 'valid'}),
    ({'Host': 'evil.example'}, {'operator_token': 'valid'})])
def test_cross_site_or_missing_intent_cannot_mutate(client, db, headers, data):
    data = dict(data)
    if data.get('operator_token') == 'valid': data['operator_token'] = routes.TOKEN
    assert client.post('/decisions/demo', data=data, headers=headers).status_code == 403
    assert db.recent() == []


def test_malformed_and_oversized_forms_cannot_mutate(client, db):
    body = 'operator_token=' + routes.TOKEN + '&operator_token=' + routes.TOKEN
    assert client.post('/decisions/demo', content=body, headers={'Content-Type': 'application/x-www-form-urlencoded'}).status_code == 400
    assert client.post('/decisions/demo', content='x' * 8193).status_code == 413
    assert client.post('/decisions/demo', json={'operator_token': routes.TOKEN}).status_code == 415
    assert db.recent() == []


def test_render_escapes_evidence_and_actor_metadata(client, db):
    m = runs.synthetic_manifest()
    m['title'] = '<script>bad()</script>'
    m['evidence'][0] = runs.evidence('message', '<script>bad()</script>', '<script>bad()</script>')
    r = db.create(m)
    html = client.get('/decisions/' + r['id']).text
    assert '<script>bad()</script>' not in html
    assert '&lt;script&gt;bad()&lt;/script&gt;' in html


def test_corruption_returns_503_for_export_and_mutation(client, db):
    r = new(db)
    with sqlite3.connect(db.path) as con:
        con.execute('UPDATE decision_runs SET record=?', ('{}',))
    assert client.get('/decisions/' + r['id'] + '/export.json').status_code == 503
    assert client.post('/decisions/' + r['id'] + '/execute', data=form(r)).status_code == 503


def test_analysis_snapshot_persists_actual_local_finding_without_fake_delegation(client, db, monkeypatch):
    job = {'report_id': 'test-analysis', 'items': [{'text': 'actual supplied text', 'label': 'input'}],
           'report': {'fused_verdict': 'Needs more evidence'},
           'decision_participant': {'id': 'runtime-test', 'name': 'Reported node',
               'model': 'test-model', 'framework': 'OpenClaw',
               'identity_provenance': 'self-reported', 'delegated_by': None}}
    monkeypatch.setitem(ui.GENERIC_JOBS, 'test-analysis', job)
    response = client.post('/decisions/from-report/test-analysis', data=form(), follow_redirects=False)
    assert response.status_code == 303
    rid = response.headers['location'].split('/')[-1]
    ui.GENERIC_JOBS.pop('test-analysis')
    r = runs.RunStore(db.path).get(rid)
    assert r['manifest']['source_kind'] == 'analysis_snapshot'
    assert len(r['manifest']['participants']) == 1
    assert not r['manifest']['findings'][0]['conflicting']
    assert r['manifest']['evidence'][0]['text'] == 'actual supplied text'
    assert client.get(response.headers['location']).status_code == 200


def test_actual_single_intake_can_be_preserved(client, db, monkeypatch):
    monkeypatch.setattr(ui.oc, 'generate', lambda *a, **kw: {
        'ok': True, 'response': json.dumps({'risk_level': 'low', 'confidence': 20,
            'verdict': 'Unconfirmed payment request', 'signals': [], 'recommended_action': 'Corroborate'}),
        'node': 'observed-node', 'model': 'observed-model', 'provider': 'test-runtime',
        'openclaw_event_id': 'actual-intake-regression'})
    monkeypatch.setattr(ui.oc, 'healthz', lambda: {})
    monkeypatch.setattr(ui.oc, 'ollama_models', lambda: [])
    response = client.post('/analyze', data={'evidence': 'Synthetic evidence passed through the actual intake route.'})
    assert response.status_code == 200 and 'Preserve for decision review' in response.text
    path = '/decisions/from-report/actual-intake-regression'
    assert client.get(path).status_code == 200
    response = client.post(path, data=form(), follow_redirects=False)
    r = db.get(response.headers['location'].split('/')[-1])
    assert r['manifest']['participants'][0]['name'] == 'observed-node'
    assert r['manifest']['participants'][0]['model'] == 'observed-model'
    assert r['manifest']['participants'][0]['runtime_event_id'] == 'actual-intake-regression'
    assert r['manifest']['findings'][0]['statement'] == 'Unconfirmed payment request'


@pytest.mark.parametrize('choice,mode', [('rejected','require_approval'), ('approved','deny')])
def test_http_rejection_and_policy_denial_are_enforced(client, db, choice, mode):
    r = approve(db, new(db, mode), choice)
    path = '/decisions/' + r['id']
    assert client.post(path + '/execute', data=form(r)).status_code == 403
    r = db.get(r['id'])
    assert not r['executed']
    assert decision.verify(client.get(path + '/export.json').json())['verified']


def test_unknown_run_and_same_origin_form(client):
    assert client.get('/decisions/not-a-run').status_code == 404
    response = client.post('/decisions/demo', data=form(), headers={'Origin': 'http://127.0.0.1'}, follow_redirects=False)
    assert response.status_code == 303


def test_bad_manifest_and_semantics_are_rejected(db):
    for field, value in [('participants',[None]), ('evidence',[None]), ('findings',[None]), ('policy',None)]:
        manifest = runs.synthetic_manifest()
        manifest[field] = value
        with pytest.raises(ValueError): db.create(manifest)
    manifest = runs.synthetic_manifest()
    manifest['findings'][0]['evidence_ids'] = {'message': 'not a list'}
    with pytest.raises(ValueError): db.create(manifest)

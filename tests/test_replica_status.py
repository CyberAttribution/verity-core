import json
from swarmtracker_node_ui.replica_status import summarize


def write(root,state,results=None):
    (root/'replica0-status.json').write_text(json.dumps(state))
    if results is not None:
        folder=root/'reviews-replica0';folder.mkdir(exist_ok=True);(folder/'results.json').write_text(json.dumps(results))


def state(**kw):
    return dict(replica=0,phase='substantive inference',actual_model='glm-5.3-flash',current_task='next',last_progress=1000,queue_depth=2,**kw)


def test_last_result_survives_next_task_and_never_means_accepted(tmp_path):
    write(tmp_path,state(),[dict(task_id='prior',state='awaiting_independent_review',finish_reason='stop',seconds=8)])
    s=summarize(tmp_path,0,now=1010)
    assert s['current_task']=='next' and s['last_result']['task_id']=='prior'
    assert s['last_result']['state']=='awaiting_independent_review' and s['automatic_acceptance'] is False


def test_stale_worker_does_not_claim_live_task_or_queue_exhaustion(tmp_path):
    value=state();value.update(phase='prepared queue exhausted',current_task=None,queue_depth=0);write(tmp_path,value)
    s=summarize(tmp_path,0,now=1201)
    assert s['telemetry']=='stale' and s['phase']=='unknown' and s['queue_depth'] is None
    assert s['reported_queue_depth']==0


def test_invalid_or_missing_sources_are_unknown(tmp_path):
    assert summarize(tmp_path,0,now=1010)['telemetry']=='unknown'
    for value in [dict(state(),replica=1),dict(state(),queue_depth=-1),dict(state(),last_progress=1100),dict(state(),current_task=None)]:
        write(tmp_path,value);assert summarize(tmp_path,0,now=1010)['telemetry']=='unknown'
    (tmp_path/'replica0-status.json').write_bytes(b'x'*131073)
    assert summarize(tmp_path,0,now=1010)['phase']=='unknown'


def test_token_limited_response_remains_incomplete(tmp_path):
    write(tmp_path,state(),[dict(task_id='partial',state='incomplete',finish_reason='length')])
    s=summarize(tmp_path,0,now=1010)
    assert s['last_result']['state']=='incomplete' and s['last_result']['finish_reason']=='length'


def test_sessions_only_reads_replica_root_for_exact_configured_session(tmp_path,monkeypatch):
    from swarmtracker_node_ui.sessions import configured_view
    write(tmp_path,state())
    monkeypatch.setenv('NODE_UI_REPLICA_STATUS_ROOT',str(tmp_path))
    monkeypatch.setenv('NODE_UI_REPLICA_STATUS_SESSION_ID','target')
    assert configured_view(dict(identity=dict(id='other'),events=[]))['replicas']==[]
    view=configured_view(dict(identity=dict(id='target'),events=[]))
    assert len(view['replicas'])==2
    assert view['replicas'][0]['telemetry']=='stale' and view['replicas'][1]['telemetry']=='unknown'


def test_malformed_identity_and_phase_fields_are_not_live_reports(tmp_path):
    for altered in [dict(state(),phase=123),dict(state(),phase=True),dict(state(),current_task={'injected':'task'}),dict(state(),current_task=123),dict(state(),actual_model=['model']),dict(state(),replica=False)]:
        write(tmp_path,altered)
        result=summarize(tmp_path,0,now=1010)
        assert result['telemetry']=='unknown' and result['current_task'] is None


def test_rejected_report_does_not_retain_its_model_or_audit_claims(tmp_path):
    write(tmp_path,dict(state(low_fields_audited=True,template_low_verified=True),queue_depth=-1))
    result=summarize(tmp_path,0,now=1010)
    assert result['telemetry']=='unknown' and result['actual_model'] is None and result['last_progress'] is None
    assert result['low_fields_audited'] is None and result['last_result'] is None

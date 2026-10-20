"""Regressions for imported history and simultaneous billable work."""
from swarmtracker_node_ui.sessions import SessionStore,derive

IDENTITY=dict(id='freshness',purpose='Synthetic activity evidence',provider='Lambda',macs=['test'],branch='test',commits=[])
NOW=1792540200.

def observation(eid,kind,data,at=NOW,received=NOW,source='local_collector'):
    return dict(id=eid,kind=kind,source=source,at=at,received=received,data=data)

def view(*events,now=NOW):
    return derive(dict(identity=IDENTITY,events=list(events)),now=now)

def task(name):
    return observation('task-'+name,'task',dict(task_id=name,title=name,scope='gpu',state='running'))

def request(name,at=NOW):
    return observation('request-'+name,'request',dict(task_id=name,state='running'),at=at)

def result(name):
    return observation('result-'+name,'result',dict(task_id=name,state='completed',finish_reason='stop'),source='model_server')

def test_importing_old_history_does_not_refresh_service_or_request(tmp_path):
    db=SessionStore(tmp_path/'sessions.db');db.create(IDENTITY)
    for entry in [
        observation('provider','provider',dict(state='running'),at=NOW-3600),
        observation('model','model',dict(state='ready'),at=NOW-3600),
        task('old'),request('old',at=NOW-3600)]:
        entry={k:v for k,v in entry.items() if k!='received'}
        db.append(IDENTITY['id'],entry)
    record=SessionStore(db.path).get(IDENTITY['id'])
    snapshot=derive(record,now=max(e['received'] for e in record['events'])+1)
    assert snapshot['stale'] and snapshot['provider']=='unknown'
    assert snapshot['model']=='unknown' and snapshot['activity']!='active'

def test_fresh_contact_does_not_refresh_old_model_provider_or_gpu():
    old=NOW-3600
    snapshot=view(
        observation('p','provider',dict(state='running'),at=old,received=old),
        observation('m','model',dict(state='ready'),at=old,received=old),
        observation('g','gpu',dict(index=0,uuid='GPU-test',utilization_pct=0,memory_used_mib=10,memory_total_mib=100),at=old),
        observation('contact','contact',dict(state='reachable')))
    assert not snapshot['stale']
    assert snapshot['provider']=='unknown' and snapshot['model']=='unknown'
    assert snapshot['gpu_samples'][0]['state']=='stale'

def test_unrelated_result_cannot_resurrect_completed_request():
    snapshot=view(task('a'),request('a',at=NOW-2),
        observation('a-done','result',dict(task_id='a',state='completed',finish_reason='stop'),at=NOW-1,received=NOW-1,source='model_server'),
        result('b'))
    assert snapshot['activity']!='active'

def test_other_completed_request_does_not_hide_parallel_activity():
    snapshot=view(task('a'),request('a'),task('b'),request('b'),result('b'))
    assert snapshot['activity']=='active'
    assert view(task('a'),request('a'),task('b'),request('b'),result('b'),result('a'))['activity']!='active'

def test_equal_timestamps_use_retained_event_order():
    assert view(task('a'),request('a'),result('a'))['activity']!='active'
    assert view(task('a'),result('a'),request('a'))['activity']=='active'

def test_source_timestamp_bounds_request_activity_even_with_fresh_contact():
    snapshot=view(task('a'),request('a',at=NOW-3600),
        observation('contact','contact',dict(state='reachable')))
    assert not snapshot['stale'] and snapshot['activity']!='active'

def test_provider_confirmation_and_estimate_survive_old_history():
    snapshot=view(
        observation('billing','billing',dict(rate=22.32,estimate_start_proxy=NOW-3600),at=NOW-3600),
        observation('stop','shutdown',dict(confirmed=True),at=NOW-1800,source='provider_console'))
    assert snapshot['termination_confirmed'] and snapshot['provider']=='terminated'
    assert snapshot['estimated_charge']==11.16

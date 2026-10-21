import copy
import json
import sqlite3
import pytest
from swarmtracker_node_ui.sessions import SessionStore,derive
from swarmtracker_node_ui.session_snapshot import import_snapshot,observation_events
from tools.session_snapshot_import import once
from swarmtracker_node_ui.session_alerts import AlertJournal

NOW=1792542300.


@pytest.fixture
def db(tmp_path):
    store=SessionStore(tmp_path/'sessions.db')
    store.create(dict(id='session',purpose='Synthetic source bridge',provider='Lambda',macs=['test'],branch='test',commits=[]))
    return store


def sample(at=NOW,running=0):
    service=dict(state='ready',models=['synthetic-model'],running_requests=running,waiting_requests=0)
    return dict(at=at,setup=dict(at=at,replicas={str(i):dict(service=copy.deepcopy(service),current_task='untrusted-marker') for i in [0,1]}))


def test_fresh_services_update_observations_without_task_billing_or_termination_authority(db):
    result=import_snapshot(db,'session',sample(),now=NOW)
    assert result['imported']==4
    record=db.get('session');view=derive(record,now=NOW)
    assert view['model']=='ready' and view['connection']=='connected'
    assert view['inventory']['models']==['synthetic-model']
    assert view['provider']=='unknown' and not view['termination_confirmed']
    assert view['activity']!='active' and view['tasks']==[] and view['reported_charge'] is None
    assert {e['kind'] for e in record['events']}=={'contact','connection','inventory','model'}


def test_running_counter_does_not_promote_worker_marker_to_assigned_task(db):
    import_snapshot(db,'session',sample(running=1),now=NOW)
    view=derive(db.get('session'),now=NOW)
    assert view['model']=='inference' and view['activity']!='active'
    assert not view['tasks']


def test_replay_and_late_import_preserve_source_age(db):
    original=sample(NOW-601)
    import_snapshot(db,'session',original,now=NOW)
    assert not import_snapshot(SessionStore(db.path),'session',original,now=NOW+1)['imported']
    view=derive(db.get('session'),now=NOW+1)
    assert view['stale'] and view['model']=='unknown'
    assert len(db.get('session')['events'])==4


@pytest.mark.parametrize('change',[
    lambda s:s['setup']['replicas']['1'].update(service=None),
    lambda s:s['setup']['replicas']['1']['service'].update(running_requests=True),
    lambda s:s['setup']['replicas']['1']['service'].update(models=[]),
])
def test_missing_or_malformed_replica_never_becomes_ready(db,change):
    snapshot=sample();change(snapshot)
    import_snapshot(db,'session',snapshot,now=NOW)
    assert derive(db.get('session'),now=NOW)['model']=='unknown'
    assert not derive(db.get('session'),now=NOW)['inventory']['checked']


def test_future_clock_and_invalid_stamp_fail_before_any_append(db):
    for snapshot in [sample(NOW+100),dict(at=True),dict(at=float('nan')),dict(at=NOW,setup=dict(at=NOW+100))]:
        with pytest.raises(ValueError):import_snapshot(db,'session',snapshot,now=NOW)
    assert db.get('session')['events']==[]


@pytest.mark.parametrize("fail_at",[3,4])
def test_partial_import_restarts_without_duplicate_events_or_old_sample_override(db,monkeypatch,fail_at):
    original=db.append;count=0
    def interrupted(*args):
        nonlocal count
        count+=1
        if count==fail_at:raise sqlite3.OperationalError('synthetic')
        return original(*args)
    monkeypatch.setattr(db,'append',interrupted)
    with pytest.raises(sqlite3.OperationalError):import_snapshot(db,'session',sample(),now=NOW)
    reopened=SessionStore(db.path)
    assert import_snapshot(reopened,'session',sample(),now=NOW)['imported']>0
    assert len(reopened.get('session')['events'])==4
    newer=sample(NOW+1)
    import_snapshot(reopened,'session',newer,now=NOW+1)
    assert import_snapshot(reopened,'session',sample(),now=NOW+2)['state']=='older source snapshot ignored'
    conflict=sample(NOW+1,running=1)
    with pytest.raises(ValueError,match='Conflicting'):import_snapshot(reopened,'session',conflict,now=NOW+2)


def test_provider_confirmed_session_is_not_reopened_by_guest_snapshot(db):
    db.append('session',dict(id='end',at=NOW-1,kind='shutdown',source='provider_console',data=dict(confirmed=True)))
    assert not import_snapshot(db,'session',sample(),now=NOW)['imported']
    assert len(db.get('session')['events'])==1 and derive(db.get('session'),now=NOW)['termination_confirmed']


def test_malformed_file_preserves_warning_history_and_projects_no_raw_fields(db,tmp_path):
    path=tmp_path/'snapshot.json';journal=AlertJournal(tmp_path/'session-alerts.db')
    journal.evaluate('session',['prior warning'],NOW-1)
    path.write_text('{')
    with pytest.raises(ValueError):once(db,'session',path,now=NOW)
    assert journal.history('session')[0]['cleared_at'] is None
    snapshot=sample();snapshot['irrelevant_private_field']='SYNTHETIC_PRIVATE_SPECIMEN'
    path.write_text(json.dumps(snapshot));once(db,'session',path,now=NOW)
    assert 'SYNTHETIC_PRIVATE_SPECIMEN' not in json.dumps(db.get('session'))
    assert not any(e['data'].get('task_id') for e in db.get('session')['events'])


def test_concurrent_old_import_cannot_overwrite_newer_service_state(db,monkeypatch):
    import threading
    held=threading.Event();release=threading.Event();errors=[]
    original=db.get
    def suspended_read(session_id):
        record=original(session_id);held.set()
        assert release.wait(3)
        return record
    monkeypatch.setattr(db,'get',suspended_read)
    def old_import():
        try:import_snapshot(db,'session',sample(),now=NOW)
        except Exception as error:errors.append(type(error).__name__)
    thread=threading.Thread(target=old_import);thread.start();assert held.wait(3)
    retry=False
    try:
        try:import_snapshot(SessionStore(db.path),'session',sample(NOW+1,running=1),now=NOW+1)
        except BlockingIOError:retry=True
    finally:
        release.set();thread.join(4)
    assert not errors
    if retry:import_snapshot(SessionStore(db.path),'session',sample(NOW+1,running=1),now=NOW+1)
    assert derive(SessionStore(db.path).get('session'),now=NOW+2)['model']=='inference'


def test_new_ssh_contact_with_regressed_probe_timestamp_preserves_newer_model_fact(db):
    import_snapshot(db,'session',sample(),now=NOW)
    regressed=sample(NOW+1)
    regressed['setup']['at']=NOW-500
    for replica in regressed['setup']['replicas'].values():
        replica['service']['models']=['older-model']
    import_snapshot(db,'session',regressed,now=NOW+1)
    view=derive(db.get('session'),now=NOW+1)
    assert view['connection']=='connected' and view['model']=='ready'
    assert view['inventory']['models']==['synthetic-model']
    assert view['model_details']['name']=='synthetic-model'
    # Continued guest contact cannot renew the retained model fact's age.
    regressed['at']=NOW+301
    import_snapshot(db,'session',regressed,now=NOW+301)
    view=derive(db.get('session'),now=NOW+301)
    assert not view['stale'] and view['model']=='unknown'

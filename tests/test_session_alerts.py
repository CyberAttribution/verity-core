import json
import sqlite3
import threading
import pytest
from swarmtracker_node_ui.session_alerts import AlertJournal
from swarmtracker_node_ui.sessions import SessionStore
from tools.session_collector import collect_once

NOW=1792541600.
IDENTITY=dict(id='alerts',purpose='Synthetic retained warnings',provider='Lambda',macs=['test'],branch='test',commits=[])


def test_restart_replay_clear_and_recurrence_keep_separate_episodes(tmp_path):
    path=tmp_path/'alerts.db';journal=AlertJournal(path)
    assert journal.evaluate('session',['idle','stale','idle'],NOW)
    assert not AlertJournal(path).evaluate('session',['stale','idle'],NOW)
    assert len(journal.history('session'))==2
    journal.evaluate('session',['idle'],NOW+1)
    cleared=next(e for e in journal.history('session') if e['warning']=='stale')
    assert cleared['cleared_at']==NOW+1 and cleared['last_seen']==NOW
    journal.evaluate('session',['stale','idle'],NOW+2)
    episodes=journal.history('session')
    assert len(episodes)==3 and sum(e['warning']=='stale' and e['cleared_at'] is None for e in episodes)==1
    assert all(e['availability']=='local record only; delivery not verified' for e in episodes)
    assert journal.history('different')==[]


def test_stale_conflicting_or_invalid_snapshot_cannot_clear_current_warnings(tmp_path):
    journal=AlertJournal(tmp_path/'alerts.db');journal.evaluate('session',['idle'],NOW)
    for at,warnings in [(NOW-1,[]),(NOW,[]),(float('nan'),[]),(NOW+1,['bad\nmessage']),(True,[])]:
        with pytest.raises(ValueError):journal.evaluate('session',warnings,at)
    episode=journal.history('session')[0]
    assert episode['cleared_at'] is None and episode['last_seen']==NOW


def test_concurrent_same_evaluation_opens_only_one_episode(tmp_path):
    path=tmp_path/'alerts.db';AlertJournal(path);barrier=threading.Barrier(2);out=[]
    def evaluate():
        journal=AlertJournal(path);barrier.wait()
        out.append(journal.evaluate('session',['idle'],NOW))
    threads=[threading.Thread(target=evaluate) for _ in range(2)]
    for t in threads:t.start()
    for t in threads:t.join(3)
    assert sorted(out)==[False,True]
    assert len(AlertJournal(path).history('session'))==1


def test_collector_restart_retains_warning_and_failed_read_does_not_clear(tmp_path,monkeypatch):
    db=SessionStore(tmp_path/'sessions.db');db.create(IDENTITY)
    inbox=tmp_path/'inbox';inbox.mkdir()
    collect_once(inbox,db,now=NOW)
    path=tmp_path/'session-alerts.db';prior=AlertJournal(path).history('alerts')
    assert any(e['warning']=='Contact stale or unknown' for e in prior)
    collect_once(inbox,SessionStore(db.path),now=NOW+1)
    assert len(AlertJournal(path).history('alerts'))==len(prior)
    def unavailable(*args):raise sqlite3.OperationalError('synthetic')
    monkeypatch.setattr(db,'get',unavailable)
    notices=collect_once(inbox,db,now=NOW+2)
    assert any('evaluation unavailable' in n for n in notices)
    assert all(e['cleared_at'] is None for e in AlertJournal(path).history('alerts'))


def test_busy_inbox_append_preserves_file_for_later_import(tmp_path,monkeypatch):
    db=SessionStore(tmp_path/'sessions.db');db.create(IDENTITY)
    inbox=tmp_path/'inbox';inbox.mkdir();pending=inbox/'contact.json'
    payload=dict(session_id='alerts',event=dict(id='contact',kind='contact',source='local_collector',at=NOW,data=dict(state='reachable')))
    pending.write_text(json.dumps(payload))
    original=db.append
    def busy(*args):raise sqlite3.OperationalError('synthetic')
    monkeypatch.setattr(db,'append',busy)
    notices=collect_once(inbox,db,now=NOW)
    assert pending.exists() and not pending.with_suffix('.seen').exists()
    assert any('Rejected inbox file' in n for n in notices)
    monkeypatch.setattr(db,'append',original)
    collect_once(inbox,db,now=NOW+1)
    assert pending.with_suffix('.seen').exists()
    assert len(db.get('alerts')['events'])==1


def test_journal_transaction_rolls_back_partially_updated_episode(tmp_path):
    path=tmp_path/'alerts.db';journal=AlertJournal(path);journal.evaluate('session',['idle'],NOW)
    with journal.db() as db:
        db.execute("CREATE TRIGGER fail_new_warning BEFORE INSERT ON alert_episodes WHEN NEW.warning='failure' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(sqlite3.IntegrityError):journal.evaluate('session',['idle','failure'],NOW+1)
    episode=journal.history('session')[0]
    assert episode['last_seen']==NOW and episode['cleared_at'] is None
    assert not journal.evaluate('session',['idle'],NOW)


def test_failed_inbox_import_does_not_close_prior_warning_when_old_contact_expires(tmp_path,monkeypatch):
    db=SessionStore(tmp_path/'sessions.db');db.create(IDENTITY)
    for eid,kind,data in [('provider','provider',dict(state='running')),('inventory','inventory',dict(checked=True,models=[]))]:
        db.append('alerts',dict(id=eid,kind=kind,source='local_collector',at=NOW,data=data))
    inbox=tmp_path/'inbox';inbox.mkdir()
    collect_once(inbox,db,now=NOW)
    journal=AlertJournal(tmp_path/'session-alerts.db')
    prior=next(e for e in journal.history('alerts') if 'successful inventory' in e['warning'])
    pending=inbox/'new-contact.json'
    pending.write_text(json.dumps(dict(session_id='alerts',event=dict(id='new-contact',kind='contact',source='local_collector',at=NOW+301,data=dict(state='reachable')))))
    def busy(*args):raise sqlite3.OperationalError('synthetic')
    monkeypatch.setattr(db,'append',busy)
    collect_once(inbox,db,now=NOW+301)
    preserved=next(e for e in journal.history('alerts') if e['id']==prior['id'])
    assert preserved['cleared_at'] is None
    assert pending.exists()


def test_unattributed_malformed_inbox_preserves_history_without_crashing(tmp_path):
    db=SessionStore(tmp_path/'sessions.db');db.create(IDENTITY)
    inbox=tmp_path/'inbox';inbox.mkdir()
    collect_once(inbox,db,now=NOW)
    journal=AlertJournal(tmp_path/'session-alerts.db');before=journal.history('alerts')
    (inbox/'malformed.json').write_text('[{}]')
    notices=collect_once(inbox,db,now=NOW+1)
    assert any('Rejected inbox file' in n for n in notices)
    assert journal.history('alerts')==before


def test_inbox_metadata_read_failure_is_reported_and_history_preserved(tmp_path,monkeypatch):
    from pathlib import Path
    db=SessionStore(tmp_path/'sessions.db');db.create(IDENTITY)
    inbox=tmp_path/'inbox';inbox.mkdir()
    collect_once(inbox,db,now=NOW)
    journal=AlertJournal(tmp_path/'session-alerts.db');before=journal.history('alerts')
    pending=inbox/'contact.json';pending.write_text('{}')
    original=Path.stat
    def unavailable(self,*args,**kwargs):
        if self==pending:raise PermissionError('synthetic')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',unavailable)
    notices=collect_once(inbox,db,now=NOW+1)
    assert any('PermissionError' in n for n in notices)
    assert journal.history('alerts')==before


def test_known_session_failure_does_not_block_other_session_warnings(tmp_path,monkeypatch):
    db=SessionStore(tmp_path/'sessions.db');db.create(IDENTITY);db.create(dict(IDENTITY,id='other'))
    inbox=tmp_path/'inbox';inbox.mkdir()
    pending=inbox/'failed.json'
    pending.write_text(json.dumps(dict(session_id='alerts',event=dict(id='contact',at=NOW,kind='contact',source='local_collector',data=dict(state='reachable')))))
    original=db.append
    def busy(sid,event):
        if sid=='alerts':raise sqlite3.OperationalError('synthetic')
        return original(sid,event)
    monkeypatch.setattr(db,'append',busy)
    collect_once(inbox,db,now=NOW)
    journal=AlertJournal(tmp_path/'session-alerts.db')
    assert journal.history('alerts')==[]
    assert journal.history('other')[0]['warning']=='Contact stale or unknown'

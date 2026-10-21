import json
import sqlite3
import threading
import uuid
import pytest
from swarmtracker_node_ui.credential_manager import CredentialManager,CredentialConflict
from swarmtracker_node_ui.credentials import CredentialUnavailable
from swarmtracker_node_ui.customer_config import CustomerConfig


class Native:
    def __init__(self):self.profile=str(uuid.uuid4());self.values={};self.calls=[];self.mode='ok'
    def create(self,*,provider,endpoint,secret,reference):
        self.calls.append(('create',reference));self.values[reference]=(provider,endpoint,secret)
        if self.mode=='timeout':raise CredentialUnavailable('Synthetic timeout',outcome_unknown=True)
        if self.mode=='crash':raise KeyboardInterrupt()
        return reference
    def read(self,reference,*,provider,endpoint):
        self.calls.append(('read',reference));p,e,s=self.values[reference]
        assert (p,e)==(provider,endpoint)
        return s
    def delete(self,reference):self.calls.append(('delete',reference));self.values.pop(reference,None)


@pytest.fixture
def env(tmp_path):
    c=CustomerConfig(tmp_path/'customer-config.db')
    entry=c.save('connection','external',dict(provider='openai',endpoint='https://api.openai.com',model='operator-selected',revision='',scope='external',credential_ref='none'))
    native=Native();manager=CredentialManager(tmp_path/'credentials.db',c,native)
    return c,entry,native,manager


def test_lifecycle_durable_binding_no_secret_in_metadata(env):
    c,e,n,m=env;ref=m.create('external',expected_digest=e['digest'],secret='synthetic-sensitive-token')
    reopened=CredentialManager(m.path,CustomerConfig(c.path),n)
    assert reopened.resolve('external',expected_digest=e['digest'])=='synthetic-sensitive-token'
    assert reopened.profile==m.profile
    assert 'synthetic-sensitive-token' not in m.path.read_bytes().decode('utf-8',errors='ignore')
    assert 'synthetic-sensitive-token' not in json.dumps(c.all())
    reopened.remove(ref,expected_digest=e['digest']);assert not n.values
    assert [x['state'] for x in reopened.history(ref)]==['pending','stored','removing','removed']
    with pytest.raises(CredentialConflict):reopened.resolve('external',expected_digest=e['digest'])


@pytest.mark.parametrize('mode,state',[('timeout','unknown'),('crash','pending')])
def test_uncertain_or_crashed_create_preserves_reference_and_prevents_retry(env,mode,state):
    c,e,n,m=env;n.mode=mode
    with pytest.raises((CredentialUnavailable,KeyboardInterrupt)):m.create('external',expected_digest=e['digest'],secret='synthetic')
    reopened=CredentialManager(m.path,c,n);row=reopened.records()[0]
    assert row['state']==state and row['reference'] in n.values
    with pytest.raises(CredentialConflict):reopened.create('external',expected_digest=e['digest'],secret='other')
    with pytest.raises(CredentialConflict):reopened.resolve('external',expected_digest=e['digest'])
    assert len(n.calls)==1
    reopened.remove(row['reference'],expected_digest=e['digest']);assert not n.values


def test_changed_or_disabled_connection_blocks_read_but_allows_cleanup(env):
    c,e,n,m=env;ref=m.create('external',expected_digest=e['digest'],secret='synthetic')
    newer=c.save('connection','external',dict(e['body'],endpoint='https://other.example'))
    with pytest.raises(CredentialConflict):m.resolve('external',expected_digest=e['digest'])
    with pytest.raises(CredentialConflict):m.resolve('external',expected_digest=newer['digest'])
    c.disable_connection('external')
    m.remove(ref,expected_digest=newer['digest'])
    assert [x[0] for x in n.calls]==['create','delete'] and not n.values


def test_concurrent_creates_dispatch_only_once(env):
    c,e,n,m=env;barrier=threading.Barrier(2);out=[]
    def attempt():
        other=CredentialManager(m.path,CustomerConfig(c.path),n);barrier.wait()
        try:other.create('external',expected_digest=e['digest'],secret='synthetic');out.append('stored')
        except CredentialConflict:out.append('denied')
    ts=[threading.Thread(target=attempt) for _ in range(2)]
    for t in ts:t.start()
    for t in ts:t.join(5)
    assert sorted(out)==['denied','stored'] and len(n.calls)==1


def test_bad_secret_or_stale_review_never_reaches_native(env):
    c,e,n,m=env
    with pytest.raises(ValueError):m.create('external',expected_digest=e['digest'],secret='bad token')
    with pytest.raises(CredentialConflict):m.create('external',expected_digest='a'*64,secret='synthetic')
    assert not n.calls and not m.records()


def test_profile_mismatch_prevents_read_or_removal(env):
    c,e,n,m=env;ref=m.create('external',expected_digest=e['digest'],secret='synthetic')
    other=CredentialManager(m.path,c,Native())
    with pytest.raises(CredentialConflict):other.resolve('external',expected_digest=e['digest'])
    with pytest.raises(CredentialConflict):other.remove(ref,expected_digest=e['digest'])


def test_http_workflow_no_secret_read_route_and_no_echo(env,monkeypatch):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app,store,customer_routes,decision_routes
    c,e,n,m=env;monkeypatch.setattr(store,'DB_PATH',c.path.with_name('profile.db'))
    monkeypatch.setattr(customer_routes,'credential_database',lambda:m)
    client=TestClient(app.app,base_url='http://127.0.0.1')
    form=dict(operator_token=decision_routes.TOKEN,expected_digest=e['digest'],secret='synthetic-sensitive-token')
    assert client.post('/configuration/credentials/external/save',data=dict(form,operator_token='wrong')).status_code==403
    assert not n.calls
    response=client.post('/configuration/credentials/external/save',data=form,follow_redirects=False)
    assert response.status_code==303 and response.headers['cache-control']=='no-store'
    page=client.get('/configuration/credentials');assert page.status_code==200
    assert 'synthetic-sensitive-token' not in page.text and 'value="synthetic' not in page.text
    assert 'stored' in page.text and page.headers['cache-control']=='no-store'
    ref=m.records()[0]['reference']
    assert client.get('/configuration/credentials/'+ref).status_code==404
    assert client.post('/configuration/credentials/'+ref+'/remove',data={k:v for k,v in form.items() if k!='secret'},follow_redirects=False).status_code==303
    assert not n.values


def test_unconfigured_installation_disables_entry_and_preserves_state(env,monkeypatch):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app,store,decision_routes
    c,e,n,m=env;monkeypatch.setattr(store,'DB_PATH',c.path.with_name('profile.db'))
    monkeypatch.delenv('VERITY_CREDENTIAL_HELPER',raising=False)
    client=TestClient(app.app,base_url='http://127.0.0.1')
    page=client.get('/configuration/credentials')
    assert 'Protected storage is unavailable' in page.text and 'disabled' in page.text
    response=client.post('/configuration/credentials/external/save',data=dict(operator_token=decision_routes.TOKEN,expected_digest=e['digest'],secret='synthetic-sensitive-token'))
    assert response.status_code==503 and 'synthetic-sensitive-token' not in response.text
    assert not m.records()


def test_metadata_failure_after_native_creation_preserves_recovery(env,monkeypatch):
    c,e,n,m=env
    def unavailable(*args):raise sqlite3.OperationalError('Synthetic storage failure')
    monkeypatch.setattr(m,'_transition',unavailable)
    with pytest.raises(sqlite3.OperationalError):m.create('external',expected_digest=e['digest'],secret='synthetic')
    reopened=CredentialManager(m.path,c,n);row=reopened.records()[0]
    assert row['state']=='pending' and row['reference'] in n.values
    with pytest.raises(CredentialConflict):reopened.resolve('external',expected_digest=e['digest'])
    reopened.remove(row['reference'],expected_digest=e['digest']);assert not n.values


def test_concurrent_removal_rereads_state_under_shared_lock(env,monkeypatch):
    c,e,n,m=env;ref=m.create('external',expected_digest=e['digest'],secret='synthetic')
    barrier=threading.Barrier(2);other=CredentialManager(m.path,CustomerConfig(c.path),n)
    for instance in (m,other):
        original=instance.records
        def stale_read(original=original):
            rows=original();barrier.wait(timeout=3);return rows
        monkeypatch.setattr(instance,'records',stale_read)
    errors=[]
    def remove(instance):
        try:instance.remove(ref,expected_digest=e['digest'])
        except Exception as error:errors.append(type(error).__name__)
    ts=[threading.Thread(target=remove,args=(instance,)) for instance in (m,other)]
    for t in ts:t.start()
    for t in ts:t.join(5)
    assert not errors and len([call for call in n.calls if call[0]=='delete'])==1
    reopened=CredentialManager(m.path,c,n)
    assert reopened.records()[0]['state']=='removed'
    reopened.create('external',expected_digest=e['digest'],secret='replacement')

@pytest.mark.parametrize('mode,state',[('timeout','unknown'),('crash','pending')])
def test_confirm_uncertain_save_is_metadata_only_and_durable(env,mode,state):
    c,e,n,m=env;n.mode=mode
    with pytest.raises((CredentialUnavailable,KeyboardInterrupt)):
        m.create('external',expected_digest=e['digest'],secret='synthetic-sensitive-token')
    ref=m.records()[0]['reference'];assert m.records()[0]['state']==state
    reopened=CredentialManager(m.path,c,n)
    assert reopened.confirm_saved(ref,expected_digest=e['digest'])==ref
    assert [x[0] for x in n.calls]==['create','read']
    assert [x['state'] for x in reopened.history(ref)]==['pending']+(['unknown'] if mode=='timeout' else [])+['stored']
    assert reopened.confirm_saved(ref,expected_digest=e['digest'])==ref
    assert [x[0] for x in n.calls]==['create','read']
    assert 'synthetic-sensitive-token' not in m.path.read_bytes().decode('utf8',errors='ignore')


def test_failed_confirmation_keeps_unresolved_reference_without_create_retry(env,monkeypatch):
    c,e,n,m=env;n.mode='timeout'
    with pytest.raises(CredentialUnavailable):m.create('external',expected_digest=e['digest'],secret='synthetic')
    ref=m.records()[0]['reference'];before=m.history(ref)
    def unavailable(*args,**kwargs):raise CredentialUnavailable('sensitive native diagnostic')
    monkeypatch.setattr(n,'read',unavailable)
    with pytest.raises(CredentialUnavailable) as caught:m.confirm_saved(ref,expected_digest=e['digest'])
    assert 'sensitive' not in str(caught.value)
    assert m.history(ref)==before and m.records()[0]['state']=='unknown'
    with pytest.raises(CredentialConflict):m.create('external',expected_digest=e['digest'],secret='replacement')
    assert len(n.calls)==1


def test_confirmation_rejects_changed_disabled_removed_or_foreign_profile(env):
    c,e,n,m=env;n.mode='timeout'
    with pytest.raises(CredentialUnavailable):m.create('external',expected_digest=e['digest'],secret='synthetic')
    ref=m.records()[0]['reference']
    foreign=CredentialManager(m.path,c,Native())
    with pytest.raises(CredentialConflict):foreign.confirm_saved(ref,expected_digest=e['digest'])
    newer=c.save('connection','external',dict(e['body'],model='changed-model'))
    for digest in (e['digest'],newer['digest']):
        with pytest.raises(CredentialConflict):m.confirm_saved(ref,expected_digest=digest)
    c.disable_connection('external')
    with pytest.raises(CredentialConflict):m.confirm_saved(ref,expected_digest=newer['digest'])
    m.remove(ref,expected_digest=newer['digest'])
    with pytest.raises(CredentialConflict):m.confirm_saved(ref,expected_digest=newer['digest'])
    assert [x[0] for x in n.calls]==['create','delete']


def test_confirmation_rechecks_under_lock_after_concurrent_removal(env,monkeypatch):
    c,e,n,m=env;n.mode='timeout'
    with pytest.raises(CredentialUnavailable):m.create('external',expected_digest=e['digest'],secret='synthetic')
    ref=m.records()[0]['reference']
    from contextlib import contextmanager
    original=m.connection
    @contextmanager
    def remove_first(*args,**kwargs):
        other=CredentialManager(m.path,c,n)
        other.remove(ref,expected_digest=e['digest'])
        with original(*args,**kwargs) as value:yield value
    monkeypatch.setattr(m,'connection',remove_first)
    with pytest.raises(CredentialConflict):m.confirm_saved(ref,expected_digest=e['digest'])
    assert [x[0] for x in n.calls]==['create','delete']

def test_http_confirmation_checks_csrf_exact_fields_and_never_echoes_secret(env,monkeypatch):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app,store,customer_routes,decision_routes
    c,e,n,m=env;n.mode='timeout'
    with pytest.raises(CredentialUnavailable):
        m.create('external',expected_digest=e['digest'],secret='synthetic-sensitive-token')
    ref=m.records()[0]['reference']
    monkeypatch.setattr(store,'DB_PATH',c.path.with_name('profile.db'))
    monkeypatch.setattr(customer_routes,'credential_database',lambda:m)
    client=TestClient(app.app,base_url='http://127.0.0.1')
    path='/configuration/credentials/'+ref+'/confirm'
    form=dict(operator_token=decision_routes.TOKEN,expected_digest=e['digest'])
    page=client.get('/configuration/credentials')
    assert path in page.text and 'synthetic-sensitive-token' not in page.text
    assert client.post(path,data=dict(form,operator_token='wrong')).status_code==403
    assert client.post(path,data=dict(form,secret='must-not-be-accepted')).status_code==400  # bounded form parser rejects excess fields
    assert [x[0] for x in n.calls]==['create']
    response=client.post(path,data=form,follow_redirects=False)
    assert response.status_code==303 and response.headers['cache-control']=='no-store'
    assert m.records()[0]['state']=='stored'
    assert 'synthetic-sensitive-token' not in response.text+client.get('/configuration/credentials').text
    assert client.get('/configuration/credentials/'+ref).status_code==404
    assert [x[0] for x in n.calls]==['create','read']


def test_confirmation_read_serializes_configuration_edit(env,monkeypatch):
    c,e,n,m=env;n.mode='timeout'
    with pytest.raises(CredentialUnavailable):m.create('external',expected_digest=e['digest'],secret='synthetic')
    ref=m.records()[0]['reference'];entered=threading.Event();release=threading.Event();edited=threading.Event();errors=[]
    original=n.read
    def bounded_read(*args,**kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args,**kwargs)
    monkeypatch.setattr(n,'read',bounded_read)
    def confirm():
        try:m.confirm_saved(ref,expected_digest=e['digest'])
        except BaseException as exc:errors.append(exc)
    def edit():
        try:CustomerConfig(c.path).save('connection','external',dict(e['body'],model='newer'))
        except BaseException as exc:errors.append(exc)
        finally:edited.set()
    worker=threading.Thread(target=confirm);worker.start();assert entered.wait(3)
    editor=threading.Thread(target=edit);editor.start()
    try:assert not edited.wait(.1)
    finally:release.set()
    worker.join(4);editor.join(4)
    assert not worker.is_alive() and not editor.is_alive() and not errors and edited.is_set()
    assert m.records()[0]['state']=='stored'
    with pytest.raises(CredentialConflict):m.resolve('external',expected_digest=c.latest('connection','external')['digest'])

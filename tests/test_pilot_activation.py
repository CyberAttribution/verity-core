import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from verity_core import activation as a


@pytest.fixture
def issuer():
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    def sign(claims):
        payload=a.canonical(claims)
        return a.encode(payload)+"."+a.encode(key.sign(payload))
    return public,sign


def claims(**extra):
    return dict(schema="verity_pilot_entitlement/v1",installation_id="install-1",license_id="pilot-1",issued_at=100,expires_at=200,**extra)


def test_signature_binding_expiry_and_revocation(issuer):
    public,sign=issuer;c=sign(claims())
    assert a.entitlement(c,public,"install-1",150)["license_id"]=="pilot-1"
    for installation,now,revoked in [("install-2",150,()),("install-1",200,()),("install-1",0,()),("install-1",150,("pilot-1",))]:
        with pytest.raises(ValueError):a.entitlement(c,public,installation,now,revoked)
    other=Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    with pytest.raises(ValueError):a.entitlement(c,other,"install-1",150)


def test_tampered_and_duplicate_claims_refused(issuer):
    public,sign=issuer;c=sign(claims())
    payload,sig=c.split('.')
    changed=claims();changed["expires_at"]=9999
    with pytest.raises(ValueError):a.entitlement(a.encode(a.canonical(changed))+'.'+sig,public,"install-1",150)
    with pytest.raises(ValueError):a.entitlement(sign(claims(unexpected="permission")),public,"install-1",150)


def test_revocation_signature_revision_and_future_time(issuer):
    public,sign=issuer
    c=sign(dict(schema="verity_pilot_revocations/v1",revision=1,issued_at=100,license_ids=["pilot-1"]))
    assert a.revocations(c,public,150)["revision"]==1
    with pytest.raises(ValueError):a.revocations(c,public,150,1)
    with pytest.raises(ValueError):a.revocations(c,public,0)


@pytest.fixture
def setup(monkeypatch,tmp_path,issuer):
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as ui,setup_routes as s,decision_routes as d
    public,sign=issuer
    key=tmp_path/'issuer.pem';key.write_bytes(public)
    monkeypatch.setattr(s,'path',lambda:tmp_path/'installation.json')
    monkeypatch.setenv('VERITY_ACTIVATION_PUBLIC_KEY',str(key))
    monkeypatch.setenv('VERITY_REQUIRE_ACTIVATION','1')
    client=TestClient(ui.app,base_url='http://127.0.0.1')
    def post(action,**values):return client.post('/setup',data=dict(operator_token=d.TOKEN,action=action,**values),follow_redirects=False)
    return client,s,sign,post


def test_first_launch_requires_real_signed_code_and_cloud_stays_disabled(setup):
    client,s,sign,post=setup
    assert client.get('/',follow_redirects=False).headers['location']=='/setup'
    assert client.get('/setup').status_code==200
    iid=s.state()['installation_id'];now=int(time.time())
    code=sign(dict(schema='verity_pilot_entitlement/v1',installation_id=iid,license_id='pilot-test',issued_at=now,expires_at=now+3600))
    assert post('activate',code='made-up').status_code==422
    assert post('activate',code=code).status_code==303
    assert client.get('/').status_code==200
    assert s.config.CLOUD_ESCALATION=='Disabled'
    assert (s.path().stat().st_mode & 0o077)==0


def test_setup_refuses_cross_site_forms_and_missing_intent(setup):
    client,s,sign,post=setup
    assert client.post('/setup',data=dict(action='configure')).status_code==403
    from swarmtracker_node_ui import decision_routes as d
    assert client.post('/setup',data=dict(action='activate',code='x',operator_token=d.TOKEN),headers={'origin':'https://evil.invalid'}).status_code==403


def test_atomic_settings_failure_does_not_publish_unpersisted_config(setup,monkeypatch):
    client,s,sign,post=setup
    s.state();before=s.config.DEFAULT_MODEL
    def fail(value):raise OSError('disk failure')
    monkeypatch.setattr(s,'write',fail)
    response=post('configure',operator='Tester',model='new-model',openclaw_url='http://127.0.0.1:8090',ollama_url='http://127.0.0.1:11434')
    assert response.status_code==422
    assert s.config.DEFAULT_MODEL==before


def test_non_loopback_runtime_settings_refused(setup):
    client,s,sign,post=setup
    response=post('configure',operator='Tester',model='new-model',openclaw_url='http://evil.invalid:80',ollama_url='http://127.0.0.1:11434')
    assert response.status_code==422
    assert s.state()['settings']=={}


def test_imported_revocation_blocks_valid_code_and_cannot_roll_back(setup):
    client,s,sign,post=setup;now=int(time.time());iid=s.state()['installation_id']
    code=sign(dict(schema='verity_pilot_entitlement/v1',installation_id=iid,license_id='revoke-me',issued_at=now,expires_at=now+3600))
    assert post('activate',code=code).status_code==303
    revoke=sign(dict(schema='verity_pilot_revocations/v1',revision=1,issued_at=now,license_ids=['revoke-me']))
    assert post('revoke',code=revoke).status_code==303
    assert client.get('/',follow_redirects=False).headers['location']=='/setup'
    assert post('activate',code=code).status_code==422
    assert post('revoke',code=revoke).status_code==422
    omission=sign(dict(schema='verity_pilot_revocations/v1',revision=2,issued_at=now,license_ids=[]))
    assert post('revoke',code=omission).status_code==422


def test_clock_rollback_blocks_product_use(setup):
    client,s,sign,post=setup
    value=s.state();value['max_seen']=time.time()+1000;s.write(value)
    assert client.get('/',follow_redirects=False).headers['location']=='/setup'


def test_corrupt_state_is_not_silently_replaced(setup):
    client,s,sign,post=setup
    s.path().write_text('bad-json')
    assert client.get('/setup').status_code==503
    assert s.path().read_text()=='bad-json'


def test_activation_and_runtime_pages_are_separate_and_relaunch_keeps_activation(setup):
    client, s, sign, post = setup
    page = client.get('/setup').text
    assert 'Activation code' in page and 'name="openclaw_url"' not in page
    assert 'kevin@swarmtracker.ai' in page
    assert client.get('/setup/runtime', follow_redirects=False).headers['location'] == '/setup'
    now = int(time.time())
    code = sign(dict(schema='verity_pilot_entitlement/v1',installation_id=s.state()['installation_id'],license_id='split-pilot',issued_at=now,expires_at=now+3600))
    assert post('activate',code=code).status_code == 303
    assert 'Choose an agent' in client.get('/setup').text
    runtime = client.get('/setup/runtime')
    assert runtime.status_code == 200 and 'name="openclaw_url"' in runtime.text
    assert 'name="code"' not in runtime.text
    from swarmtracker_node_ui import decision_routes as d
    assert client.post('/setup/runtime',data=dict(operator_token=d.TOKEN,action='activate',code=code)).status_code == 422
    before = s.path().read_bytes()
    from fastapi.testclient import TestClient
    from swarmtracker_node_ui import app as ui
    relaunched = TestClient(ui.app,base_url='http://127.0.0.1')
    assert relaunched.get('/setup/runtime').status_code == 200
    assert s.state()['activation'] == code and s.path().read_bytes() == before

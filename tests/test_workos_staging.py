from datetime import datetime, timezone, timedelta
import pytest
from verity_core.workos_staging import StagingInvitations, BASE

class Response:
    is_redirect=False
    def __init__(self, data):self.data=data
    def raise_for_status(self):pass
    def json(self):return self.data

class FakeSession:
    def __init__(self, result):self.result=result;self.calls=[];self.trust_env=True
    def request(self, method, url, **kwargs):
        self.calls.append((method,url,kwargs))
        return Response(self.result)

def invitation():
    return dict(object='invitation',id='invitation_test',email='pilot@example.com',state='pending',
        expires_at=(datetime.now(timezone.utc)+timedelta(days=7)).isoformat(),
        accepted_at=None,accepted_user_id=None,token='must-not-return',accept_invitation_url='must-not-return')

def test_staging_send_email_only_and_strip_tokens():
    fake=FakeSession(invitation());gateway=StagingInvitations('sk_example_private',fake)
    result=gateway.send('PILOT@example.com')
    assert fake.trust_env is False
    method,url,args=fake.calls[0]
    assert (method,url)==('POST',BASE)
    assert args['json']=={'email':'pilot@example.com','expires_in_days':7}
    assert args['timeout']==10 and args['allow_redirects'] is False
    assert 'token' not in result and 'accept_invitation_url' not in result
    assert gateway.get('invitation_test')['id']=='invitation_test'
    assert fake.calls[1][0:2]==('GET',BASE+'/invitation_test')

def test_mismatch_and_untrusted_fields_refused():
    data=invitation();data['email']='other@example.com';fake=FakeSession(data)
    with pytest.raises(ValueError):StagingInvitations('sk_example_private',fake).send('pilot@example.com')
    with pytest.raises(ValueError):StagingInvitations('sk_example_private',fake).get('other-id')
    with pytest.raises(ValueError):StagingInvitations('sk_example_private',fake).send('bad-email')
    with pytest.raises(ValueError):StagingInvitations('sk_example_private',fake).send('pilot@example.com',days=31)


def test_fake_provider_metadata_to_private_enrollment_without_token(tmp_path):
    from verity_core.pilot_enrollment import EnrollmentStore
    data=invitation();fake=FakeSession(data);gateway=StagingInvitations('sk_example_private',fake)
    pending=gateway.send('pilot@example.com')
    store=EnrollmentStore(tmp_path/'private/enrollment.db')
    store.prepare_from_gateway(gateway,pending['id'],pending['email'])
    data['state']='accepted';data['accepted_user_id']='user_test'
    data['accepted_at']=datetime.now(timezone.utc).isoformat()
    accepted=gateway.get('invitation_test')
    candidate=store.accept('invitation_test',accepted,
        dict(id='user_test',email='pilot@example.com',email_verified=True),'Pilot Name')
    assert candidate['status']=='eligible_for_installation_enrollment'
    assert 'must-not-return' not in str(store.get('invitation_test'))
    assert store.bind_installation('invitation_test','install_test')['activation_issued'] is False


def test_gateway_readback_blocks_wrong_recipient_or_already_accepted(tmp_path):
    from verity_core.pilot_enrollment import EnrollmentStore
    data=invitation();fake=FakeSession(data);gateway=StagingInvitations('sk_example_private',fake)
    store=EnrollmentStore(tmp_path/'private/enrollment.db')
    with pytest.raises(ValueError):
        store.prepare_from_gateway(gateway,'invitation_test','other@example.com')
    data['state']='accepted';data['accepted_user_id']='user_test'
    data['accepted_at']=datetime.now(timezone.utc).isoformat()
    with pytest.raises(ValueError):
        store.prepare_from_gateway(gateway,'invitation_test','pilot@example.com')
    data['state']='pending';data['accepted_user_id']=data['accepted_at']=None
    assert store.prepare_from_gateway(gateway,'invitation_test','pilot@example.com')['state']=='pending'


def test_fake_staging_to_signed_code_and_explicit_offline_revocation_gap(tmp_path):
    import time
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from verity_core import activation
    from verity_core.pilot_enrollment import EnrollmentStore
    from verity_core.pilot_registry import Registry
    data=invitation();fake=FakeSession(data);gateway=StagingInvitations('sk_example_private',fake)
    sent=gateway.send('pilot@example.com')
    enrollment=EnrollmentStore(tmp_path/'private/enrollment.db')
    enrollment.prepare_from_gateway(gateway,sent['id'],'pilot@example.com')
    data['state']='accepted';data['accepted_user_id']='user_test'
    data['accepted_at']=datetime.now(timezone.utc).isoformat()
    candidate=enrollment.accept(sent['id'],gateway.get(sent['id']),
        dict(id='user_test',email='pilot@example.com',email_verified=True),
        'Pilot Name','Visible Org')
    assert candidate['organization']=='Visible Org'
    enrollment.bind_installation(sent['id'],'install_test')
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    claims=dict(schema='verity_pilot_entitlement/v1',installation_id='install_test',
                license_id='license_test',issued_at=int(time.time())-10,expires_at=int(time.time())+3600)
    payload=activation.canonical(claims)
    code=activation.encode(payload)+'.'+activation.encode(key.sign(payload))
    registry=Registry(tmp_path/'private/registry.db')
    exported=enrollment.issue_bound_entitlement(sent['id'],'install_test',code,public,
                  tmp_path/'private/code',registry)
    assert exported['activation_issued']
    assert 'must-not-return' not in str(enrollment.get(sent['id']))
    revoked=enrollment.revoke(sent['id'],'Disposable revocation')
    assert revoked['offline_revocation_pending']
    assert activation.entitlement(code,public,'install_test',time.time())['license_id']=='license_test'
    # An administrator-signed cumulative list is a separate artifact. The
    # installation must import and verify it before local offline denial.
    revoked_claims=dict(schema='verity_pilot_revocations/v1',revision=1,
                        issued_at=int(time.time()),license_ids=['license_test'])
    revoked_payload=activation.canonical(revoked_claims)
    signed_list=activation.encode(revoked_payload)+'.'+activation.encode(key.sign(revoked_payload))
    list_path=tmp_path/'private/revocations.code';list_path.write_text(signed_list)
    registry.record_revocations(signed_list,public,list_path)
    assert registry.status('license_test')['licenses'][0]['revocation_list_issued']
    assert registry.status()['events'][-1]['data']['installation_import']=='not observed'
    imported=activation.revocations(signed_list,public,time.time())['license_ids']
    with pytest.raises(ValueError):
        activation.entitlement(code,public,'install_test',time.time(),imported)

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
    store.prepare(pending['id'],pending['email'],pending['expires_at'])
    data['state']='accepted';data['accepted_user_id']='user_test'
    data['accepted_at']=datetime.now(timezone.utc).isoformat()
    accepted=gateway.get('invitation_test')
    candidate=store.accept('invitation_test',accepted,
        dict(id='user_test',email='pilot@example.com',email_verified=True),'Pilot Name')
    assert candidate['status']=='eligible_for_installation_enrollment'
    assert 'must-not-return' not in str(store.get('invitation_test'))
    assert store.bind_installation('invitation_test','install_test')['activation_issued'] is False

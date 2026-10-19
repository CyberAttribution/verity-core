from datetime import datetime, timezone, timedelta
import pytest
from verity_core.pilot_enrollment import EnrollmentStore, EnrollmentConflict

NOW = datetime(2026, 10, 19, 2, 30, tzinfo=timezone.utc)

def records():
    expiry=(NOW+timedelta(days=1)).isoformat()
    invite=dict(id='invite-test',email='pilot@example.com',state='accepted',accepted_user_id='user-test',
                accepted_at=(NOW-timedelta(minutes=1)).isoformat(),expires_at=expiry)
    user=dict(id='user-test',email='pilot@example.com',email_verified=True)
    return invite,user

def test_verified_acceptance_relaunch_installation_and_replay(tmp_path):
    path=tmp_path/'private/enroll.db';s=EnrollmentStore(path)
    invite,user=records();assert s.prepare('invite-test','pilot@example.com',invite['expires_at'],now=NOW.timestamp())['state']=='pending'
    with pytest.raises(EnrollmentConflict):s.prepare('invite-test','pilot@example.com',invite['expires_at'],now=NOW.timestamp())
    candidate=s.accept('invite-test',invite,user,'Pilot Name','',now=NOW)
    assert candidate['email_verified'] and candidate['organization']==''
    again=EnrollmentStore(path)
    assert again.get('invite-test')['state']=='accepted'
    with pytest.raises(EnrollmentConflict):again.accept('invite-test',invite,user,'Pilot Name',now=NOW)
    with pytest.raises(ValueError):again.bind_installation('invite-test','install-late',now=(NOW+timedelta(days=2)).timestamp())
    bound=again.bind_installation('invite-test','install-test')
    assert bound['activation_issued'] is False
    with pytest.raises(EnrollmentConflict):again.bind_installation('invite-test','other-install')
    assert again.get('invite-test')['installation_id']=='install-test'
    assert path.stat().st_mode & 0o077 == 0

def test_wrong_recipient_unverified_and_expiry_never_bind(tmp_path):
    s=EnrollmentStore(tmp_path/'private/enroll.db');invite,user=records()
    s.prepare('invite-test','pilot@example.com',invite['expires_at'],now=NOW.timestamp())
    wrong=dict(user,email='alternate@example.com')
    with pytest.raises(ValueError):s.accept('invite-test',invite,wrong,'Pilot Name',now=NOW)
    with pytest.raises(ValueError):s.accept('invite-test',invite,dict(user,email_verified=False),'Pilot Name',now=NOW)
    assert s.get('invite-test')['state']=='pending'
    with pytest.raises(ValueError):s.accept('invite-test',invite,user,'Pilot Name',now=NOW+timedelta(days=2))
    with pytest.raises(EnrollmentConflict):s.bind_installation('invite-test','install-test')


def test_disposable_verified_enrollment_can_issue_offline_code_then_revoke(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from verity_core import activation
    from verity_core.pilot_registry import Registry
    s=EnrollmentStore(tmp_path/'private/enroll.db');invite,user=records()
    s.prepare('invite-test','pilot@example.com',invite['expires_at'],now=NOW.timestamp())
    s.accept('invite-test',invite,user,'Pilot Name',now=NOW)
    bound=s.bind_installation('invite-test','install-test',now=NOW.timestamp())
    assert bound['activation_issued'] is False
    issuer=Ed25519PrivateKey.generate()
    public=issuer.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    claims=dict(schema='verity_pilot_entitlement/v1',installation_id='install-test',license_id='license-test',
                issued_at=int(NOW.timestamp()),expires_at=int((NOW+timedelta(hours=1)).timestamp()))
    payload=activation.canonical(claims)
    code=activation.encode(payload)+'.'+activation.encode(issuer.sign(payload))
    registry=Registry(tmp_path/'private/registry.db')
    registry.issue(code,public,tmp_path/'private/entitlement.code')
    assert activation.entitlement(code,public,'install-test',NOW.timestamp())['license_id']=='license-test'
    assert activation.entitlement(code,public,'install-test',NOW.timestamp()+60)['license_id']=='license-test'
    with pytest.raises(ValueError):activation.entitlement(code,public,'other-install',NOW.timestamp())
    with pytest.raises(ValueError):activation.entitlement(code,public,'install-test',(NOW+timedelta(hours=2)).timestamp())
    with pytest.raises(ValueError):activation.entitlement(code,public,'install-test',NOW.timestamp(),['license-test'])
    # Binding and code issuance are separate administrator actions; no WorkOS call occurred.
    assert s.get('invite-test')['state']=='installation_bound'

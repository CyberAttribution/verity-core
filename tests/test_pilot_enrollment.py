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
    bound=again.bind_installation('invite-test','install-test',now=NOW.timestamp())
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
    current=datetime.now(timezone.utc)
    s=EnrollmentStore(tmp_path/'private/enroll.db');invite,user=records()
    invite["expires_at"]=(current+timedelta(days=1)).isoformat()
    invite["accepted_at"]=(current-timedelta(minutes=1)).isoformat()
    s.prepare('invite-test','pilot@example.com',invite['expires_at'],now=current.timestamp())
    s.accept('invite-test',invite,user,'Pilot Name',now=current)
    bound=s.bind_installation('invite-test','install-test',now=current.timestamp())
    assert bound['activation_issued'] is False
    issuer=Ed25519PrivateKey.generate()
    public=issuer.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    claims=dict(schema='verity_pilot_entitlement/v1',installation_id='install-test',license_id='license-test',
                issued_at=int(current.timestamp()),expires_at=int((current+timedelta(hours=1)).timestamp()))
    payload=activation.canonical(claims)
    code=activation.encode(payload)+'.'+activation.encode(issuer.sign(payload))
    registry=Registry(tmp_path/'private/registry.db')
    registry.issue(code,public,tmp_path/'private/entitlement.code')
    assert activation.entitlement(code,public,'install-test',current.timestamp())['license_id']=='license-test'
    assert activation.entitlement(code,public,'install-test',current.timestamp()+60)['license_id']=='license-test'
    with pytest.raises(ValueError):activation.entitlement(code,public,'other-install',current.timestamp())
    with pytest.raises(ValueError):activation.entitlement(code,public,'install-test',(current+timedelta(hours=2)).timestamp())
    with pytest.raises(ValueError):activation.entitlement(code,public,'install-test',current.timestamp(),['license-test'])
    # Binding and code issuance are separate administrator actions; no WorkOS call occurred.
    assert s.get('invite-test')['state']=='installation_bound'


@pytest.mark.parametrize("revocation_timing", ["before_resume", "after_status"])
def test_bound_entitlement_requires_verified_identity_exact_installation_and_one_license(tmp_path,monkeypatch,revocation_timing):
    import time
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from verity_core import activation
    from verity_core.pilot_registry import Registry
    from verity_core.pilot_enrollment import EnrollmentConflict
    current=datetime.now(timezone.utc)
    expiry=(current+timedelta(days=1)).isoformat()
    invite=dict(id='invite-bound',email='pilot@example.com',state='accepted',accepted_user_id='user-bound',
                accepted_at=(current-timedelta(minutes=1)).isoformat(),expires_at=expiry)
    user=dict(id='user-bound',email='pilot@example.com',email_verified=True)
    store=EnrollmentStore(tmp_path/'private/enroll.db')
    store.prepare('invite-bound','pilot@example.com',expiry,now=current.timestamp())
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    def code(license_id):
        claims=dict(schema='verity_pilot_entitlement/v1',installation_id='install-bound',
                    license_id=license_id,issued_at=int(time.time())-10,expires_at=int(time.time())+3600)
        payload=activation.canonical(claims)
        return activation.encode(payload)+'.'+activation.encode(key.sign(payload))
    signed=code('license-bound')
    registry=Registry(tmp_path/'private/registry.db')
    output=tmp_path/'private/entitlement.code'
    with pytest.raises(EnrollmentConflict):
        store.issue_bound_entitlement('invite-bound','install-bound',signed,public,output,registry)
    store.accept('invite-bound',invite,user,'Pilot',now=current)
    with pytest.raises(EnrollmentConflict):
        store.issue_bound_entitlement('invite-bound','install-bound',signed,public,output,registry)
    store.bind_installation('invite-bound','install-bound',now=current.timestamp())
    with pytest.raises(ValueError):
        store.issue_bound_entitlement('invite-bound','other-install',signed,public,output,registry)
    issued=store.issue_bound_entitlement('invite-bound','install-bound',signed,public,output,registry)
    assert issued['activation_issued'] and output.stat().st_mode & 0o077==0
    assert registry.status('license-bound')['licenses'][0]['artifact_state']=='exported'
    assert EnrollmentStore(tmp_path/'private/enroll.db').activation_link('invite-bound')['state']=='exported'
    again=store.issue_bound_entitlement('invite-bound','install-bound',signed,public,output,registry)
    assert again['license_id']=='license-bound'
    with pytest.raises(EnrollmentConflict):
        store.issue_bound_entitlement('invite-bound','install-bound',code('different-license'),public,
                                      tmp_path/'private/other.code',registry)
    # A registry-issued signed list must block a resumed confirmation as well.
    revclaims=dict(schema="verity_pilot_revocations/v1",revision=1,issued_at=int(time.time()),license_ids=["license-bound"])
    payload=activation.canonical(revclaims)
    revcode=activation.encode(payload)+"."+activation.encode(key.sign(payload))
    revpath=tmp_path/"revocations.code";revpath.write_text(revcode)
    if revocation_timing=="before_resume":
        registry.record_revocations(revcode,public,revpath)
    else:
        original_status=registry.status
        def revoke_after_snapshot(*args,**kwargs):
            snapshot=original_status(*args,**kwargs)
            registry.record_revocations(revcode,public,revpath)
            return snapshot
        monkeypatch.setattr(registry,"status",revoke_after_snapshot)
    from verity_core.pilot_registry import Conflict
    with pytest.raises(Conflict,match="revocation"):
        store.issue_bound_entitlement('invite-bound','install-bound',signed,public,output,registry)


def test_entitlement_reservation_failure_stays_visible_for_reconciliation(tmp_path,monkeypatch):
    import time
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from verity_core import activation
    from verity_core.pilot_registry import Registry
    now=datetime.now(timezone.utc);expiry=(now+timedelta(days=1)).isoformat()
    store=EnrollmentStore(tmp_path/'private/enroll.db')
    store.prepare('invite-fail','pilot@example.com',expiry,now=now.timestamp())
    invitation=dict(id='invite-fail',email='pilot@example.com',state='accepted',accepted_user_id='user-fail',
                    accepted_at=now.isoformat(),expires_at=expiry)
    store.accept('invite-fail',invitation,dict(id='user-fail',email='pilot@example.com',email_verified=True),
                 'Pilot',now=now)
    store.bind_installation('invite-fail','install-fail',now=now.timestamp())
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    claims=dict(schema='verity_pilot_entitlement/v1',installation_id='install-fail',license_id='license-fail',
                issued_at=int(time.time())-10,expires_at=int(time.time())+3600)
    payload=activation.canonical(claims);signed=activation.encode(payload)+'.'+activation.encode(key.sign(payload))
    registry=Registry(tmp_path/'private/registry.db')
    output=tmp_path/'private/entitlement.code'
    original=registry.issue
    def interrupted(*args,**kwargs):
        raise OSError('Simulated export interruption')
    monkeypatch.setattr(registry,'issue',interrupted)
    with pytest.raises(OSError):
        store.issue_bound_entitlement('invite-fail','install-fail',signed,public,output,registry)
    assert store.activation_link('invite-fail')['state']=='pending'
    monkeypatch.setattr(registry,'issue',original)
    assert store.issue_bound_entitlement('invite-fail','install-fail',signed,public,output,registry)['activation_issued']


def test_local_revocation_blocks_pending_and_accepted_enrollment(tmp_path):
    store=EnrollmentStore(tmp_path/'private/enroll.db');invite,user=records()
    store.prepare('invite-test','pilot@example.com',invite['expires_at'],now=NOW.timestamp())
    revoked=store.revoke('invite-test','Operator cancelled',now=NOW.timestamp())
    assert revoked['state']=='revoked' and not revoked['offline_revocation_pending']
    assert EnrollmentStore(tmp_path/'private/enroll.db').revocation('invite-test')['reason']=='Operator cancelled'
    with pytest.raises(EnrollmentConflict):store.accept('invite-test',invite,user,'Pilot',now=NOW)
    with pytest.raises(EnrollmentConflict):store.revoke('invite-test','Replay',now=NOW.timestamp())
    assert store.get('invite-test')['state']=='revoked'


def test_revoking_exported_code_preserves_offline_delay_until_signed_list_import(tmp_path):
    import time
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from verity_core import activation
    from verity_core.pilot_registry import Registry
    now=datetime.now(timezone.utc);expiry=(now+timedelta(days=1)).isoformat()
    store=EnrollmentStore(tmp_path/'private/enroll.db')
    store.prepare('invite-revoke','pilot@example.com',expiry,now=now.timestamp())
    invitation=dict(id='invite-revoke',email='pilot@example.com',state='accepted',
                    accepted_user_id='user-revoke',accepted_at=now.isoformat(),expires_at=expiry)
    store.accept('invite-revoke',invitation,dict(id='user-revoke',email='pilot@example.com',
                 email_verified=True),'Pilot',now=now)
    store.bind_installation('invite-revoke','install-revoke',now=now.timestamp())
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    claims=dict(schema='verity_pilot_entitlement/v1',installation_id='install-revoke',
                license_id='license-revoke',issued_at=int(time.time())-10,expires_at=int(time.time())+3600)
    payload=activation.canonical(claims);code=activation.encode(payload)+'.'+activation.encode(key.sign(payload))
    registry=Registry(tmp_path/'private/registry.db')
    store.issue_bound_entitlement('invite-revoke','install-revoke',code,public,
                                  tmp_path/'private/entitlement.code',registry)
    result=store.revoke('invite-revoke','Operator revoked',now=now.timestamp())
    assert result['offline_revocation_pending'] and result['exported_code_at_revocation']
    # Local state alone cannot make an already exported offline signature invalid.
    assert activation.entitlement(code,public,'install-revoke',time.time())['license_id']=='license-revoke'
    with pytest.raises(ValueError):
        activation.entitlement(code,public,'install-revoke',time.time(),['license-revoke'])
    with pytest.raises(EnrollmentConflict):
        store.issue_bound_entitlement('invite-revoke','install-revoke',code,public,
                                      tmp_path/'private/entitlement.code',registry)


def test_revocation_serializes_with_entitlement_export(tmp_path,monkeypatch):
    import threading,time
    from concurrent.futures import ThreadPoolExecutor
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from verity_core import activation
    from verity_core.pilot_registry import Registry
    now=datetime.now(timezone.utc);expiry=(now+timedelta(days=1)).isoformat()
    store=EnrollmentStore(tmp_path/'private/enroll.db')
    store.prepare('invite-race','pilot@example.com',expiry,now=now.timestamp())
    invitation=dict(id='invite-race',email='pilot@example.com',state='accepted',
                    accepted_user_id='user-race',accepted_at=now.isoformat(),expires_at=expiry)
    store.accept('invite-race',invitation,dict(id='user-race',email='pilot@example.com',
                 email_verified=True),'Pilot',now=now)
    store.bind_installation('invite-race','install-race',now=now.timestamp())
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    claims=dict(schema='verity_pilot_entitlement/v1',installation_id='install-race',
                license_id='license-race',issued_at=int(time.time())-10,expires_at=int(time.time())+3600)
    payload=activation.canonical(claims);code=activation.encode(payload)+'.'+activation.encode(key.sign(payload))
    registry=Registry(tmp_path/'private/registry.db')
    original=registry.issue
    entered=threading.Event();release=threading.Event()
    def paused(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args,**kwargs)
    monkeypatch.setattr(registry,'issue',paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        issued=pool.submit(store.issue_bound_entitlement,'invite-race','install-race',code,public,
                           tmp_path/'private/entitlement.code',registry)
        assert entered.wait(5)
        revoked=pool.submit(store.revoke,'invite-race','Operator revokes during export')
        release.set()
        assert issued.result(timeout=10)['activation_issued']
        assert revoked.result(timeout=10)['offline_revocation_pending']
    assert store.get('invite-race')['state']=='revoked'


def test_revocation_after_uncertain_export_reservation_flags_offline_risk(tmp_path):
    from verity_core.pilot_enrollment import EnrollmentStore
    store=EnrollmentStore(tmp_path/'private/enroll.db')
    expiry=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    store.prepare('invite-uncertain','pilot@example.com',expiry)
    invite=dict(id='invite-uncertain',email='pilot@example.com',state='accepted',
                accepted_user_id='user-uncertain',accepted_at=datetime.now(timezone.utc).isoformat(),
                expires_at=expiry)
    store.accept('invite-uncertain',invite,dict(id='user-uncertain',email='pilot@example.com',
                 email_verified=True),'Pilot')
    store.bind_installation('invite-uncertain','install-uncertain')
    with store._db() as db:
        db.execute("INSERT INTO activation_links VALUES (?,?,?,?,?)",
                   ('invite-uncertain','install-uncertain','license-uncertain','0'*64,'pending'))
    state=store.revoke('invite-uncertain','Unknown export status')
    assert state['export_status_uncertain'] and state['offline_revocation_pending']
    assert not state['exported_code_at_revocation']
    assert EnrollmentStore(tmp_path/'private/enroll.db').revocation('invite-uncertain')['export_status_uncertain']==1


def test_old_revocation_store_migrates_uncertainty_column(tmp_path):
    import sqlite3
    path=tmp_path/'private/enroll.db'
    EnrollmentStore(path)
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE invitation_revocations')
        db.execute('CREATE TABLE invitation_revocations (invitation_id TEXT PRIMARY KEY, at REAL NOT NULL, reason TEXT NOT NULL, exported_code_at_revocation INTEGER NOT NULL)')
        db.execute("INSERT INTO invitation_revocations VALUES ('old-invite',1,'Old reason',0)")
    migrated=EnrollmentStore(path).revocation('old-invite')
    assert migrated['reason']=='Old reason' and migrated['export_status_uncertain']==0


def test_enrollment_acceptance_expiry_does_not_mutate_pending_state(tmp_path):
    store=EnrollmentStore(tmp_path/"enroll.db");invite,user=records()
    store.prepare("invite-test","pilot@example.com",invite["expires_at"],now=NOW.timestamp())
    expiry=datetime.fromisoformat(invite["expires_at"])
    with pytest.raises(ValueError,match="expired"):
        store.accept("invite-test",invite,user,"Pilot",now=expiry)
    assert store.get("invite-test")["state"]=="pending"

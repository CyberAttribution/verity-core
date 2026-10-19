from datetime import datetime, timezone, timedelta
import pytest
from verity_core.pilot_acceptance import validate_acceptance

NOW = datetime(2026, 10, 19, 2, 30, tzinfo=timezone.utc)

def sample():
    invitation = dict(id="invitation-test", email="invitee@example.com", state="accepted",
        accepted_user_id="user-test", accepted_at=(NOW-timedelta(minutes=3)).isoformat(),
        expires_at=(NOW+timedelta(days=1)).isoformat())
    user = dict(id="user-test", email="invitee@example.com", email_verified=True)
    expected = dict(expected_invitation_id="invitation-test", expected_email="invitee@example.com",
                    name="Pilot Name", organization="", now=NOW)
    return invitation, user, expected

def test_exact_verified_recipient_can_become_enrollment_candidate():
    invitation, user, expected = sample()
    result = validate_acceptance(invitation=invitation, user=user, **expected)
    assert result["status"] == "eligible_for_installation_enrollment"
    assert result["organization"] == "" and result["email_verified"] is True
    assert "token" not in result and "license_id" not in result

def test_other_recipient_unverified_reused_expired_and_missing_name_fail():
    for change in (
        lambda i,u,e: u.update(email="other@example.com"),
        lambda i,u,e: i.update(email="other@example.com"),
        lambda i,u,e: u.update(email_verified=False),
        lambda i,u,e: i.update(accepted_user_id="another-user"),
        lambda i,u,e: i.update(state="pending"),
        lambda i,u,e: i.update(expires_at=(NOW-timedelta(seconds=1)).isoformat()),
        lambda i,u,e: i.update(accepted_at=(NOW+timedelta(minutes=1)).isoformat()),
        lambda i,u,e: e.update(name=" "),
    ):
        invitation, user, expected = sample()
        change(invitation, user, expected)
        with pytest.raises(ValueError):
            validate_acceptance(invitation=invitation, user=user, **expected)

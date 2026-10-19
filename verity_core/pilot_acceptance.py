"""Validate a provider-fetched invitation and authenticated user before enrollment.

The caller must fetch both records from WorkOS using its server-side API and
exchange the authentication code through AuthKit. Caller-supplied JSON or a
browser invitation token is not proof of identity. This module issues no code.
"""
from datetime import datetime, timezone
import re


def _email(value):
    if not isinstance(value, str) or len(value) > 320 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Invalid recipient email")
    return value.casefold()


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Provider timestamp missing")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid provider timestamp") from None
    if result.tzinfo is None:
        raise ValueError("Provider timestamp lacks timezone")
    return result.astimezone(timezone.utc)


def validate_acceptance(*, invitation, user, expected_invitation_id, expected_email, name,
                        organization="", now=None):
    """Return an enrollment candidate, never an entitlement or login session."""
    if not isinstance(invitation, dict) or not isinstance(user, dict):
        raise ValueError("Provider records required")
    if not isinstance(expected_invitation_id, str) or not expected_invitation_id:
        raise ValueError("Expected invitation ID required")
    recipient = _email(expected_email)
    if invitation.get("id") != expected_invitation_id or invitation.get("state") != "accepted":
        raise ValueError("Invitation is not the expected accepted invitation")
    if _email(invitation.get("email")) != recipient or _email(user.get("email")) != recipient:
        raise ValueError("Authenticated user must match invited email exactly")
    if not isinstance(user.get("id"), str) or not user["id"] or invitation.get("accepted_user_id") != user["id"]:
        raise ValueError("Invitation is not bound to authenticated user")
    if user.get("email_verified") is not True:
        raise ValueError("User email is not verified")
    accepted = _timestamp(invitation.get("accepted_at"))
    expires = _timestamp(invitation.get("expires_at"))
    current = datetime.now(timezone.utc) if now is None else now
    if current.tzinfo is None or accepted > expires or accepted > current or current > expires:
        raise ValueError("Invitation acceptance expired or inconsistent")
    if not isinstance(name, str) or not name.strip() or len(name) > 160 or "\x00" in name:
        raise ValueError("Name is required")
    if not isinstance(organization, str) or len(organization) > 160 or "\x00" in organization:
        raise ValueError("Invalid optional organization")
    return {"invitation_id": expected_invitation_id, "user_id": user["id"],
            "email": expected_email, "name": name.strip(),
            "organization": organization.strip(), "email_verified": True,
            "accepted_at": accepted.isoformat(), "status": "eligible_for_installation_enrollment"}

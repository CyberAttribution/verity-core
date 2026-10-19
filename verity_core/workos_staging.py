"""Minimal WorkOS staging invitation API boundary; no AuthKit callback or mail UI.

A caller must supply a verified staging API key through a private server
process. This class cannot prove which WorkOS environment owns the key. Never
pass its key or provider response to ordinary Verity configuration/exports.
"""
import requests

from .activation import _id
from .pilot_acceptance import _email, _timestamp

BASE = "https://api.workos.com/user_management/invitations"


class StagingInvitations:
    def __init__(self, api_key, session=None):
        if not isinstance(api_key, str) or not api_key.startswith("sk_") or len(api_key) < 8:
            raise ValueError("Private WorkOS API key required")
        self._key = api_key
        self._session = session or requests.Session()
        self._session.trust_env = False

    def _request(self, method, url, *, payload=None):
        response = self._session.request(method, url,
            headers={"Authorization": "Bearer " + self._key, "Accept": "application/json"},
            json=payload, timeout=10, allow_redirects=False)
        if response.is_redirect:
            raise ValueError("WorkOS API redirect refused")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("object") != "invitation":
            raise ValueError("Invalid WorkOS invitation response")
        return data

    @staticmethod
    def _metadata(data):
        _id(data.get("id"))
        _email(data.get("email"))
        _timestamp(data.get("expires_at"))
        if data.get("state") not in {"pending", "accepted", "revoked", "expired"}:
            raise ValueError("Invalid WorkOS invitation state")
        return {k: data.get(k) for k in ("id", "email", "state", "expires_at",
                "accepted_at", "accepted_user_id")}

    def send(self, email, *, days=7):
        recipient = _email(email)
        if type(days) is not int or not 1 <= days <= 30:
            raise ValueError("Invitation validity must be 1 to 30 days")
        data = self._request("POST", BASE, payload={"email": recipient, "expires_in_days": days})
        result = self._metadata(data)
        if _email(result["email"]) != recipient or result["state"] != "pending":
            raise ValueError("WorkOS invitation recipient or state mismatch")
        return result

    def get(self, invitation_id):
        _id(invitation_id)
        result = self._metadata(self._request("GET", BASE + "/" + invitation_id))
        if result["id"] != invitation_id:
            raise ValueError("WorkOS invitation ID mismatch")
        return result

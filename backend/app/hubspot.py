"""
HubSpot integration: OAuth connect + push leads/meetings as Contacts.

Stores per-tenant tokens in tenant.integrations.hubspot. Refresh tokens are
swapped automatically when the access token expires.
"""
import logging
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx

from .config import settings
from . import tenants

logger = logging.getLogger(__name__)

OAUTH_AUTHORIZE = "https://app.hubspot.com/oauth/authorize"
OAUTH_TOKEN = "https://api.hubapi.com/oauth/v1/token"
API_BASE = "https://api.hubapi.com"
DEFAULT_SCOPES = "crm.objects.contacts.read crm.objects.contacts.write oauth"

_oauth_states: dict[str, str] = {}  # state -> tenant_id


def is_configured() -> bool:
    return bool(settings.HUBSPOT_CLIENT_ID and settings.HUBSPOT_CLIENT_SECRET)


def build_authorize_url(tenant_id: str, redirect_uri: str) -> str:
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = tenant_id
    params = {
        "client_id": settings.HUBSPOT_CLIENT_ID,
        "scope": DEFAULT_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{OAUTH_AUTHORIZE}?{urlencode(params)}"


def consume_state(state: str) -> Optional[str]:
    return _oauth_states.pop(state, None)


async def exchange_code(code: str, redirect_uri: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.HUBSPOT_CLIENT_ID,
        "client_secret": settings.HUBSPOT_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(OAUTH_TOKEN, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code >= 400:
        raise RuntimeError(f"HubSpot token exchange failed: {r.text}")
    return r.json()


async def refresh_token(refresh_token: str) -> dict:
    data = {
        "grant_type": "refresh_token",
        "client_id": settings.HUBSPOT_CLIENT_ID,
        "client_secret": settings.HUBSPOT_CLIENT_SECRET,
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(OAUTH_TOKEN, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code >= 400:
        raise RuntimeError(f"HubSpot token refresh failed: {r.text}")
    return r.json()


def save_tokens_for_tenant(tenant_id: str, token_payload: dict) -> dict:
    tenant = tenants.get_tenant(tenant_id)
    if not tenant:
        raise RuntimeError("Tenant not found")
    integrations = dict(tenant.get("integrations") or {})
    integrations["hubspot"] = {
        "access_token": token_payload.get("access_token"),
        "refresh_token": token_payload.get("refresh_token"),
        "expires_at": int(time.time()) + int(token_payload.get("expires_in") or 0),
        "scope": token_payload.get("scope") or DEFAULT_SCOPES,
    }
    return tenants.update_tenant(tenant_id, {"integrations": integrations}) or {}


async def _ensure_valid_token(tenant: dict) -> Optional[str]:
    integrations = tenant.get("integrations") or {}
    hs = integrations.get("hubspot") or {}
    access = hs.get("access_token")
    expires_at = hs.get("expires_at") or 0
    if access and expires_at - 60 > time.time():
        return access
    if not hs.get("refresh_token"):
        return None
    new_payload = await refresh_token(hs["refresh_token"])
    save_tokens_for_tenant(tenant["tenant_id"], new_payload)
    return new_payload.get("access_token")


async def push_contact_from_lead(tenant: dict, lead: dict) -> Optional[dict]:
    """Upsert a HubSpot Contact from a lead/meeting payload."""
    if not (tenant.get("integrations") or {}).get("hubspot"):
        return None
    token = await _ensure_valid_token(tenant)
    if not token:
        return None
    email = (lead.get("visitor_email") or "").strip().lower()
    if not email:
        return None
    properties = {
        "email": email,
        "firstname": (lead.get("visitor_name") or "").split(" ")[0] if lead.get("visitor_name") else "",
        "lastname": " ".join((lead.get("visitor_name") or "").split(" ")[1:]) if lead.get("visitor_name") else "",
        "company": lead.get("company_name") or "",
        "hs_lead_status": "NEW",
        "lifecyclestage": "lead",
        "savant_lead_score": str((lead.get("lead_summary") or {}).get("lead_score") or ""),
        "savant_next_action": str((lead.get("lead_summary") or {}).get("next_best_action") or ""),
        "savant_session_id": lead.get("session_id") or "",
    }
    # Strip empty values to avoid blanking existing data
    properties = {k: v for k, v in properties.items() if v not in (None, "")}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as client:
        # Use search-then-upsert via /crm/v3/objects/contacts
        search = await client.post(
            f"{API_BASE}/crm/v3/objects/contacts/search",
            headers=headers,
            json={
                "filterGroups": [{
                    "filters": [{"propertyName": "email", "operator": "EQ", "value": email}]
                }],
                "limit": 1,
            },
        )
        contact_id = None
        if search.status_code == 200:
            results = search.json().get("results") or []
            if results:
                contact_id = results[0].get("id")
        if contact_id:
            r = await client.patch(
                f"{API_BASE}/crm/v3/objects/contacts/{contact_id}",
                headers=headers,
                json={"properties": properties},
            )
        else:
            r = await client.post(
                f"{API_BASE}/crm/v3/objects/contacts",
                headers=headers,
                json={"properties": properties},
            )
    if r.status_code >= 400:
        logger.warning("HubSpot contact upsert failed: %s %s", r.status_code, r.text[:300])
        return None
    return r.json()

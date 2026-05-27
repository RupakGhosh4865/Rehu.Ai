"""
Google Calendar integration: OAuth + slot suggestion + event creation.

Stores per-tenant OAuth tokens in tenant.integrations.google_calendar.
"""
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx

from .config import settings
from . import tenants

logger = logging.getLogger(__name__)

OAUTH_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/calendar/v3"
DEFAULT_SCOPES = "openid email profile https://www.googleapis.com/auth/calendar.events"

_oauth_states: dict[str, str] = {}  # state -> tenant_id


def is_configured() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def build_authorize_url(tenant_id: str, redirect_uri: str) -> str:
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = tenant_id
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "response_type": "code",
        "scope": DEFAULT_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{OAUTH_AUTHORIZE}?{urlencode(params)}"


def consume_state(state: str) -> Optional[str]:
    return _oauth_states.pop(state, None)


async def exchange_code(code: str, redirect_uri: str) -> dict:
    data = {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(OAUTH_TOKEN, data=data)
    if r.status_code >= 400:
        raise RuntimeError(f"Google token exchange failed: {r.text}")
    return r.json()


async def refresh_token(refresh_token: str) -> dict:
    data = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(OAUTH_TOKEN, data=data)
    if r.status_code >= 400:
        raise RuntimeError(f"Google token refresh failed: {r.text}")
    return r.json()


def save_tokens_for_tenant(tenant_id: str, token_payload: dict) -> dict:
    tenant = tenants.get_tenant(tenant_id)
    if not tenant:
        raise RuntimeError("Tenant not found")
    integrations = dict(tenant.get("integrations") or {})
    existing = integrations.get("google_calendar") or {}
    integrations["google_calendar"] = {
        "access_token": token_payload.get("access_token") or existing.get("access_token"),
        "refresh_token": token_payload.get("refresh_token") or existing.get("refresh_token"),
        "expires_at": int(time.time()) + int(token_payload.get("expires_in") or 0),
        "scope": token_payload.get("scope") or existing.get("scope") or DEFAULT_SCOPES,
    }
    return tenants.update_tenant(tenant_id, {"integrations": integrations}) or {}


async def _ensure_valid_token(tenant: dict) -> Optional[str]:
    integrations = tenant.get("integrations") or {}
    gc = integrations.get("google_calendar") or {}
    access = gc.get("access_token")
    expires_at = gc.get("expires_at") or 0
    if access and expires_at - 60 > time.time():
        return access
    if not gc.get("refresh_token"):
        return None
    new_payload = await refresh_token(gc["refresh_token"])
    save_tokens_for_tenant(tenant["tenant_id"], new_payload)
    return new_payload.get("access_token")


async def suggest_slots(tenant: dict, *, days: int = 5, slot_minutes: int = 30) -> list[dict]:
    """Return up to 3 free 30-min slots between 10:00-16:00 over the next N business days."""
    token = await _ensure_valid_token(tenant)
    if not token:
        return []
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days)).isoformat()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{API_BASE}/freeBusy",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": "primary"}],
            },
        )
    if r.status_code >= 400:
        logger.warning("Google freeBusy failed: %s %s", r.status_code, r.text[:300])
        return []
    busy = (r.json().get("calendars") or {}).get("primary", {}).get("busy") or []
    busy_ranges = []
    for b in busy:
        try:
            busy_ranges.append((
                datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                datetime.fromisoformat(b["end"].replace("Z", "+00:00")),
            ))
        except Exception:
            continue

    slots: list[dict] = []
    cursor = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while len(slots) < 3 and cursor < now + timedelta(days=days):
        if cursor.weekday() < 5 and 10 <= cursor.hour < 16:
            end = cursor + timedelta(minutes=slot_minutes)
            conflict = any(s < end and e > cursor for s, e in busy_ranges)
            if not conflict:
                slots.append({
                    "start": cursor.isoformat(),
                    "end": end.isoformat(),
                    "label": cursor.strftime("%a %d %b, %I:%M %p UTC"),
                })
        cursor += timedelta(minutes=30)
    return slots


async def create_event(tenant: dict, *, summary: str, start_iso: str, end_iso: str,
                       attendee_emails: list[str], description: str = "") -> Optional[dict]:
    token = await _ensure_valid_token(tenant)
    if not token:
        return None
    payload = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
        "attendees": [{"email": e} for e in attendee_emails if e],
        "reminders": {"useDefault": True},
        "conferenceData": {
            "createRequest": {
                "requestId": secrets.token_hex(8),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{API_BASE}/calendars/primary/events?conferenceDataVersion=1&sendUpdates=all",
            headers=headers,
            json=payload,
        )
    if r.status_code >= 400:
        logger.warning("Google event create failed: %s %s", r.status_code, r.text[:300])
        return None
    return r.json()

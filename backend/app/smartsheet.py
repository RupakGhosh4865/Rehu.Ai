"""
Smartsheet integration: append leads and meetings as rows.

Configure via environment variables or per-tenant integrations.smartsheet:
  SMARTSHEET_ACCESS_TOKEN, SMARTSHEET_SHEET_ID

Expected sheet columns (matched case-insensitively by title):
  Name, Email, Company, Source, Lead Score, Session ID, Notes, Created At
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.smartsheet.com/2.0"

_column_cache: dict[str, dict[str, int]] = {}
_cache_at: dict[str, float] = {}
_CACHE_TTL = 600

COLUMN_ALIASES = {
    "name": ["name", "visitor name", "contact name", "full name"],
    "email": ["email", "visitor email", "contact email", "e-mail"],
    "company": ["company", "company name", "organization", "org"],
    "source": ["source", "lead source", "origin"],
    "lead_score": ["lead score", "score", "lead_score"],
    "session_id": ["session id", "session_id", "savant session id"],
    "notes": ["notes", "next action", "next best action", "message", "comments"],
    "created_at": ["created at", "created", "date", "timestamp"],
}


def is_configured(tenant: Optional[dict] = None) -> bool:
    return bool(_access_token(tenant) and _sheet_id(tenant))


def _integration(tenant: Optional[dict]) -> dict:
    if not tenant:
        return {}
    ss = (tenant.get("integrations") or {}).get("smartsheet") or {}
    if ss.get("enabled") is False:
        return {}
    return ss


def _access_token(tenant: Optional[dict] = None) -> str:
    ss = _integration(tenant)
    return (ss.get("access_token") or settings.SMARTSHEET_ACCESS_TOKEN or "").strip()


def _sheet_id(tenant: Optional[dict] = None) -> str:
    ss = _integration(tenant)
    return (ss.get("sheet_id") or settings.SMARTSHEET_SHEET_ID or "").strip()


def save_config_for_tenant(tenant_id: str, *, enabled: bool, sheet_id: str, access_token: str = "") -> dict:
    from . import tenants

    tenant = tenants.get_tenant(tenant_id)
    if not tenant:
        raise RuntimeError("Tenant not found")
    integrations = dict(tenant.get("integrations") or {})
    prev = integrations.get("smartsheet") or {}
    integrations["smartsheet"] = {
        "enabled": enabled,
        "sheet_id": sheet_id.strip(),
        "access_token": access_token.strip() or prev.get("access_token") or "",
    }
    return tenants.update_tenant(tenant_id, {"integrations": integrations}) or {}


def disconnect_for_tenant(tenant_id: str) -> dict:
    from . import tenants

    tenant = tenants.get_tenant(tenant_id)
    if not tenant:
        raise RuntimeError("Tenant not found")
    integrations = dict(tenant.get("integrations") or {})
    integrations.pop("smartsheet", None)
    return tenants.update_tenant(tenant_id, {"integrations": integrations}) or {}


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def _column_map(sheet_id: str, token: str) -> dict[str, int]:
    now = time.time()
    if sheet_id in _column_cache and now - _cache_at.get(sheet_id, 0) < _CACHE_TTL:
        return _column_cache[sheet_id]

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{API_BASE}/sheets/{sheet_id}", headers=_headers(token))
    if r.status_code >= 400:
        logger.warning("Smartsheet sheet fetch failed %s: %s", r.status_code, r.text[:300])
        return {}

    by_title: dict[str, int] = {}
    for col in r.json().get("columns") or []:
        title = (col.get("title") or "").strip().lower()
        if title and col.get("id"):
            by_title[title] = col["id"]

    _column_cache[sheet_id] = by_title
    _cache_at[sheet_id] = now
    return by_title


def _resolve_column(by_title: dict[str, int], field: str) -> Optional[int]:
    for alias in COLUMN_ALIASES.get(field, [field]):
        col_id = by_title.get(alias.lower())
        if col_id:
            return col_id
    return None


def _lead_fields(lead: dict) -> dict[str, str]:
    summary = lead.get("lead_summary") or {}
    metadata = lead.get("metadata") or {}
    notes_parts = [
        str(summary.get("next_best_action") or ""),
        str(summary.get("notes") or ""),
        str(metadata.get("product_context") or ""),
        str(lead.get("topic") or ""),
    ]
    notes = " | ".join(p for p in notes_parts if p and p.strip())
    return {
        "name": str(lead.get("visitor_name") or ""),
        "email": str(lead.get("visitor_email") or ""),
        "company": str(lead.get("company_name") or lead.get("company") or ""),
        "source": str(metadata.get("source") or lead.get("source") or "savant"),
        "lead_score": str(summary.get("lead_score") or ""),
        "session_id": str(lead.get("session_id") or ""),
        "notes": notes,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


async def push_row_from_lead(tenant: dict, lead: dict) -> Optional[dict]:
    """Append a lead/meeting row to the configured Smartsheet."""
    if not is_configured(tenant):
        return None
    token = _access_token(tenant)
    sheet_id = _sheet_id(tenant)
    email = (lead.get("visitor_email") or "").strip()
    if not email:
        return None

    by_title = await _column_map(sheet_id, token)
    if not by_title:
        return None

    values = _lead_fields(lead)
    cells = []
    for field, value in values.items():
        if not value:
            continue
        col_id = _resolve_column(by_title, field)
        if col_id:
            cells.append({"columnId": col_id, "value": value})

    if not cells:
        logger.warning("Smartsheet: no matching columns in sheet %s", sheet_id)
        return None

    payload = {"toBottom": True, "cells": cells}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{API_BASE}/sheets/{sheet_id}/rows",
            headers=_headers(token),
            json=payload,
        )
    if r.status_code >= 400:
        logger.warning("Smartsheet row append failed %s: %s", r.status_code, r.text[:300])
        return None
    logger.info("Smartsheet row added for %s", email)
    return r.json()

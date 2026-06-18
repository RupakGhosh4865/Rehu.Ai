"""Auto-split router (Prompt 1.4). Route definitions only; logic lives in
core.py and the domain modules. Bodies are unchanged from the original main.py."""
import json
import uuid
import os
import time
import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import (
    APIRouter, HTTPException, UploadFile, File, Form, WebSocket, Depends,
    Request, Response, Body,
)
from fastapi.responses import (
    FileResponse, PlainTextResponse, RedirectResponse, JSONResponse,
)

from ..config import settings
from ..models import (
    CreateSessionRequest, AddKnowledgeRequest,
    KnowledgeQueryRequest, KnowledgeQueryResponse, KnowledgeQueryResult,
    HealthResponse, PersonaConfig,
    SessionEventRequest, UpdateVisitorRequest, UpdateSessionLanguageRequest, SessionMessageRequest,
    PresentRequest,
    MeetingRequest, MeetingStatusRequest, ProductCardRequest, ComplianceSettingsRequest,
    SignupRequest, LoginRequest, TenantUpdateRequest, BillingCheckoutRequest, IntegrationConnectRequest,
    RideAlongJoinRequest, RideAlongSpeakRequest, LeadCaptureRequest,
    StudioTrainRequest, SmartsheetConfigRequest, TeamInviteRequest,
    ForgotPasswordRequest, ResetPasswordRequest,
)
from .. import (
    liveavatar, agent, knowledge, persona_templates, persona_experience,
    persona_store, auth, session_log_store, languages, product_cards,
    meeting_store, notifications, tenants, billing, hubspot, google_calendar,
    smartsheet, meeting_bot, ride_along_store, orchestrator, orchestrator_tools,
    security, audit, db, rbac, monitoring, google_auth, metering, intelligence,
)
from .. import core
from ..core import (
    store, _local_runtime, _runtime, _get_personas, _save_personas,
    _invalidate_tenant_persona_cache, _seed_personas, _apply_avatar_bindings,
    _cleanup_session, _prune_idle_sessions, _post_session_pipeline,
    _liveavatar_keepalive_loop, _append_transcript, _owned_session,
    _build_lead_summary, _format_user_context, _persona_tone,
    _role_hint_for_persona, _opening_fallback_for_persona,
    _knowledge_query_for_persona, _localized_aiza_opening,
    _alert_avatar_credits_exhausted, _voice_for_locale, _voice_candidate,
    _stream_avatar_and_voice, require_authed, KEEPALIVE_INTERVAL_SECONDS,
    FRONTEND_DIR, _tenant_plan, _MAX_SYSTEM_PROMPT_CHARS,
    _set_session_cookie, _plan_price_usd,
)
import logging
logger = logging.getLogger(__name__)

# Page routes and uploads reference these module-level paths from the factory.
from pathlib import Path as _Path
UPLOADS_DIR = str(_Path(settings.CHROMA_PERSIST_DIR).parent / "uploads")
_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}

router = APIRouter()


@router.get("/api/integrations/hubspot/status", tags=["Integrations"])
async def hubspot_status(request: Request):
    tenant = tenants.require_tenant(request)
    hs = (tenant.get("integrations") or {}).get("hubspot") or {}
    return {
        "configured_on_server": hubspot.is_configured(),
        "connected": bool(hs.get("access_token")),
        "scope": hs.get("scope") or "",
    }


@router.post("/api/integrations/hubspot/connect", tags=["Integrations"])
async def hubspot_connect(request: Request):
    tenant = tenants.require_tenant(request)
    if not hubspot.is_configured():
        raise HTTPException(400, "HubSpot OAuth is not configured on this server")
    redirect = settings.HUBSPOT_REDIRECT_URI or f"{settings.APP_BASE_URL.rstrip('/')}/integrations/hubspot/callback"
    return {"url": hubspot.build_authorize_url(tenant["tenant_id"], redirect)}


@router.get("/integrations/hubspot/callback", tags=["Integrations"], include_in_schema=False)
async def hubspot_callback(code: str = "", state: str = ""):
    if not code or not state:
        raise HTTPException(400, "Missing OAuth code/state")
    tenant_id = hubspot.consume_state(state)
    if not tenant_id:
        raise HTTPException(400, "Invalid or expired state")
    redirect = settings.HUBSPOT_REDIRECT_URI or f"{settings.APP_BASE_URL.rstrip('/')}/integrations/hubspot/callback"
    tokens = await hubspot.exchange_code(code, redirect)
    hubspot.save_tokens_for_tenant(tenant_id, tokens)
    return RedirectResponse(url=f"{settings.APP_BASE_URL.rstrip('/')}/admin?hubspot=connected")


@router.delete("/api/integrations/hubspot", tags=["Integrations"])
async def hubspot_disconnect(request: Request):
    tenant = tenants.require_tenant(request)
    integrations = dict(tenant.get("integrations") or {})
    integrations.pop("hubspot", None)
    tenants.update_tenant(tenant["tenant_id"], {"integrations": integrations})
    return {"status": "disconnected"}


# ── Integrations: Smartsheet ─────────────────────────────────────────────────

@router.get("/api/integrations/smartsheet/status", tags=["Integrations"])
async def smartsheet_status(request: Request):
    tenant = tenants.require_tenant(request)
    ss = (tenant.get("integrations") or {}).get("smartsheet") or {}
    env_ok = bool(settings.SMARTSHEET_ACCESS_TOKEN and settings.SMARTSHEET_SHEET_ID)
    tenant_ok = smartsheet.is_configured(tenant)
    return {
        "configured_on_server": env_ok,
        "connected": tenant_ok,
        "enabled": ss.get("enabled", True) if ss else env_ok,
        "sheet_id": smartsheet._sheet_id(tenant) if tenant_ok else (ss.get("sheet_id") or settings.SMARTSHEET_SHEET_ID or ""),
        "uses_env_fallback": env_ok and not ss.get("access_token"),
    }


@router.put("/api/integrations/smartsheet", tags=["Integrations"])
async def smartsheet_save(request: Request, body: SmartsheetConfigRequest):
    tenant = tenants.require_tenant(request)
    if not body.sheet_id.strip() and not settings.SMARTSHEET_SHEET_ID:
        raise HTTPException(400, "Sheet ID is required")
    smartsheet.save_config_for_tenant(
        tenant["tenant_id"],
        enabled=body.enabled,
        sheet_id=body.sheet_id.strip() or settings.SMARTSHEET_SHEET_ID,
        access_token=(body.access_token or "").strip(),
    )
    return {"status": "saved"}


@router.delete("/api/integrations/smartsheet", tags=["Integrations"])
async def smartsheet_disconnect(request: Request):
    tenant = tenants.require_tenant(request)
    smartsheet.disconnect_for_tenant(tenant["tenant_id"])
    return {"status": "disconnected"}


# ── Integrations: Google Calendar ────────────────────────────────────────────

@router.get("/api/integrations/google-calendar/status", tags=["Integrations"])
async def gcal_status(request: Request):
    tenant = tenants.require_tenant(request)
    gc = (tenant.get("integrations") or {}).get("google_calendar") or {}
    return {
        "configured_on_server": google_calendar.is_configured(),
        "connected": bool(gc.get("access_token") or gc.get("refresh_token")),
    }


@router.post("/api/integrations/google-calendar/connect", tags=["Integrations"])
async def gcal_connect(request: Request):
    tenant = tenants.require_tenant(request)
    if not google_calendar.is_configured():
        raise HTTPException(400, "Google OAuth is not configured on this server")
    redirect = settings.GOOGLE_REDIRECT_URI or f"{settings.APP_BASE_URL.rstrip('/')}/integrations/google-calendar/callback"
    return {"url": google_calendar.build_authorize_url(tenant["tenant_id"], redirect)}


@router.get("/integrations/google-calendar/callback", tags=["Integrations"], include_in_schema=False)
async def gcal_callback(code: str = "", state: str = ""):
    if not code or not state:
        raise HTTPException(400, "Missing OAuth code/state")
    tenant_id = google_calendar.consume_state(state)
    if not tenant_id:
        raise HTTPException(400, "Invalid or expired state")
    redirect = settings.GOOGLE_REDIRECT_URI or f"{settings.APP_BASE_URL.rstrip('/')}/integrations/google-calendar/callback"
    tokens = await google_calendar.exchange_code(code, redirect)
    google_calendar.save_tokens_for_tenant(tenant_id, tokens)
    return RedirectResponse(url=f"{settings.APP_BASE_URL.rstrip('/')}/admin?google_calendar=connected")


@router.delete("/api/integrations/google-calendar", tags=["Integrations"])
async def gcal_disconnect(request: Request):
    tenant = tenants.require_tenant(request)
    integrations = dict(tenant.get("integrations") or {})
    integrations.pop("google_calendar", None)
    tenants.update_tenant(tenant["tenant_id"], {"integrations": integrations})
    return {"status": "disconnected"}


@router.get("/api/integrations/google-calendar/slots", tags=["Integrations"])
async def gcal_slots(request: Request):
    tenant = tenants.require_tenant(request)
    slots = await google_calendar.suggest_slots(tenant)
    return {"slots": slots}


# ── Ride-along meeting bot ───────────────────────────────────────────────────


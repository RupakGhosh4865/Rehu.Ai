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


@router.get("/api/usage", tags=["Billing"])
async def get_usage(request: Request):
    """Live avatar-minute usage vs the plan cap, for the signed-in tenant."""
    tenant = tenants.get_current_tenant_optional(request)
    if not tenant:
        raise HTTPException(401, "Sign in to view usage.")
    return metering.usage_summary(tenant)


@router.get("/api/metering/usage", tags=["Billing"])
async def get_metering_usage(request: Request, _scope: str = Depends(require_authed)):
    """Tenant dashboard usage widget: avatar minutes used vs plan cap + plan limits.
    Tenant-scoped — a tenant only ever sees its own usage."""
    tenant = tenants.get_current_tenant_optional(request)
    if not tenant:
        raise HTTPException(401, "Sign in to view usage.")
    summary = metering.usage_summary(tenant)
    summary["plan"] = tenant.get("plan", "trial")
    summary["plan_limits"] = tenants.plan_limits(tenant.get("plan", "trial"))
    return summary


@router.get("/api/plans", tags=["Billing"])
async def get_plans():
    """Public pricing plans (Growth / Scale / Enterprise) for the pricing page."""
    return {
        "currency_note": "Prices in USD per month.",
        "avatar_cost_per_min_inr": settings.AVATAR_COST_PER_MIN_INR,
        "plans": tenants.public_plans(),
    }


@router.get("/api/billing/plans", tags=["Billing"])
async def billing_plans():
    return {
        "configured": billing.stripe_configured(),
        "plans": [
            {"id": pid, **info, "monthly_price_usd": _plan_price_usd(pid)}
            for pid, info in tenants.PLAN_DEFAULTS.items()
        ],
    }


@router.post("/api/billing/checkout", tags=["Billing"])
async def billing_checkout(req: BillingCheckoutRequest, request: Request):
    tenant = tenants.require_tenant(request)
    if not billing.stripe_configured():
        raise HTTPException(400, "Stripe is not configured on this server")
    try:
        session = await billing.create_checkout_session(
            tenant,
            req.plan,
            return_url=settings.APP_BASE_URL or str(request.base_url).rstrip("/"),
        )
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"url": session.get("url"), "id": session.get("id")}


@router.post("/api/billing/portal", tags=["Billing"])
async def billing_portal(request: Request):
    tenant = tenants.require_tenant(request)
    if not billing.stripe_configured():
        raise HTTPException(400, "Stripe is not configured on this server")
    try:
        session = await billing.create_portal_session(
            tenant,
            return_url=settings.APP_BASE_URL or str(request.base_url).rstrip("/"),
        )
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"url": session.get("url")}


@router.post("/api/billing/webhook", tags=["Billing"], include_in_schema=False)
async def billing_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature") or ""
    # Fail closed: an unconfigured secret means we cannot trust the event, so we
    # must reject it rather than apply attacker-controlled plan changes.
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(503, "Billing webhook is not configured (STRIPE_WEBHOOK_SECRET unset)")
    if not _verify_stripe_signature(payload, sig):
        raise HTTPException(400, "Invalid Stripe signature")
    try:
        event = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid event payload")
    event_type = event.get("type") or ""
    if event_type.startswith("customer.subscription."):
        try:
            billing.apply_subscription_event(event)
        except Exception:
            logger.exception("Failed to apply subscription event")
    return {"received": True}


def _verify_stripe_signature(payload: bytes, sig_header: str) -> bool:
    """Lightweight Stripe signature check (v1 scheme)."""
    import hmac as _hmac
    import hashlib as _hashlib
    if not sig_header or not settings.STRIPE_WEBHOOK_SECRET:
        return False
    try:
        parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    except Exception:
        return False
    timestamp = parts.get("t", "")
    v1 = parts.get("v1", "")
    signed = f"{timestamp}.".encode("utf-8") + payload
    expected = _hmac.new(settings.STRIPE_WEBHOOK_SECRET.encode("utf-8"), signed, _hashlib.sha256).hexdigest()
    return _hmac.compare_digest(expected, v1)


# ── Integrations: HubSpot ────────────────────────────────────────────────────


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


@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    db_ok = monitoring.db_healthy()
    return HealthResponse(
        status="healthy" if db_ok else "degraded",
        version=settings.APP_VERSION,
        services={
            "database": "ok" if db_ok else "error",
            "environment": settings.ENVIRONMENT,
            "active_sessions": store.count(),
            "sandbox_mode": settings.LIVEAVATAR_USE_SANDBOX,
            "liveavatar_key_set": bool(settings.LIVEAVATAR_API_KEY),
        },
    )




@router.get("/", tags=["System"])
async def root():
    # Serve the v3 homepage (current design); fall back to the previous pages, then call UI.
    for name in ("homepage-v3.html", "homepage-v2.html", "homepage.html"):
        p = os.path.join(FRONTEND_DIR, name)
        if os.path.isfile(p):
            return FileResponse(p, headers=_NO_CACHE)
    idx = os.path.join(FRONTEND_DIR, "index.html")
    return FileResponse(idx, headers=_NO_CACHE) if os.path.isfile(idx) else {"status": "running", "version": settings.APP_VERSION}


@router.get("/call", tags=["System"])
async def call_page():
    idx = os.path.join(FRONTEND_DIR, "index.html")
    # no-cache so frontend updates always reach the browser (the call UI changes often)
    return FileResponse(idx, headers=_NO_CACHE) if os.path.isfile(idx) else {"status": "call interface not found"}


@router.get("/v2", tags=["System"], include_in_schema=False)
async def homepage_v2_preview():
    """Alias of the live homepage (kept so existing /v2 links still work)."""
    p = os.path.join(FRONTEND_DIR, "homepage-v2.html")
    return FileResponse(p, headers=_NO_CACHE) if os.path.isfile(p) else {"status": "v2 preview not found"}


@router.get("/v3", tags=["System"], include_in_schema=False)
async def homepage_v3_preview():
    """Preview of the next homepage design — NOT the default. Reachable at /v3
    so it can be reviewed live before deciding to promote it to `/`."""
    p = os.path.join(FRONTEND_DIR, "homepage-v3.html")
    return FileResponse(p, headers=_NO_CACHE) if os.path.isfile(p) else {"status": "v3 preview not found"}


@router.get("/legacy", tags=["System"], include_in_schema=False)
async def homepage_legacy():
    """The previous (8-screen) homepage, kept reachable for reference."""
    p = os.path.join(FRONTEND_DIR, "homepage.html")
    return FileResponse(p, headers=_NO_CACHE) if os.path.isfile(p) else {"status": "legacy homepage not found"}


@router.get("/trust", tags=["System"], include_in_schema=False)
async def trust_page():
    """Public Trust & Security page (buyer-facing security posture)."""
    p = os.path.join(FRONTEND_DIR, "trust.html")
    return FileResponse(p, headers=_NO_CACHE) if os.path.isfile(p) else {"status": "trust page not found"}


@router.get("/reset-password", tags=["System"], include_in_schema=False)
async def reset_password_page():
    p = os.path.join(FRONTEND_DIR, "reset-password.html")
    return FileResponse(p, headers=_NO_CACHE) if os.path.isfile(p) else {"status": "reset page not found"}


@router.get("/solutions/{slug}", tags=["System"])
async def solution_page(slug: str):
    if not persona_templates.get_template(slug):
        raise HTTPException(404, f"Solution '{slug}' not found")
    p = os.path.join(FRONTEND_DIR, "solution.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Solution page not found")


@router.get("/admin", tags=["System"])
async def admin():
    # Serve the dashboard shell openly; the SPA authenticates each customer with
    # their own login token (redirecting to /login if absent). Per-customer data
    # is protected by the tenant-scoped API + isolation, not by this page gate.
    p = os.path.join(FRONTEND_DIR, "admin.html")
    if os.path.isfile(p):
        return FileResponse(p, headers=_NO_CACHE)
    raise HTTPException(404, "Admin panel not found")


@router.get("/dashboard", tags=["System"])
async def dashboard_page(request: Request):
    # Server-side onboarding gate (source of truth = tenant.onboarding_completed).
    # A signed-in tenant who hasn't finished onboarding is sent there first.
    tenant = tenants.get_current_tenant_optional(request)
    if tenant and not tenant.get("onboarding_completed"):
        return RedirectResponse("/onboarding", status_code=302)
    p = os.path.join(FRONTEND_DIR, "dashboard.html")
    if os.path.isfile(p):
        return FileResponse(p, headers=_NO_CACHE)
    raise HTTPException(404, "Dashboard not found")


@router.get("/signup", tags=["System"])
async def signup_page():
    p = os.path.join(FRONTEND_DIR, "signup.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Signup page not found")


@router.get("/login", tags=["System"])
async def login_page():
    p = os.path.join(FRONTEND_DIR, "login.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Login page not found")


@router.get("/onboarding", tags=["System"])
async def onboarding_page(request: Request):
    # A tenant who already completed onboarding skips straight to the dashboard,
    # so the redirect is enforced server-side (not bypassable via Google OAuth or
    # a cleared localStorage hint).
    tenant = tenants.get_current_tenant_optional(request)
    if tenant and tenant.get("onboarding_completed"):
        return RedirectResponse("/dashboard", status_code=302)
    p = os.path.join(FRONTEND_DIR, "onboarding.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Onboarding page not found")


@router.get("/pricing", tags=["System"])
async def pricing_page():
    p = os.path.join(FRONTEND_DIR, "pricing.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Pricing page not found")


# ── Personas ──────────────────────────────────────────────────────────────────

@router.get("/api/languages", tags=["System"])
async def list_languages():
    return {"languages": [{"code": k, "name": v} for k, v in languages.SUPPORTED_LANGUAGES.items()]}


@router.get("/ride-along", tags=["System"])
async def ride_along_page(_user: str = Depends(auth.verify_admin)):
    p = os.path.join(FRONTEND_DIR, "ride-along.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Ride-along page not found")


@router.websocket("/ws/voice/{session_id}")
async def voice_websocket(websocket: WebSocket, session_id: str):
    """Fallback voice pipeline when LiveAvatar is not configured."""
    await websocket.accept()
    session = store.get(session_id)
    if not session:
        await websocket.close(code=4004, reason="Session not found")
        return
    tenants.set_active_tenant(session.get("tenant_id") or tenants.DEFAULT_TENANT_ID)
    personas = _get_personas()
    persona = personas.get(session["persona_id"], personas.get("default"))
    await agent.handle_voice_session(
        websocket=websocket,
        session_id=session_id,
        persona_id=session["persona_id"],
        persona_name=session["persona_name"],
        company_name=session["company_name"],
        tone=session["tone"],
        prompt_override=persona.system_prompt_override,
        heygen_session_id=None,
        visitor_name=session.get("visitor_name"),
        opening_text=session.get("opening_text", ""),
        language=session.get("language", "en"),
    )


# ── Avatars ───────────────────────────────────────────────────────────────────


"""
Savant.ai -- FastAPI backend
Uses LiveAvatar (new HeyGen API) for photorealistic streaming avatar via LiveKit.
"""
import json
import logging
import uuid
import os
import time
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, Depends, Request, Response, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request as StarletteRequest

from .config import settings
from .models import (
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
from . import liveavatar, agent, knowledge, persona_templates, persona_experience, persona_store, auth
from . import session_log_store, languages, product_cards, meeting_store
from . import notifications, tenants, billing, hubspot, google_calendar, smartsheet
from . import meeting_bot, ride_along_store
from . import orchestrator, orchestrator_tools
from . import security, audit, db, rbac, monitoring, google_auth, metering
from . import intelligence, session_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s -- %(message)s")
logger = logging.getLogger(__name__)

# ── Shared runtime/state/helpers ──────────────────────────────────────────────
# Extracted to core.py so route handlers can move into app/routers/* without a
# circular import (routers import core; core never imports a router or main).
# Re-export the names main.py's own glue + the test-suite reference. Routers
# import these from core directly.
from . import core
from .core import (  # noqa: F401
    store, _local_runtime, _runtime, _get_personas, _save_personas,
    _invalidate_tenant_persona_cache, _seed_personas, _apply_avatar_bindings,
    _enforce_security_config, _cleanup_session, _prune_idle_sessions,
    _post_session_pipeline, _liveavatar_keepalive_loop, _orphan_sweep_loop,
    _append_transcript, _owned_session, _build_lead_summary, _format_user_context,
    _persona_tone, _role_hint_for_persona, _opening_fallback_for_persona,
    _knowledge_query_for_persona, _localized_aiza_opening,
    _alert_avatar_credits_exhausted, _voice_for_locale, _voice_candidate,
    _stream_avatar_and_voice, require_authed, KEEPALIVE_INTERVAL_SECONDS,
    FRONTEND_DIR,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    core._enforce_security_config()  # single security gate (calls security.check_secrets)
    logger.info("LiveAvatar API key set: %s", bool(settings.LIVEAVATAR_API_KEY))
    logger.info("Sandbox mode: %s", settings.LIVEAVATAR_USE_SANDBOX)
    monitoring.init_monitoring()  # Sentry if SENTRY_DSN is set; no-op otherwise
    db.init_db()              # create tables (SQLite dev / Postgres prod)
    try:
        result = db.migrate_all_json()  # idempotent import of all legacy JSON stores
        logger.info("JSON->DB migration: %s", {k: v.get("imported") for k, v in result.items()})
    except Exception:
        logger.exception("JSON->DB migration failed (non-fatal)")
    audit.record("server.start", meta={"version": settings.APP_VERSION, "env": settings.ENVIRONMENT})
    avatars = await liveavatar.list_public_avatars(page_size=48)
    core.set_avatar_cache(avatars)
    persona_experience.bind_live_avatars(avatars)
    core.reload_personas()
    core._apply_avatar_bindings()
    logger.info("Cached %d public avatars for previews", len(avatars))
    sweeper = asyncio.create_task(core._orphan_sweep_loop())   # reclaims crashed-worker sessions
    yield
    sweeper.cancel()
    with suppress(asyncio.CancelledError):
        await sweeper
    owned = list(core._local_runtime.keys()) or [sid for sid, _ in core.store.list_active()]
    logger.info("Shutting down -- cleaning up %d sessions", len(owned))
    for sid in owned:
        await core._cleanup_session(sid)


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)


@app.exception_handler(Exception)
async def global_exception_handler(request: StarletteRequest, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Internal error: {exc}"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# SecurityHeaders added last = outermost. RateLimitMiddleware in security.py is
# the SINGLE rate limiter (Redis-backed with in-memory fallback).
app.add_middleware(security.RateLimitMiddleware)
app.add_middleware(security.SecurityHeadersMiddleware)


@app.middleware("http")
async def tenant_context_middleware(request, call_next):
    """Resolve tenant from JWT or subdomain and bind it to the request context."""
    tenant = tenants.get_current_tenant_optional(request)
    tenant_id = (tenant or {}).get("tenant_id") if tenant else tenants.DEFAULT_TENANT_ID
    tenants.set_active_tenant(tenant_id)
    request.state.tenant = tenant
    request.state.tenant_id = tenant_id
    response = await call_next(request)
    return response


if os.path.isdir(FRONTEND_DIR):
    for subdir, mount in [("assets", "/static"), ("sdk", "/sdk")]:
        d = os.path.join(FRONTEND_DIR, subdir)
        if os.path.isdir(d):
            app.mount(mount, StaticFiles(directory=d), name=subdir)

# User-uploaded slide images live on the PERSISTENT VOLUME (data/uploads).
from pathlib import Path as _Path
UPLOADS_DIR = str(_Path(settings.CHROMA_PERSIST_DIR).parent / "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


# ── Routes ────────────────────────────────────────────────────────────────────
# All route definitions now live in app/routers/* (split out of this god module
# in Prompt 1.4). main.py is the app factory: setup, lifespan, middleware,
# static mounts, and router wiring. Domain logic stays in the domain modules;
# shared runtime/state/helpers live in core.py.
from . import routers as _routers

for _r in _routers.all_routers:
    app.include_router(_r)

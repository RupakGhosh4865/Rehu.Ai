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


@router.post("/api/auth/signup", tags=["Accounts"])
async def auth_signup(req: SignupRequest, response: Response):
    try:
        tenant = tenants.create_tenant(req.email, req.password, req.company_name, req.plan or "trial", name=req.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = tenants.issue_token(tenant["tenant_id"], role="owner", actor=req.email)
    tenants.set_active_tenant(tenant["tenant_id"])
    # Seed personas for the new tenant so they have a starting Aiza
    seed = _seed_personas()
    if req.company_name and "default" in seed:
        seed["default"] = seed["default"].model_copy(update={"company_name": req.company_name})
    persona_store.save_all(seed)
    _invalidate_tenant_persona_cache()
    _set_session_cookie(response, token)
    return {"token": token, "tenant": tenant}


@router.post("/api/auth/login", tags=["Accounts"])
async def auth_login(req: LoginRequest, request: Request, response: Response):
    ip = security._client_ip(request)
    principal = tenants.authenticate_user(req.email, req.password)
    if not principal:
        audit.record("auth.login.failed", actor=req.email, ip=ip)
        raise HTTPException(401, "Invalid email or password")
    tenant, role = principal["tenant"], principal["role"]
    # Track last-login for the owner console (best-effort; never block sign-in).
    try:
        tenants.update_tenant(tenant["tenant_id"], {
            "last_login": time.time(),
            "login_count": int(tenant.get("login_count") or 0) + 1,
        })
    except Exception:
        pass
    token = tenants.issue_token(tenant["tenant_id"], role=role, actor=req.email)
    audit.record("auth.login.success", actor=req.email, tenant=tenant.get("tenant_id"), ip=ip, meta={"role": role})
    _set_session_cookie(response, token)
    return {"token": token, "role": role, "tenant": tenants._redact(tenant)}


# ── Team / RBAC ───────────────────────────────────────────────────────────────

@router.get("/api/team", tags=["Accounts"])
async def team_list(principal: dict = Depends(rbac.require("admin"))):
    return {"members": tenants.list_members(principal["tenant"]["tenant_id"])}


@router.post("/api/team/invite", tags=["Accounts"])
async def team_invite(req: TeamInviteRequest, principal: dict = Depends(rbac.require("admin"))):
    tid = principal["tenant"]["tenant_id"]
    try:
        member = tenants.add_member(tid, req.email, req.password, req.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record("team.member.add", actor=principal.get("actor"), tenant=tid,
                 target=req.email, meta={"role": req.role})
    return {"status": "invited", "member": member}


@router.delete("/api/team/{email}", tags=["Accounts"])
async def team_remove(email: str, principal: dict = Depends(rbac.require("admin"))):
    tid = principal["tenant"]["tenant_id"]
    if not tenants.remove_member(tid, email):
        raise HTTPException(404, "Member not found")
    audit.record("team.member.remove", actor=principal.get("actor"), tenant=tid, target=email)
    return {"status": "removed", "email": email}


@router.get("/api/auth/me", tags=["Accounts"])
async def auth_me(request: Request):
    tenant = tenants.get_current_tenant_optional(request)
    if not tenant:
        return {"tenant": None}
    return {"tenant": tenants._redact(tenant)}


@router.post("/api/auth/logout", tags=["Accounts"])
async def auth_logout(response: Response):
    response.delete_cookie("savant_token", path="/")
    return {"status": "ok"}


@router.get("/api/auth/providers", tags=["Accounts"])
async def auth_providers():
    """Which sign-in methods are available (so the UI can show/hide SSO buttons)."""
    return {"password": True, "google": google_auth.is_configured()}


def _safe_relative(path: str, fallback: str) -> str:
    return path if (path.startswith("/") and not path.startswith("//")) else fallback


@router.get("/api/auth/google/login", tags=["Accounts"])
async def google_login(next: str = "/admin"):
    if not google_auth.is_configured():
        raise HTTPException(503, "Google sign-in is not configured")
    state = uuid.uuid4().hex
    resp = RedirectResponse(google_auth.build_login_url(state))
    resp.set_cookie("g_state", state, max_age=600, httponly=True,
                    secure=settings.FORCE_HTTPS, samesite="lax", path="/")
    resp.set_cookie("g_next", _safe_relative(next, "/admin"), max_age=600, httponly=True,
                    secure=settings.FORCE_HTTPS, samesite="lax", path="/")
    return resp


@router.get("/api/auth/google/callback", tags=["Accounts"], include_in_schema=False)
async def google_callback(request: Request, code: str = "", state: str = ""):
    if not google_auth.is_configured():
        raise HTTPException(503, "Google sign-in is not configured")
    if not code or not state or request.cookies.get("g_state") != state:
        raise HTTPException(400, "Invalid sign-in state — please try again")
    email = await google_auth.fetch_email(code)
    if not email:
        raise HTTPException(401, "Google sign-in failed")
    tenant = tenants.get_tenant_by_email(email)
    if not tenant:  # first-time Google sign-in -> auto-provision a workspace
        tenant = tenants.create_tenant(email, uuid.uuid4().hex + uuid.uuid4().hex,
                                       company_name=email.split("@")[0])
        tenants.set_active_tenant(tenant["tenant_id"])
        persona_store.save_all(_seed_personas())
        _invalidate_tenant_persona_cache()
        tenant = tenants.get_tenant_by_email(email)
    token = tenants.issue_token(tenant["tenant_id"], role="owner", actor=email)
    audit.record("auth.login.google", actor=email, tenant=tenant.get("tenant_id"),
                 ip=security._client_ip(request))
    next_url = _safe_relative(request.cookies.get("g_next") or "/admin", "/admin")
    redir = RedirectResponse(next_url)
    _set_session_cookie(redir, token)
    redir.delete_cookie("g_state", path="/")
    redir.delete_cookie("g_next", path="/")
    return redir


@router.post("/api/auth/forgot-password", tags=["Accounts"])
async def auth_forgot_password(req: ForgotPasswordRequest, request: Request):
    """Email a time-limited reset link. Always returns ok (no user enumeration)."""
    ip = security._client_ip(request)
    tenant = tenants.get_tenant_by_email(req.email)
    if tenant:
        token = tenants.issue_reset_token(req.email, ttl_minutes=60)
        link = f"{settings.APP_BASE_URL}/reset-password?token={token}"
        audit.record("auth.password.reset_requested", actor=req.email, tenant=tenant.get("tenant_id"), ip=ip)
        try:
            await notifications.send_email(
                req.email, "Reset your Savant.ai password",
                f"Reset your password (link valid for 1 hour):\n{link}",
                f'<p>Reset your password (valid 1 hour):</p><p><a href="{link}">{link}</a></p>',
            )
        except Exception:
            logger.warning("Reset email could not be sent for %s", req.email)
    return {"status": "ok", "message": "If that email exists, a reset link has been sent."}


@router.post("/api/auth/reset-password", tags=["Accounts"])
async def auth_reset_password(req: ResetPasswordRequest, request: Request):
    email = tenants.verify_reset_token(req.token)
    if not email:
        raise HTTPException(400, "Reset link is invalid or has expired")
    tenant = tenants.get_tenant_by_email(email)
    if not tenant or not tenants.reset_password(tenant["tenant_id"], req.new_password):
        raise HTTPException(400, "Could not reset password")
    audit.record("auth.password.reset", actor=email, tenant=tenant.get("tenant_id"),
                 ip=security._client_ip(request))
    return {"status": "ok", "message": "Password updated. You can now sign in."}


@router.get("/api/account", tags=["Accounts"])
async def get_account(request: Request):
    tenant = tenants.require_tenant(request)
    limits = tenants.plan_limits(tenant.get("plan", "trial"))
    root = (settings.APP_ROOT_DOMAIN or "").strip().lower()
    tenant_url = ""
    if root and tenant.get("slug"):
        scheme = "https" if "localhost" not in root else "http"
        tenant_url = f"{scheme}://{tenant['slug']}.{root}"
    else:
        tenant_url = settings.APP_BASE_URL or str(request.base_url).rstrip("/")
    return {
        "tenant": tenants._redact(tenant),
        "plan_limits": limits,
        "tenant_url": tenant_url,
        "root_domain": root,
    }


@router.post("/api/onboarding/complete", tags=["Accounts"])
async def complete_onboarding(request: Request):
    """Mark onboarding finished for the signed-in tenant (called when step 7
    completes). This is the SERVER-SIDE source of truth that gates
    /onboarding vs /dashboard; localStorage is only a UX hint."""
    tenant = tenants.require_tenant(request)
    updated = tenants.update_tenant(tenant["tenant_id"], {"onboarding_completed": True})
    audit.record("onboarding.complete", actor=tenant.get("email"),
                 tenant=tenant.get("tenant_id"))
    return {"status": "ok", "onboarding_completed": True,
            "tenant": tenants._redact(updated) if updated else None}


@router.patch("/api/account", tags=["Accounts"])
async def update_account(req: TenantUpdateRequest, request: Request):
    tenant = tenants.require_tenant(request)
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if "slug" in patch:
        patch["slug"] = tenants.slugify(patch["slug"]) or tenant.get("slug")
    updated = tenants.update_tenant(tenant["tenant_id"], patch)
    return {"tenant": updated}


# ── Billing (Stripe) ─────────────────────────────────────────────────────────


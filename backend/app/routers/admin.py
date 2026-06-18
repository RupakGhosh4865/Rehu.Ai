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


@router.get("/api/admin/tenants", tags=["Admin"])
async def admin_list_tenants(_user: str = Depends(auth.verify_admin)):
    """Ops view of ALL customers: plan, avatar-minute usage vs cap, session
    activity. Powers the /admin/customers page."""
    stats = db.session_stats_by_tenant()
    rows = []
    for t in tenants.list_tenants():
        tid = t.get("tenant_id")
        usage = metering.usage_summary(t)
        st = stats.get(tid, {})
        plan = t.get("plan") or "trial"
        cost_inr = usage.get("est_cost_inr") or 0           # our COGS this period
        price_usd = _plan_price_usd(plan)                   # MRR contribution (None=custom/enterprise)
        sub_active = bool(t.get("stripe_subscription_id"))
        is_paying = plan not in ("trial",) and (sub_active or bool(price_usd))
        rows.append({
            "tenant_id":       tid,
            "email":           t.get("email"),
            "name":            t.get("name"),
            "company_name":    t.get("company_name"),
            "slug":            t.get("slug"),
            "plan":            plan,
            "plan_label":      usage.get("plan_label"),
            "is_paying":       is_paying,
            "subscription_active": sub_active,
            "monthly_price_usd":   price_usd,
            "minutes_used":    usage.get("avatar_minutes_used"),
            "minutes_cap":     usage.get("avatar_minutes_cap"),
            "usage_status":    usage.get("status"),
            "cost_inr":        cost_inr,
            "sessions":        st.get("sessions", 0),
            "last_session_at": st.get("last_session_at"),
            "last_login":      t.get("last_login"),
            "login_count":     int(t.get("login_count") or 0),
            "created_at":      t.get("created_at"),
        })
    rows.sort(key=lambda r: r.get("last_session_at") or "", reverse=True)

    # Portfolio rollups for the owner — what the business is doing and what it costs us.
    by_plan: dict[str, int] = {}
    for r in rows:
        by_plan[r["plan"]] = by_plan.get(r["plan"], 0) + 1
    # MRR broken down by plan (Revenue section), and trial→paid conversion.
    mrr_by_plan: dict[str, int] = {}
    for r in rows:
        if r["is_paying"] and r["monthly_price_usd"]:
            mrr_by_plan[r["plan"]] = mrr_by_plan.get(r["plan"], 0) + r["monthly_price_usd"]
    paying = sum(1 for r in rows if r["is_paying"])
    trials = sum(1 for r in rows if r["plan"] == "trial")
    # Conversion = paying / (paying + trials) — what fraction of signups became paid.
    conv_denom = paying + trials
    conversion_pct = round(100.0 * paying / conv_denom, 1) if conv_denom else 0.0
    totals = {
        "customers":      len(rows),
        "paying":         paying,
        "free":           trials,
        "by_plan":        by_plan,
        "minutes_used":   round(sum(r["minutes_used"] or 0 for r in rows), 1),
        "cost_inr_total": round(sum(r["cost_inr"] or 0 for r in rows)),
        "trial_burn_inr": round(sum(r["cost_inr"] or 0 for r in rows if r["plan"] == "trial")),
        "mrr_usd":        sum(r["monthly_price_usd"] or 0 for r in rows if r["is_paying"]),
        "mrr_by_plan":    mrr_by_plan,
        "trial_to_paid_conversion_pct": conversion_pct,
    }
    return {
        "count": len(rows),
        "cost_per_min_inr": settings.AVATAR_COST_PER_MIN_INR,
        "totals": totals,
        "tenants": rows,
    }


@router.get("/admin/customers", tags=["Admin"], include_in_schema=False)
async def admin_customers_page():
    p = os.path.join(FRONTEND_DIR, "admin-customers.html")
    if os.path.isfile(p):
        return FileResponse(p, headers=_NO_CACHE)
    raise HTTPException(404, "Customers page not found")


@router.post("/api/admin/tenant-plan", tags=["Admin"])
async def admin_set_tenant_plan(payload: dict = Body(...), _user: str = Depends(auth.verify_admin)):
    """Set a tenant's plan by email (support/founder ops). Resets the usage period."""
    email = (payload.get("email") or "").strip().lower()
    plan = (payload.get("plan") or "").strip()
    if not email or not plan:
        raise HTTPException(400, "email and plan are required")
    if plan not in tenants.PLAN_DEFAULTS:
        raise HTTPException(400, f"Unknown plan '{plan}'. Valid: {list(tenants.PLAN_DEFAULTS)}")
    tenant = tenants.get_tenant_by_email(email)
    if not tenant:
        raise HTTPException(404, f"No tenant found with email {email}")
    updated = tenants.update_tenant(tenant["tenant_id"], {
        "plan": plan, "minutes_used": 0, "minutes_period_start": time.time(),
    })
    audit.record("admin.set_plan", actor=_user or "admin",
                 tenant=tenant["tenant_id"], target=plan)
    return {"ok": True, "email": email, "plan": plan,
            "usage": metering.usage_summary(updated or tenant)}


@router.post("/api/admin/tenant-flags", tags=["Admin"])
async def admin_set_tenant_flags(payload: dict = Body(...), _user: str = Depends(auth.verify_admin)):
    """Set per-tenant feature flags by email (global admin only). Merges into the
    tenant's existing flags so callers can toggle a single flag."""
    email = (payload.get("email") or "").strip().lower()
    flags = payload.get("flags")
    if not email or not isinstance(flags, dict):
        raise HTTPException(400, "email and flags (object) are required")
    tenant = tenants.get_tenant_by_email(email)
    if not tenant:
        raise HTTPException(404, f"No tenant found with email {email}")
    merged = dict(tenant.get("feature_flags") or {})
    merged.update({str(k): bool(v) for k, v in flags.items()})
    updated = tenants.update_tenant(tenant["tenant_id"], {"feature_flags": merged})
    audit.record("admin.set_flags", actor=_user or "admin",
                 tenant=tenant["tenant_id"], meta={"flags": merged})
    return {"ok": True, "email": email, "feature_flags": merged}


@router.get("/api/admin/health", tags=["Admin"])
async def admin_health(_user: str = Depends(auth.verify_admin)):
    """Ops health: active live sessions + error monitoring status. Error rate is
    surfaced via Sentry when wired (SENTRY_DSN); we expose whether it's enabled
    rather than fabricating a number here."""
    return {
        "active_sessions": core.store.count(),
        "session_backend": "redis" if getattr(core.store, "is_redis", lambda: False)() else "in-memory",
        "db_healthy": monitoring.db_healthy(),
        "error_monitoring": "sentry" if settings.SENTRY_DSN else "disabled",
        "environment": settings.ENVIRONMENT,
    }



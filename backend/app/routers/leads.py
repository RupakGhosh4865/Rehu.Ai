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


@router.get("/api/leads", tags=["Leads"])
async def list_leads(_scope: str = Depends(require_authed)):
    leads = []
    for row in session_log_store.list_history(limit=500):
        summary = row.get("lead_summary") or {}
        if row.get("visitor_email") or summary.get("lead_score") or row.get("meeting_requests"):
            leads.append({
                "session_id": row.get("session_id"),
                "visitor_name": row.get("visitor_name"),
                "visitor_email": row.get("visitor_email"),
                "company_name": row.get("company_name"),
                "persona_name": row.get("persona_name"),
                "started_at": row.get("started_at"),
                "lead_summary": summary,
                "meeting_requests": row.get("meeting_requests", []),
            })
    return {"leads": leads}


@router.get("/api/leads/export", tags=["Leads"])
async def export_leads(_scope: str = Depends(require_authed)):
    """CSV export of the signed-in tenant's leads. Tenant-scoped via the store
    (list_history only returns the active tenant's rows), so a tenant can never
    export another tenant's visitor PII."""
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["visitor_name", "visitor_email", "company_name", "persona_name",
                     "started_at", "lead_score", "intent", "next_best_action", "meetings"])
    for row in session_log_store.list_history(limit=5000):
        summary = row.get("lead_summary") or {}
        if not (row.get("visitor_email") or summary.get("lead_score") or row.get("meeting_requests")):
            continue
        writer.writerow([
            row.get("visitor_name", ""), row.get("visitor_email", ""),
            row.get("company_name", ""), row.get("persona_name", ""),
            row.get("started_at", ""), summary.get("lead_score", ""),
            summary.get("intent", ""), summary.get("next_best_action", ""),
            len(row.get("meeting_requests") or []),
        ])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=leads.csv"})


@router.post("/api/leads", tags=["Leads"])
async def capture_lead(req: LeadCaptureRequest):
    """Public inbound lead from the homepage. Persists the record so it appears in the
    admin Leads page, then fires the existing notification pipeline (email + webhook +
    HubSpot + Smartsheet) so the customer is notified within seconds."""
    started_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "session_id": f"inbound-{uuid.uuid4().hex[:12]}",
        "persona_id": "default",
        "persona_name": "Aiza",
        "company_name": req.company or "",
        "visitor_name": req.name or "",
        "visitor_email": req.email,
        "language": "en",
        "started_at": started_at,
        "ended_at": started_at,
        "transcript": [],
        "consent": {},
        "meeting_requests": [],
        "product_interests": [],
        "lead_summary": {
            "lead_score": 75,
            "next_best_action": f"Inbound lead from {req.source or 'homepage'}. Reply within 24h.",
            "notes": req.message or req.product_context or "",
        },
        "metadata": {
            "source": req.source or "homepage",
            "product_context": req.product_context or "",
        },
    }
    try:
        session_log_store.finalize_session(payload)
    except Exception:
        logger.exception("Could not persist inbound lead")
    asyncio.create_task(notifications.notify_lead_captured(payload))
    return {"status": "ok"}



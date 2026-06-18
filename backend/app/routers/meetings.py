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


@router.get("/api/solutions", tags=["Solutions"])
async def list_solutions():
    return {"solutions": [t.to_api_dict() for t in persona_templates.get_all_templates()]}


@router.get("/api/solutions/{slug}", tags=["Solutions"])
async def get_solution(slug: str):
    t = persona_templates.get_template(slug)
    if not t:
        raise HTTPException(404, f"Solution '{slug}' not found")
    data = t.to_api_dict()
    data["system_prompt_preview"] = t.system_prompt[:200] + "…"
    data["system_prompt"] = t.system_prompt
    return data


@router.get("/api/templates", tags=["Solutions"])
async def list_templates():
    return {"templates": [t.to_api_dict() for t in persona_templates.get_all_templates()]}


@router.get("/api/compliance/settings", tags=["Compliance"])
async def get_compliance_settings():
    return session_log_store.load_compliance_settings()


@router.put("/api/compliance/settings", tags=["Compliance"])
async def update_compliance_settings(
    req: ComplianceSettingsRequest,
    _user: str = Depends(auth.verify_admin),
):
    audit.record("compliance.settings.update", actor=_user or "admin", meta=req.model_dump())
    return session_log_store.save_compliance_settings(req.model_dump())


@router.get("/api/audit/log", tags=["Compliance"])
async def get_audit_log(limit: int = 100, principal: dict = Depends(rbac.require("admin"))):
    """The caller's own tenant-scoped, tamper-evident audit trail."""
    tid = principal["tenant"]["tenant_id"]
    return {"integrity": audit.verify_chain().get("ok", True),
            "entries": audit.tail(min(limit, 1000), tenant=tid)}


@router.get("/api/compliance/policy", tags=["Compliance"])
async def compliance_policy():
    """Public AI data & trust policy — the kind a buyer's security review asks for."""
    return {
        "no_train_policy": (
            "Customer data is never used to train AI models. We use the OpenAI API, "
            "which by default does not train on data submitted via the API."
        ),
        "data_isolation": "Each customer's data is isolated per tenant in the database; "
                          "queries are always scoped to the authenticated tenant.",
        "controls": {
            "pii_redaction": "Optional per-tenant redaction of personal data in stored transcripts.",
            "audit_log": "Tamper-evident, hash-chained audit trail of security-relevant actions.",
            "rbac": "Role-based access control (owner/admin/editor/viewer).",
            "prompt_injection_guardrails": "Untrusted visitor input and retrieved documents are "
                                           "treated as data, never instructions.",
            "encryption_in_transit": "TLS for all traffic.",
        },
        "subprocessors": [
            {"name": "OpenAI", "purpose": "LLM reasoning & embeddings", "trains_on_data": False},
            {"name": "HeyGen / LiveAvatar", "purpose": "Avatar video streaming"},
            {"name": "ElevenLabs", "purpose": "Voice (TTS)"},
            {"name": "Deepgram", "purpose": "Speech recognition (STT)"},
        ],
        "contact": "security@savant.ai",
    }


# ── Tenant accounts ───────────────────────────────────────────────────────────

@router.get("/api/ride-along", tags=["RideAlong"])
async def list_ride_along_bots(_user: str = Depends(auth.verify_admin)):
    """List active and historical ride-along bots for the current tenant."""
    return {"bots": ride_along_store.list_all()}


@router.post("/api/ride-along/join", tags=["RideAlong"])
async def join_ride_along(req: RideAlongJoinRequest, _user: str = Depends(auth.verify_admin)):
    """Spin up a meeting bot that joins the meeting as a visible participant."""
    personas = _get_personas()
    persona = personas.get(req.persona_id) or personas.get("default")
    if not persona:
        raise HTTPException(404, f"Persona '{req.persona_id}' not found")
    bot = meeting_bot.MeetingBot(
        meeting_url=req.meeting_url,
        persona_id=req.persona_id,
        bot_name=req.bot_name,
    )
    try:
        result = await bot.join()
    except Exception as e:
        logger.exception("Ride-along join crashed")
        raise HTTPException(500, f"Ride-along join failed: {e}")
    snapshot = ride_along_store.register(bot)
    if not result.get("ok"):
        ride_along_store.update(bot.bot_id, status="error", detail=result.get("detail"))
        raise HTTPException(400, result.get("detail") or "Could not join meeting")
    snapshot.update(result)
    snapshot["persona_name"] = persona.persona_name
    return snapshot


@router.delete("/api/ride-along/{bot_id}", tags=["RideAlong"])
async def stop_ride_along(bot_id: str, _user: str = Depends(auth.verify_admin)):
    bot = ride_along_store.get_live(bot_id)
    if bot is None:
        # The bot might have been stored but the live instance was lost on restart;
        # remove from the disk store so admins can clean up zombie entries.
        if not ride_along_store.get(bot_id):
            raise HTTPException(404, "Bot not found")
        ride_along_store.remove(bot_id)
        return {"bot_id": bot_id, "status": "removed", "detail": "Live bot was already gone"}
    try:
        await bot.leave()
    except Exception:
        logger.exception("Ride-along leave failed")
    ride_along_store.update(bot_id, status="left")
    ride_along_store.remove(bot_id)
    return {"bot_id": bot_id, "status": "left"}


@router.post("/api/ride-along/{bot_id}/speak", tags=["RideAlong"])
async def ride_along_speak(bot_id: str, req: RideAlongSpeakRequest, _user: str = Depends(auth.verify_admin)):
    bot = ride_along_store.get_live(bot_id)
    if bot is None:
        raise HTTPException(404, "Bot not found or already left")
    ok = await bot.speak(req.text)
    return {"bot_id": bot_id, "spoke": ok}


@router.get("/api/notifications/settings", tags=["Notifications"])
async def get_notification_settings(_scope: str = Depends(require_authed)):
    return notifications.get_settings()


@router.put("/api/notifications/settings", tags=["Notifications"])
async def update_notification_settings(
    payload: dict,
    _scope: str = Depends(require_authed),
):
    return notifications.save_settings(payload or {})


@router.get("/api/notifications/deliveries", tags=["Notifications"])
async def webhook_deliveries(_scope: str = Depends(require_authed)):
    """Recent webhook delivery attempts (ring buffer) so customers can debug
    their integration: event, target URL, HTTP status, attempts."""
    return {"deliveries": notifications.list_deliveries()}


@router.post("/api/notifications/test", tags=["Notifications"])
async def test_notification(payload: dict, _scope: str = Depends(require_authed)):
    """Send a test email + webhook to verify customer setup."""
    sample = {
        "session_id": "test-session",
        "visitor_name": "Test Visitor",
        "visitor_email": payload.get("test_email") or "test@example.com",
        "company_name": "Acme Inc",
        "persona_name": "Aiza",
        "lead_summary": {"lead_score": 80, "next_best_action": "Follow up within 24h"},
        "product_interests": ["pricing", "demo"],
        "transcript": [
            {"role": "user", "text": "Hi, I'm interested in your platform"},
            {"role": "assistant", "text": "Great! Tell me about your team size."},
        ],
    }
    await notifications.notify_lead_captured(sample)
    return {"status": "sent", "preview": sample}


@router.get("/api/meetings", tags=["Meetings"])
async def list_meetings(_scope: str = Depends(require_authed)):
    return {"meetings": meeting_store.list_meetings()}


@router.get("/api/meetings/export", tags=["Meetings"])
async def export_meetings(_scope: str = Depends(require_authed)):
    csv_data = meeting_store.export_csv()
    return PlainTextResponse(
        csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=meeting-requests.csv"},
    )


@router.post("/api/meetings", tags=["Meetings"])
async def create_meeting_request(req: MeetingRequest, request: Request):
    payload = req.model_dump()
    meeting = meeting_store.create_meeting(payload)
    sess = store.get(req.session_id) if req.session_id else None
    if sess is not None:
        meetings = sess.get("meeting_requests") or []
        meetings.append(meeting)
        patch = {"meeting_requests": meetings}
        if req.visitor_name:
            patch["visitor_name"] = req.visitor_name
        if req.visitor_email:
            patch["visitor_email"] = req.visitor_email
        store.update(req.session_id, **patch)
    tenant = tenants.get_current_tenant_optional(request)
    if tenant and meeting.get("visitor_email") and meeting.get("preferred_time"):
        gc = (tenant.get("integrations") or {}).get("google_calendar") or {}
        if gc.get("refresh_token") or gc.get("access_token"):
            try:
                start = meeting["preferred_time"]
                from datetime import datetime as _dt, timedelta as _td
                start_dt = _dt.fromisoformat(start.replace("Z", "+00:00"))
                end_dt = start_dt + _td(minutes=30)
                event = await google_calendar.create_event(
                    tenant,
                    summary=f"Savant lead: {meeting.get('visitor_name') or meeting.get('visitor_email')}",
                    start_iso=start_dt.isoformat(),
                    end_iso=end_dt.isoformat(),
                    attendee_emails=[meeting["visitor_email"]] + ([tenant.get("email")] if tenant.get("email") else []),
                    description=meeting.get("topic") or "Meeting booked via Aiza",
                )
                if event:
                    meeting["calendar_event_id"] = event.get("id")
                    meeting["calendar_event_link"] = event.get("htmlLink")
                    meeting_store.update_meeting(meeting["meeting_id"], meeting.get("status") or "booked", meeting.get("notes"))
            except Exception:
                logger.exception("Failed to create Google Calendar event")
    asyncio.create_task(notifications.notify_meeting_requested(meeting))
    return {"meeting": meeting}


@router.patch("/api/meetings/{meeting_id}", tags=["Meetings"])
async def update_meeting_request(
    meeting_id: str,
    req: MeetingStatusRequest,
    _user: str = Depends(auth.verify_admin),
):
    meeting = meeting_store.update_meeting(meeting_id, req.status, req.notes)
    if not meeting:
        raise HTTPException(404, "Meeting request not found")
    return {"meeting": meeting}



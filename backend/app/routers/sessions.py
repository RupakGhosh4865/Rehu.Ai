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


@router.patch("/api/sessions/{session_id}/language", tags=["Sessions"])
async def update_session_language(session_id: str, req: UpdateSessionLanguageRequest):
    session = _owned_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    # Keep the accent variant (en-GB) so idiom hints follow; the voice itself is
    # fixed at session start — switching accents mid-call changes STT + wording only.
    locale = languages.normalize_locale(req.language)
    session["language"] = locale
    return {
        "session_id": session_id,
        "language": locale,
        "language_name": languages.language_name(languages.base_language(locale)),
    }


@router.post("/api/sessions", tags=["Sessions"])
async def create_session(req: CreateSessionRequest, request: Request):
    """
    Full session creation flow:
    1. Enforce tenant plan limits (minutes/month) when authenticated
    2. Generate opening pitch from knowledge base
    3. Create LiveAvatar context (system prompt + opening text)
    4. Create session token
    5. Start session -> get LiveKit credentials
    6. Return LiveKit URL + token to frontend
    """
    viewer = tenants.get_current_tenant_optional(request)
    tenant = viewer
    # Homepage "Talk to Aiza" demo (slot=hero): a SIGNED-IN visitor runs on their
    # OWN tenant — their trial minutes, their dashboard, their logs (the
    # controlled self-serve experience). Only anonymous hero sessions fall back
    # to the configured demo workspace so the founder's content powers them.
    if (viewer is None) and settings.DEMO_TENANT_ID and (req.metadata or {}).get("slot") == "hero":
        demo_tenant = tenants.get_tenant(settings.DEMO_TENANT_ID)
        if demo_tenant:
            tenants.set_active_tenant(settings.DEMO_TENANT_ID)
            tenant = demo_tenant
    # Personalize: Aiza already "knows" the signed-in visitor (name + email from
    # signup), so she greets them by name and never re-asks for details we have.
    if not req.visitor_name and viewer:
        _first = (viewer.get("name") or "").strip().split()
        if _first:
            req.visitor_name = _first[0]
    if not req.visitor_email and viewer:
        req.visitor_email = (viewer.get("email") or "").strip() or req.visitor_email
    # Avatar-minute cap (the margin firewall). When the monthly pool is used up we
    # either block with a clear upgrade signal, or — if configured — let the call
    # run in voice/text mode (near-zero cost) instead of streaming the avatar.
    avatar_decision = metering.evaluate_avatar(tenant)
    avatar_disabled = False
    if not avatar_decision.allowed:
        if avatar_decision.fallback in ("chat", "voice"):
            # Pool exhausted -> degrade to TEXT CHAT (near-zero cost) instead of
            # going dark. Aiza keeps qualifying/selling; voice returns when the
            # tenant buys minutes or the period resets.
            avatar_disabled = True
            logger.info("Avatar capped for tenant %s — running in chat mode",
                        (tenant or {}).get("tenant_id"))
        else:
            raise HTTPException(status_code=402, detail={
                "code": avatar_decision.code,
                "message": avatar_decision.reason,
                "usage": (metering.usage_summary(tenant) if tenant else None),
            })

    _personas_dict = _get_personas()
    persona = _personas_dict.get(req.persona_id) or _personas_dict.get("default")
    if not persona:
        raise HTTPException(404, f"Persona '{req.persona_id}' not found")

    session_id = str(uuid.uuid4())
    try:
        # Sandbox allows very few concurrent streams — free capacity before each call.
        await _prune_idle_sessions(keep=0 if settings.LIVEAVATAR_USE_SANDBOX else 6)
        # locale keeps the accent variant (en-GB); lang is the base language (en)
        # used wherever a supported language code is required.
        locale = languages.normalize_locale(req.language)
        lang = languages.base_language(locale)

        persona_name = persona.persona_name
        company_name = persona.company_name

        kb_query = _knowledge_query_for_persona(req.persona_id)
        knowledge_ctx = await knowledge.query_knowledge(req.persona_id, kb_query)
        user_ctx_text = _format_user_context(req)
        persona_tone = _persona_tone(persona)
        system_prompt = agent.build_system_prompt(
            persona_name, company_name, knowledge_ctx, persona_tone,
            prompt_override=persona.system_prompt_override,
            language=locale,
            calendly_url=getattr(persona, "calendly_url", None),
            user_context=user_ctx_text or None,
        )
        if len(system_prompt) > _MAX_SYSTEM_PROMPT_CHARS:
            system_prompt = system_prompt[:_MAX_SYSTEM_PROMPT_CHARS] + "…"

        fallback = _opening_fallback_for_persona(req.persona_id)
        opening_text = (
            _localized_aiza_opening(lang, req.visitor_name)
            if req.persona_id == "default"
            else fallback or f"Hi! I'm {persona_name} from {company_name}. How can I help?"
        )
        if req.persona_id != "default" and req.visitor_name and fallback:
            opening_text = fallback.replace("Hi,", f"Hi {req.visitor_name},").replace("Hello,", f"Hello {req.visitor_name},")

        merged_metadata = dict(req.metadata or {})
        flow = merged_metadata.get("flow") or ""
        opening_override = (req.opening_override or merged_metadata.get("opening_override") or "").strip()
        if opening_override:
            opening_text = opening_override[:1200]
        elif flow == "builder" or req.page_context:
            studio_kb = await knowledge.query_knowledge(
                req.persona_id,
                f"{company_name} {req.page_context or ''} products services overview".strip()[:500],
            )
            pitch_ctx = studio_kb or knowledge_ctx
            pitch_fallback = (
                f"Hi — I'm {persona_name}, your {company_name} Superhuman. "
                f"{'I help with ' + req.page_context.split('Product:')[-1].split('.')[0].strip() + '. ' if req.page_context and 'Product:' in req.page_context else ''}"
                "Ask me anything about what we offer."
            )
            opening_text = await agent.generate_opening_pitch(
                persona_name, company_name, pitch_ctx,
                visitor_name=req.visitor_name,
                role_hint="demo",
                opening_fallback=pitch_fallback[:400],
                language=locale,
                page_context=req.page_context or None,
            )

        la_context_id = None
        la_session_token = ""
        la_session_id = ""
        livekit_url = ""
        livekit_client_token = ""
        stream_avatar_id = None

        if settings.LIVEAVATAR_API_KEY and not avatar_disabled:
            try:
                la_context_id = await liveavatar.create_context(
                    prompt=system_prompt,
                    opening_text=opening_text,
                    display_name=f"{persona_name} @ {company_name} {session_id[:8]}",
                )

                requested_avatar, stream_voice = _stream_avatar_and_voice(
                    req.persona_id, persona, locale, voice_candidate=req.voice,
                )
                stream = await liveavatar.provision_stream(
                    avatar_id=requested_avatar,
                    context_id=la_context_id,
                    voice_id=stream_voice,
                    language=lang,
                    is_sandbox=settings.LIVEAVATAR_USE_SANDBOX,
                )
                if not stream and "concurrency" in liveavatar.last_api_error().lower():
                    logger.warning("LiveAvatar concurrency limit — retrying after cleanup")
                    await _prune_idle_sessions(keep=0)
                    await asyncio.sleep(2.5)
                    stream = await liveavatar.provision_stream(
                        avatar_id=requested_avatar,
                        context_id=la_context_id,
                        voice_id=stream_voice,
                        language=lang,
                        is_sandbox=settings.LIVEAVATAR_USE_SANDBOX,
                    )
                stream_avatar_id = requested_avatar
                if stream:
                    la_session_token     = stream["session_token"]
                    la_session_id        = stream["la_session_id"]
                    livekit_url          = stream["livekit_url"]
                    livekit_client_token = stream["livekit_client_token"]
                    stream_avatar_id     = stream.get("stream_avatar_id") or requested_avatar
                else:
                    logger.error(
                        "LiveAvatar provision failed for %s (persona=%s): %s",
                        session_id[:8], req.persona_id, liveavatar.last_api_error(),
                    )
            except Exception as e:
                logger.warning(
                    "LiveAvatar setup failed for %s (persona=%s): %s",
                    session_id[:8], req.persona_id, e,
                )

        user_block = {
            "user_id":      req.user_id or merged_metadata.get("user_id"),
            "user_plan":    req.user_plan or merged_metadata.get("user_plan"),
            "user_stage":   req.user_stage or merged_metadata.get("user_stage"),
            "page_context": req.page_context or merged_metadata.get("page_context"),
        }
        user_block = {k: v for k, v in user_block.items() if v}
        if user_block:
            merged_metadata["user"] = user_block
        if user_ctx_text:
            merged_metadata["user_context"] = user_ctx_text

        store.create(session_id, {
            "persona_id":         req.persona_id,
            "persona_name":       persona_name,
            "company_name":       company_name,
            "tone":               persona_tone,
            "visitor_name":       req.visitor_name,
            "visitor_email":      req.visitor_email,
            "language":           locale,
            "started_at":         datetime.now(timezone.utc).isoformat(),
            "transcript":         [],
            "metadata":           merged_metadata,
            "consent":            (req.metadata or {}).get("consent", {}),
            "meeting_requests":   [],
            "product_interests":  [],
            "lead_summary":       {},
            "la_session_id":      la_session_id,
            "la_session_token":   la_session_token,
            "la_context_id":      la_context_id,
            "opening_text":       opening_text,
            "tenant_id":          tenants.active_tenant_id(),
            "used_avatar":        bool(la_session_id),   # only avatar sessions consume the metered pool
            "avatar_disabled":    avatar_disabled,
            "last_activity_at":   time.time(),           # for idle-kill
        })

        if opening_text:
            _append_transcript(session_id, "assistant", opening_text)

        if la_session_id:
            _runtime(session_id)["keepalive_task"] = asyncio.create_task(_liveavatar_keepalive_loop(session_id))

        if not avatar_disabled and (not livekit_url or not livekit_client_token):
            # Session never actually started — drop it without running the
            # finalize/metering pipeline (nothing was consumed).
            store.remove(session_id)
            rt = _local_runtime.pop(session_id, {})
            if rt.get("keepalive_task"):
                rt["keepalive_task"].cancel()
            if not settings.LIVEAVATAR_API_KEY:
                raise HTTPException(
                    503,
                    "LiveAvatar is not configured. Set LIVEAVATAR_API_KEY to start a session.",
                )
            err = liveavatar.last_api_error() or ""
            if "insufficient credits" in err.lower():
                # The account ran dry: visitors get a graceful message (never the
                # raw billing error) and the founder gets an immediate alert.
                _alert_avatar_credits_exhausted(err)
                raise HTTPException(503, (
                    "Aiza is temporarily unavailable. Please try again in a "
                    "little while — the team has been notified."
                ))
            if "concurrency" in err.lower():
                detail = (
                    "LiveAvatar sandbox allows one active call at a time. "
                    "End any open call, wait a few seconds, and try again."
                )
            else:
                detail = err or "LiveAvatar session could not start. Please try again in a moment."
            raise HTTPException(502, detail)

        return {
            "session_id":           session_id,
            "persona_id":           req.persona_id,
            "persona_name":         persona_name,
            "company_name":         company_name,
            "language":             locale,
            "product_cards":        product_cards.get_product_cards(req.persona_id),
            "livekit_url":          livekit_url,
            "livekit_client_token": livekit_client_token,
            "la_session_id":        la_session_id,
            "opening_text":         opening_text,
            "mode":                 "chat" if avatar_disabled else "liveavatar",
            "cap_exhausted":        avatar_disabled,
            "usage":                (metering.usage_summary(tenant) if (tenant and avatar_disabled) else None),
            "idle_timeout_seconds": metering.idle_timeout_seconds(),
            "stream_avatar_id":     stream_avatar_id,
            "sandbox_stream":       settings.LIVEAVATAR_USE_SANDBOX,
            "avatar_disabled":      avatar_disabled,
            "avatar_disabled_reason": (avatar_decision.reason if avatar_disabled else None),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            "create_session failed session=%s persona=%s tenant=%s",
            session_id[:8], req.persona_id, tenants.active_tenant_id(),
        )
        raise HTTPException(502, f"Could not start session: {e}") from e


@router.get("/api/sessions/history/export", tags=["Sessions"])
async def export_session_history(_scope: str = Depends(require_authed)):
    csv_data = session_log_store.export_csv()
    return PlainTextResponse(
        csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sessions.csv"},
    )


@router.get("/api/analytics/summary", tags=["Sessions"])
async def analytics_summary(days: int = 30, _scope: str = Depends(require_authed)):
    """Aggregated tenant analytics (sessions/leads/meetings trend, lead-score
    distribution, languages, interests) over a trailing window."""
    from .. import analytics
    return analytics.summary(days=days)


@router.get("/api/sessions/history", tags=["Sessions"])
async def session_history(_scope: str = Depends(require_authed)):
    return {"sessions": session_log_store.list_history()}


@router.get("/api/sessions/history/{session_id}", tags=["Sessions"])
async def session_history_detail(session_id: str, _scope: str = Depends(require_authed)):
    record = session_log_store.get_session(session_id)
    if not record:
        raise HTTPException(404, "Session not found")
    return record


@router.delete("/api/sessions/history/{session_id}", tags=["Sessions"])
async def delete_session_history(session_id: str, _scope: str = Depends(require_authed)):
    if not session_log_store.delete_session(session_id):
        raise HTTPException(404, "Session not found")
    return {"session_id": session_id, "status": "deleted"}


@router.post("/api/sessions/{session_id}/events", tags=["Sessions"])
async def append_session_event(session_id: str, req: SessionEventRequest):
    if not _owned_session(session_id):
        raise HTTPException(404, "Session not found")
    role = "user" if req.role in ("user", "me") else "assistant"
    _append_transcript(session_id, role, req.text.strip(), req.event_type)
    return {"status": "ok"}


@router.patch("/api/sessions/{session_id}/visitor", tags=["Sessions"])
async def update_session_visitor(session_id: str, req: UpdateVisitorRequest):
    session = _owned_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if req.visitor_name is not None:
        session["visitor_name"] = req.visitor_name.strip() or None
    if req.visitor_email is not None:
        session["visitor_email"] = req.visitor_email.strip() or None
    return {
        "session_id": session_id,
        "visitor_name": session.get("visitor_name"),
        "visitor_email": session.get("visitor_email"),
    }


@router.get("/api/sessions/{session_id}/visual", tags=["Sessions"])
async def session_visual(session_id: str, topic: str = ""):
    session = _owned_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    card = product_cards.match_product_card(session["persona_id"], topic)
    if card:
        interests = session.setdefault("product_interests", [])
        if card.get("title") and card.get("title") not in interests:
            interests.append(card.get("title"))
    return {"product_card": card}


@router.post("/api/sessions/{session_id}/liveavatar/reconnect", tags=["Sessions"])
async def reconnect_liveavatar_session(session_id: str):
    session = _owned_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if not settings.LIVEAVATAR_API_KEY:
        raise HTTPException(400, "LiveAvatar is not configured")

    keepalive_task = session.get("keepalive_task")
    if keepalive_task:
        keepalive_task.cancel()
        with suppress(asyncio.CancelledError):
            await keepalive_task
        session["keepalive_task"] = None

    if session.get("la_session_id"):
        await liveavatar.stop_session(session["la_session_id"], session.get("la_session_token", ""))

    persona_id = session.get("persona_id", "default")
    personas = _get_personas()
    persona = personas.get(persona_id, personas.get("default"))
    avatar_id, voice_id = _stream_avatar_and_voice(persona_id, persona)

    context_id = session.get("la_context_id")
    if not context_id:
        kb_query = _knowledge_query_for_persona(persona_id)
        knowledge_ctx = await knowledge.query_knowledge(persona_id, kb_query)
        system_prompt = agent.build_system_prompt(
            session.get("persona_name", persona.persona_name),
            session.get("company_name", persona.company_name),
            knowledge_ctx,
            session.get("tone", persona.tone.value),
            prompt_override=persona.system_prompt_override,
            language=session.get("language", "en"),
            calendly_url=getattr(persona, "calendly_url", None),
        )
        context_id = await liveavatar.create_context(
            prompt=system_prompt,
            opening_text="",
            display_name=f"{session.get('persona_name', persona.persona_name)} reconnect {session_id[:8]}",
        )
        session["la_context_id"] = context_id

    token_data = await liveavatar.create_session_token(
        avatar_id=avatar_id,
        context_id=context_id,
        voice_id=voice_id,
        language=session.get("language", "en"),
        is_sandbox=settings.LIVEAVATAR_USE_SANDBOX,
    )
    if not token_data:
        raise HTTPException(502, "Could not create LiveAvatar session token")

    start_data = await liveavatar.start_session(token_data["session_token"])
    if not start_data:
        raise HTTPException(502, "Could not restart LiveAvatar session")

    session["la_session_token"] = token_data["session_token"]
    session["la_session_id"] = start_data["session_id"]
    session["keepalive_task"] = asyncio.create_task(_liveavatar_keepalive_loop(session_id))
    session["last_reconnected_at"] = datetime.now(timezone.utc).isoformat()

    return {
        "session_id": session_id,
        "livekit_url": start_data["livekit_url"],
        "livekit_client_token": start_data["livekit_client_token"],
        "la_session_id": start_data["session_id"],
        "sandbox_stream": settings.LIVEAVATAR_USE_SANDBOX,
    }


@router.post("/api/sessions/{session_id}/message", tags=["Sessions"])
async def session_text_message(session_id: str, req: SessionMessageRequest):
    """Log typed messages; LiveAvatar delivery happens client-side via LiveKit."""
    if not _owned_session(session_id):
        raise HTTPException(404, "Session not found")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "Message text required")
    _append_transcript(session_id, "user", text)
    return {"status": "ok", "logged": True}


@router.post("/api/sessions/{session_id}/respond", tags=["Sessions"])
async def session_respond(session_id: str, req: SessionMessageRequest):
    """Run one visitor turn through the orchestrator ('Neural Engine').

    This is the brain we own: it grounds in the knowledge base, reasons with
    tool-calling (capture lead, book meeting, escalate), tracks the playbook
    stage, and scores the lead live. Returns the words for the avatar/voice to
    speak plus the actions it took. (Step 3 will feed this to the avatar's
    speak channel; the voice path can call it directly today.)
    """
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "Message text required")

    tenants.set_active_tenant(session.get("tenant_id") or tenants.DEFAULT_TENANT_ID)

    # conv_state is process-local (not serializable). On whichever worker handles
    # this turn, rebuild it from the durable session if we don't already hold it.
    rt = _runtime(session_id)
    state: orchestrator.ConversationState = rt.get("conv_state")
    if state is None:
        persona = _get_personas().get(session["persona_id"]) or _get_personas().get("default")
        state = orchestrator.ConversationState(
            persona_name=session.get("persona_name", "Aiza"),
            company_name=session.get("company_name", "our company"),
            tone=session.get("tone", "professional"),
            language=session.get("language", "en"),
            prompt_override=getattr(persona, "system_prompt_override", None) if persona else None,
            extra_context=(session.get("metadata") or {}).get("user_context"),
        )
        if session.get("visitor_name"):
            state.lead["name"] = session["visitor_name"]
        if session.get("visitor_email"):
            state.lead["email"] = session["visitor_email"]
        rt["conv_state"] = state

    # In live-avatar mode the client already logs the spoken transcript, so we
    # skip persistence here to avoid duplicate lines — we still run the brain.
    if not req.actions_only:
        _append_transcript(session_id, "user", text)
    knowledge_fn = orchestrator_tools.knowledge_fn_for(session["persona_id"])
    # Handlers mutate `session` in place (meeting_requests, product_interests, …);
    # persist those mutations back to the durable store after the turn.
    handlers = orchestrator_tools.build_handlers(session, session_id, state)
    result = await orchestrator.respond(state, text, knowledge_fn=knowledge_fn, tool_handlers=handlers)

    if not req.actions_only:
        _append_transcript(session_id, "assistant", result["reply"])
    if state.lead.get("email"):
        session["visitor_email"] = state.lead["email"]
    session["lead_score"] = result["score"]
    store.update(session_id, **{k: v for k, v in session.items()
                                if k not in ("transcript",)})  # transcript persisted via _append_transcript
    return result


@router.post("/api/sessions/{session_id}/present", tags=["Sessions"])
async def session_present(session_id: str, req: PresentRequest):
    """Build a guided slide presentation: the matching deck plus one spoken
    narration line per slide, grounded in the persona's knowledge. The client
    shows slide i while the avatar narrates line i — synced, 1mind-style.
    """
    session = store.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    tenants.set_active_tenant(session.get("tenant_id") or tenants.DEFAULT_TENANT_ID)

    persona_id = session["persona_id"]
    topic = (req.topic or "overview").strip()
    card = product_cards.match_product_card(persona_id, topic)
    if not card:
        raise HTTPException(404, "No presentation deck configured for this persona")

    images = list(card.get("slide_images") or [])
    if not images and card.get("image_url"):
        images = [card["image_url"]]
    if not images:
        # No slide images — still narrate the card's points as a text presentation.
        images = [""] * max(1, len(card.get("bullets") or [1]))

    knowledge_ctx = await knowledge.query_knowledge(persona_id, topic or card.get("title", ""))
    narration = await orchestrator.build_presentation(
        session.get("persona_name", "Aiza"),
        session.get("company_name", "our company"),
        card, knowledge_ctx, len(images),
        language=session.get("language", "en"),
    )
    return {
        "title": card.get("title", "Overview"),
        "eyebrow": card.get("eyebrow", ""),
        "slides": [{"image": images[i], "narration": narration[i]} for i in range(len(images))],
    }


@router.delete("/api/sessions/{session_id}", tags=["Sessions"])
async def end_session(session_id: str):
    if not _owned_session(session_id):
        raise HTTPException(404, "Session not found")
    await _cleanup_session(session_id)
    return {"session_id": session_id, "status": "ended"}


@router.get("/api/sessions", tags=["Sessions"])
async def list_sessions(_user: str = Depends(auth.verify_admin)):
    # Admin-only, and scoped to the active tenant so one tenant cannot enumerate
    # another tenant's live sessions or visitor PII.
    hidden = {"la_session_token", "keepalive_task"}
    active = tenants.active_tenant_id()
    return {"sessions": [{"session_id": sid, **{k: v for k, v in d.items() if k not in hidden}}
                         for sid, d in store.list_for_tenant(active)]}


# ── Voice WebSocket (fallback for voice-only mode) ────────────────────────────


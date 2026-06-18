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


@router.get("/api/personas", tags=["Personas"])
async def list_personas():
    return {"personas": [p.model_dump() for p in _get_personas().values()]}


@router.post("/api/personas", tags=["Personas"])
async def create_persona(config: PersonaConfig, request: Request, _scope: str = Depends(require_authed)):
    # Trial includes a single Superhuman (the default persona, which they edit).
    # Adding more personas is a paid capability.
    if _tenant_plan(request) == "trial":
        raise HTTPException(403, "Your trial includes one Superhuman. Upgrade to add more personas.")
    personas = _get_personas()
    personas[config.persona_id] = config
    persona_store.save_all(personas)
    _invalidate_tenant_persona_cache()
    return {"persona_id": config.persona_id, "status": "created"}


@router.get("/api/personas/{persona_id}", tags=["Personas"])
async def get_persona(persona_id: str):
    p = _get_personas().get(persona_id)
    if not p:
        raise HTTPException(404, f"Persona '{persona_id}' not found")
    return p.model_dump()


@router.get("/api/personas/{persona_id}/experience", tags=["Personas"])
async def get_persona_experience(persona_id: str):
    """Preview image, role title, and immersive connect messages for the call UI."""
    personas = _get_personas()
    p = personas.get(persona_id) or personas.get("default")
    exp = persona_experience.get_experience(persona_id, p.persona_name)
    if settings.LIVEAVATAR_USE_SANDBOX:
        wayne = liveavatar.sandbox_preview_url(_avatar_cache)
        if wayne:
            exp["preview_url"] = wayne
        exp["sandbox_mode"] = True
        exp["stream_avatar_name"] = "Wayne"
    else:
        preview = persona_experience.pick_avatar_preview(persona_id, _avatar_cache, p.persona_name)
        if preview:
            exp["preview_url"] = preview
    exp["persona_name"] = p.persona_name
    exp["company_name"] = p.company_name
    exp["product_cards"] = product_cards.get_product_cards(persona_id)
    exp["calendly_url"] = getattr(p, "calendly_url", None) or ""
    return exp


@router.post("/api/personas/{persona_id}/preview-session", tags=["Personas"])
async def create_preview_session(persona_id: str):
    """
    Idle LiveAvatar stream for hero and landing pages.
    Shows the same persona with natural blinking — video only, no microphone.
    """
    personas = _get_personas()
    persona = personas.get(persona_id) or personas.get("default")
    session_id = str(uuid.uuid4())
    avatar_id, voice_id = _stream_avatar_and_voice(persona_id, persona)

    la_context_id = None
    la_session_token = ""
    la_session_id = ""
    livekit_url = ""
    livekit_client_token = ""
    stream_avatar_id = avatar_id

    if settings.LIVEAVATAR_API_KEY and avatar_id:
        idle_prompt = (
            f"You are {persona.persona_name}. Stay in a calm, professional idle state facing the camera. "
            "Do not speak until the visitor starts a conversation. Maintain natural eye contact."
        )
        la_context_id = await liveavatar.create_context(
            prompt=idle_prompt,
            opening_text="",
            display_name=f"{persona.persona_name} preview {session_id[:8]}",
        )
        stream = await liveavatar.provision_stream(
            avatar_id=avatar_id,
            context_id=la_context_id,
            voice_id=voice_id,
            is_sandbox=settings.LIVEAVATAR_USE_SANDBOX,
        )
        if stream:
            la_session_token = stream["session_token"]
            la_session_id = stream["la_session_id"]
            livekit_url = stream["livekit_url"]
            livekit_client_token = stream["livekit_client_token"]
            stream_avatar_id = stream.get("stream_avatar_id") or avatar_id

    store.create(session_id, {
        "persona_id": persona_id,
        "persona_name": persona.persona_name,
        "company_name": persona.company_name,
        "tone": persona.tone.value,
        "la_session_id": la_session_id,
        "la_session_token": la_session_token,
        "la_context_id": la_context_id,
        "opening_text": "",
        "is_preview": True,
        "tenant_id": tenants.active_tenant_id(),
        "last_activity_at": time.time(),
    })

    if la_session_id:
        _runtime(session_id)["keepalive_task"] = asyncio.create_task(_liveavatar_keepalive_loop(session_id))

    exp = persona_experience.get_experience(persona_id, persona.persona_name)
    return {
        "session_id": session_id,
        "persona_id": persona_id,
        "persona_name": persona.persona_name,
        "avatar_id": avatar_id,
        "preview_url": exp.get("preview_url"),
        "livekit_url": livekit_url,
        "livekit_client_token": livekit_client_token,
        "mode": "liveavatar" if livekit_url else "poster_only",
        "stream_avatar_id": stream_avatar_id,
        "sandbox_stream": settings.LIVEAVATAR_USE_SANDBOX,
    }


@router.put("/api/personas/{persona_id}", tags=["Personas"])
async def update_persona(persona_id: str, config: PersonaConfig, request: Request,
                         _scope: str = Depends(require_authed)):
    config.persona_id = persona_id
    personas = _get_personas()
    # Avatar gating: a plan without avatar_choice (trial/pilot) is locked to the
    # current avatar. Reject a change server-side so the lock can't be bypassed by
    # calling the API directly. Other persona edits (name/tone/knowledge) are free.
    current = personas.get(persona_id)
    new_avatar = getattr(config, "avatar_id", None)
    cur_avatar = getattr(current, "avatar_id", None) if current else None
    if new_avatar and new_avatar != cur_avatar:
        if not tenants.plan_limits(_tenant_plan(request)).get("avatar_choice", False):
            raise HTTPException(403, "Choosing a custom avatar requires a paid plan. Upgrade to unlock the avatar library.")
    personas[persona_id] = config
    persona_store.save_all(personas)
    _invalidate_tenant_persona_cache()
    return {"persona_id": persona_id, "status": "updated"}


@router.delete("/api/personas/{persona_id}", tags=["Personas"])
async def delete_persona(persona_id: str, _scope: str = Depends(require_authed)):
    # Only the core "default" Aiza is protected (so a workspace always has one);
    # every other persona — including seeded examples — the owner may remove.
    if persona_id == "default":
        raise HTTPException(400, "The default Aiza can't be deleted — but you can rename and retrain her.")
    personas = _get_personas()
    if persona_id not in personas:
        raise HTTPException(404, f"Persona '{persona_id}' not found")
    personas.pop(persona_id, None)
    await knowledge.delete_persona_knowledge(persona_id)
    persona_store.save_all(personas)
    persona_store.mark_deleted(persona_id)   # so the seed can't re-add it on reload
    _invalidate_tenant_persona_cache()
    audit.record("persona.delete", actor=_scope or "owner", tenant=tenants.active_tenant_id(), target=persona_id)
    return {"persona_id": persona_id, "status": "deleted"}


# ── Solutions & templates ─────────────────────────────────────────────────────

@router.post("/api/personas/from-template/{slug}", tags=["Personas"])
async def create_persona_from_template(
    slug: str,
    request: Request,
    company_name: Optional[str] = None,
    _scope: str = Depends(require_authed),
):
    """Clone a solution template into a new custom persona (optional company name)."""
    # Vertical templates are a paid feature — trials build their own single Superhuman.
    if _tenant_plan(request) == "trial":
        raise HTTPException(403, "Vertical templates are a paid feature. Upgrade to clone a ready-made persona.")
    t = persona_templates.get_template(slug)
    if not t:
        raise HTTPException(404, f"Template '{slug}' not found")
    new_id = f"{t.persona_id}-custom-{uuid.uuid4().hex[:6]}"
    cfg = t.to_persona_config()
    cfg.persona_id = new_id
    if company_name:
        cfg.company_name = company_name
    personas = _get_personas()
    personas[new_id] = cfg
    persona_store.save_all(personas)
    _invalidate_tenant_persona_cache()
    return {"persona_id": new_id, "status": "created", "template": slug}


@router.get("/api/personas/{persona_id}/product-cards", tags=["Personas"])
async def get_persona_product_cards(persona_id: str):
    return {"persona_id": persona_id, "product_cards": product_cards.get_product_cards(persona_id)}


@router.post("/api/personas/{persona_id}/product-cards", tags=["Personas"])
async def upsert_persona_product_card(
    persona_id: str,
    req: ProductCardRequest,
    _scope: str = Depends(require_authed),
):
    card = product_cards.upsert_product_card(persona_id, req.model_dump(exclude={"persona_id"}))
    return {"persona_id": persona_id, "product_card": card}


@router.delete("/api/personas/{persona_id}/product-cards/{card_id}", tags=["Personas"])
async def delete_persona_product_card(
    persona_id: str,
    card_id: str,
    _scope: str = Depends(require_authed),
):
    if not product_cards.delete_product_card(persona_id, card_id):
        raise HTTPException(404, "Product card not found")
    return {"persona_id": persona_id, "card_id": card_id, "status": "deleted"}


@router.get("/api/avatar/list", tags=["Avatar"])
async def avatar_list():
    """List public LiveAvatar stock avatars (no API key required)."""
    avatars = await liveavatar.list_public_avatars(page_size=40)
    return {"avatars": avatars}


# ── Knowledge Base ────────────────────────────────────────────────────────────


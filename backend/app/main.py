"""
Savant.ai -- FastAPI backend
Uses LiveAvatar (new HeyGen API) for photorealistic streaming avatar via LiveKit.
"""
import logging
import uuid
import os
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

from .config import settings
from .models import (
    CreateSessionRequest, AddKnowledgeRequest,
    KnowledgeQueryRequest, KnowledgeQueryResponse, KnowledgeQueryResult,
    HealthResponse, PersonaConfig,
    SessionEventRequest, UpdateVisitorRequest, UpdateSessionLanguageRequest, SessionMessageRequest,
    MeetingRequest, MeetingStatusRequest, ProductCardRequest, ComplianceSettingsRequest,
    SignupRequest, LoginRequest, TenantUpdateRequest, BillingCheckoutRequest, IntegrationConnectRequest,
    RideAlongJoinRequest, RideAlongSpeakRequest, LeadCaptureRequest,
)
from . import liveavatar, agent, knowledge, persona_templates, persona_experience, persona_store, auth
from . import session_log_store, languages, product_cards, meeting_store
from . import notifications, tenants, billing, hubspot, google_calendar
from . import meeting_bot, ride_along_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s -- %(message)s")
logger = logging.getLogger(__name__)

_sessions: dict[str, dict] = {}
_avatar_cache: list = []
_default_template = persona_templates.get_template("sales-demo")
KEEPALIVE_INTERVAL_SECONDS = 25


def _seed_personas() -> dict[str, PersonaConfig]:
    return {
        "default": PersonaConfig(
            persona_id="default",
            persona_name=settings.DEFAULT_PERSONA_NAME,
            company_name="our company",
            system_prompt_override=_default_template.system_prompt if _default_template else None,
        ),
        **persona_templates.get_persona_configs(),
    }


_personas: dict[str, PersonaConfig] = _seed_personas()
_tenant_personas_cache: dict[str, dict[str, PersonaConfig]] = {}


def _get_personas() -> dict[str, PersonaConfig]:
    """Return the active tenant's personas, loading from disk on first access."""
    tid = tenants.active_tenant_id()
    if tid == tenants.DEFAULT_TENANT_ID:
        return _personas
    cached = _tenant_personas_cache.get(tid)
    if cached is None:
        seed = _seed_personas()
        try:
            saved = persona_store.load_all()
        except Exception:
            saved = {}
        cached = persona_store.merge_into(seed, saved)
        _tenant_personas_cache[tid] = cached
    return cached


def _invalidate_tenant_persona_cache() -> None:
    tid = tenants.active_tenant_id()
    _tenant_personas_cache.pop(tid, None)


def _save_personas() -> None:
    persona_store.save_all(_personas)


def _format_user_context(req) -> str:
    """Render an in-product user context line for the system prompt.

    Example: "Logged-in user on Pricing page. Plan: free. Stage: trial."
    Returns "" when no fields are set so the prompt template omits the line.
    """
    parts: list[str] = []
    if getattr(req, "user_id", None):
        parts.append("Logged-in user")
    if getattr(req, "page_context", None):
        location = "on " + req.page_context.strip()
        parts[0] = f"{parts[0]} {location}" if parts else f"Visitor {location}"
    extras: list[str] = []
    if getattr(req, "user_plan", None):
        extras.append(f"Plan: {req.user_plan.strip()}")
    if getattr(req, "user_stage", None):
        extras.append(f"Stage: {req.user_stage.strip()}")
    if not parts and not extras:
        return ""
    head = ". ".join(parts) if parts else "Visitor"
    if extras:
        return f"{head}. {'. '.join(extras)}."
    return f"{head}."


def _apply_avatar_bindings() -> None:
    global _personas
    for pid, p in list(_personas.items()):
        b = persona_experience.get_avatar_binding(pid)
        if b and b.get("avatar_id"):
            _personas[pid] = p.model_copy(update={
                "avatar_id": b["avatar_id"],
                "voice_id": b.get("voice_id") or p.voice_id,
            })
            logger.info("Persona %s -> LiveAvatar %s (%s)", pid, b["avatar_id"][:8], b.get("avatar_name"))


def _role_hint_for_persona(persona_id: str) -> str:
    hints = {
        "hr-interviewer": "hr",
        "onboarding-guide": "onboarding",
        "support-agent": "support",
        "human-chatbot": "support",
        "demo-host": "demo",
        "product-demo": "demo",
        "healthcare-guide": "support",
        "meeting-assistant": "demo",
    }
    return hints.get(persona_id, "sales")


def _opening_fallback_for_persona(persona_id: str) -> str:
    for t in persona_templates.get_all_templates():
        if t.persona_id == persona_id:
            return t.opening_fallback
    return ""


def _knowledge_query_for_persona(persona_id: str) -> str:
    for t in persona_templates.get_all_templates():
        if t.persona_id == persona_id:
            return t.knowledge_query
    return "overview products services features pricing"


def _localized_aiza_opening(lang: str, visitor_name: Optional[str] = None) -> str:
    name_part = {
        "en": f"Hey {visitor_name}!" if visitor_name else "Hey!",
        "zh": f"你好，{visitor_name}！" if visitor_name else "你好！",
        "ar": f"مرحبًا {visitor_name}!" if visitor_name else "مرحبًا!",
        "es": f"Hola, {visitor_name}!" if visitor_name else "Hola!",
        "fr": f"Bonjour {visitor_name}!" if visitor_name else "Bonjour!",
        "hi": f"नमस्ते {visitor_name}!" if visitor_name else "नमस्ते!",
        "te": f"నమస్తే {visitor_name}!" if visitor_name else "నమస్తే!",
        "ja": f"こんにちは、{visitor_name}さん！" if visitor_name else "こんにちは！",
        "de": f"Hallo {visitor_name}!" if visitor_name else "Hallo!",
        "pt": f"Olá, {visitor_name}!" if visitor_name else "Olá!",
    }.get(lang, "Hey!")
    openings = {
        "en": "I'm Aiza — your personal expert here at savant.ai. What brings you in today?",
        "zh": "我是 Aiza，你在 savant.ai 的个人专家。今天想了解什么？",
        "ar": "أنا Aiza — خبيرتك الشخصية هنا في savant.ai. ما الذي أتى بك اليوم؟",
        "es": "Soy Aiza, tu experta personal aquí en savant.ai. ¿Qué te trae por aquí hoy?",
        "fr": "Je suis Aiza, votre experte personnelle chez savant.ai. Qu'est-ce qui vous amène aujourd'hui ?",
        "hi": "मैं Aiza हूँ — savant.ai पर आपकी निजी विशेषज्ञ। आज आप क्या जानना चाहेंगे?",
        "te": "నేను Aiza — savant.ai లో మీ వ్యక్తిగత నిపుణిని. ఈ రోజు మీకు ఏమి తెలుసుకోవాలి?",
        "ja": "私は Aiza、savant.ai のあなた専属のエキスパートです。今日は何を知りたいですか？",
        "de": "Ich bin Aiza, deine persönliche Expertin hier bei savant.ai. Was führt dich heute zu uns?",
        "pt": "Eu sou Aiza, sua especialista pessoal aqui na savant.ai. O que traz você hoje?",
    }
    return f"{name_part} {openings.get(lang, openings['en'])}"


def _stream_avatar_and_voice(persona_id: str, persona: PersonaConfig) -> tuple[Optional[str], Optional[str]]:
    """Sandbox always uses Wayne + default voice so video and speech both work."""
    if settings.LIVEAVATAR_USE_SANDBOX:
        return liveavatar.SANDBOX_AVATAR_ID, None
    aid = persona.avatar_id or persona_experience.resolve_avatar_id(persona_id)
    vid = persona.voice_id or persona_experience.resolve_voice_id(persona_id)
    return aid, vid


def _build_lead_summary(session: dict) -> dict:
    transcript = session.get("transcript") or []
    text = " ".join((m.get("text") or "") for m in transcript).lower()
    meeting_count = len(session.get("meeting_requests") or [])
    product_interests = session.get("product_interests") or []
    score = 20
    if session.get("visitor_email"):
        score += 20
    if meeting_count:
        score += 35
    if any(word in text for word in ("pricing", "price", "cost", "budget", "quote")):
        score += 15
    if any(word in text for word in ("demo", "book", "meeting", "calendar", "call", "contact")):
        score += 20
    score = min(score, 100)
    next_action = "Review transcript and follow up"
    if meeting_count:
        next_action = "Contact visitor to confirm requested meeting"
    elif score >= 70:
        next_action = "Prioritize sales follow-up"
    elif product_interests:
        next_action = "Send product/service information"
    return {
        "lead_score": score,
        "intent": "meeting_requested" if meeting_count else ("high_interest" if score >= 70 else "researching"),
        "product_interests": product_interests,
        "meeting_requested": bool(meeting_count),
        "next_best_action": next_action,
        "summary": " ".join((m.get("text") or "") for m in transcript[:6])[:700],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    logger.info("LiveAvatar API key set: %s", bool(settings.LIVEAVATAR_API_KEY))
    logger.info("Sandbox mode: %s", settings.LIVEAVATAR_USE_SANDBOX)
    global _avatar_cache, _personas
    _avatar_cache = await liveavatar.list_public_avatars(page_size=48)
    persona_experience.bind_live_avatars(_avatar_cache)
    _personas = persona_store.merge_into(_seed_personas(), persona_store.load_all())
    _apply_avatar_bindings()
    logger.info("Cached %d public avatars for previews", len(_avatar_cache))
    yield
    logger.info("Shutting down -- cleaning up %d sessions", len(_sessions))
    for sid in list(_sessions.keys()):
        await _cleanup_session(sid)


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    for subdir, mount in [("assets", "/static"), ("sdk", "/sdk")]:
        d = os.path.join(FRONTEND_DIR, subdir)
        if os.path.isdir(d):
            app.mount(mount, StaticFiles(directory=d), name=subdir)


async def _cleanup_session(session_id: str):
    session = _sessions.pop(session_id, None)
    if not session:
        return
    keepalive_task = session.get("keepalive_task")
    if keepalive_task:
        keepalive_task.cancel()
        with suppress(asyncio.CancelledError):
            await keepalive_task
    if not session.get("is_preview"):
        session["ended_at"] = datetime.now(timezone.utc).isoformat()
        session["lead_summary"] = _build_lead_summary(session)
        record = {
            "session_id": session_id,
            "persona_id": session.get("persona_id"),
            "persona_name": session.get("persona_name"),
            "company_name": session.get("company_name"),
            "visitor_name": session.get("visitor_name"),
            "visitor_email": session.get("visitor_email"),
            "language": session.get("language", "en"),
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at"),
            "transcript": session.get("transcript", []),
            "consent": session.get("consent", {}),
            "lead_summary": session.get("lead_summary", {}),
            "meeting_requests": session.get("meeting_requests", []),
            "product_interests": session.get("product_interests", []),
            "metadata": session.get("metadata", {}),
        }
        session_log_store.finalize_session(record)
        tenant_id = session.get("tenant_id")
        if tenant_id and tenant_id != tenants.DEFAULT_TENANT_ID:
            try:
                started = session.get("started_at")
                ended = session.get("ended_at")
                from datetime import datetime as _dt
                if started and ended:
                    sec = (_dt.fromisoformat(ended.replace("Z", "+00:00")) -
                           _dt.fromisoformat(started.replace("Z", "+00:00"))).total_seconds()
                    tenants.add_minutes_used(tenant_id, max(0.0, sec / 60.0))
            except Exception:
                logger.exception("Could not record minutes for tenant %s", tenant_id)
        if record.get("visitor_email") or (record.get("lead_summary") or {}).get("lead_score"):
            asyncio.create_task(notifications.notify_lead_captured(record))
        asyncio.create_task(notifications.notify_session_ended(record))
    await agent.stop_voice_pipeline(session_id)
    if session.get("la_session_id") and session.get("la_session_token"):
        await liveavatar.stop_session(session["la_session_id"], session["la_session_token"])
    if session.get("la_context_id"):
        await liveavatar.delete_context(session["la_context_id"])
    logger.info("Session %s cleaned up", session_id)


async def _liveavatar_keepalive_loop(session_id: str):
    """Keep provider sessions alive during quiet periods until cleanup cancels the task."""
    try:
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL_SECONDS)
            session = _sessions.get(session_id)
            if not session:
                return
            la_session_id = session.get("la_session_id")
            if not la_session_id:
                return
            ok = await liveavatar.keep_session_alive(la_session_id)
            session["last_keepalive_at"] = datetime.now(timezone.utc).isoformat()
            session["last_keepalive_ok"] = ok
            if not ok:
                logger.warning("LiveAvatar keep-alive failed for app session %s", session_id)
    except asyncio.CancelledError:
        raise


def _append_transcript(session_id: str, role: str, text: str, event_type: str = "transcript") -> bool:
    session = _sessions.get(session_id)
    if not session or not text:
        return False
    session.setdefault("transcript", []).append({
        "role": role,
        "text": text,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return True


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health():
    return HealthResponse(
        status="healthy",
        version=settings.APP_VERSION,
        services={
            "active_sessions": len(_sessions),
            "sandbox_mode": settings.LIVEAVATAR_USE_SANDBOX,
            "liveavatar_key_set": bool(settings.LIVEAVATAR_API_KEY),
        },
    )


@app.get("/", tags=["System"])
async def root():
    # Serve marketing homepage first; fall back to call interface
    homepage = os.path.join(FRONTEND_DIR, "homepage.html")
    if os.path.isfile(homepage):
        return FileResponse(homepage)
    idx = os.path.join(FRONTEND_DIR, "index.html")
    return FileResponse(idx) if os.path.isfile(idx) else {"status": "running", "version": settings.APP_VERSION}

@app.get("/call", tags=["System"])
async def call_page():
    idx = os.path.join(FRONTEND_DIR, "index.html")
    return FileResponse(idx) if os.path.isfile(idx) else {"status": "call interface not found"}


@app.get("/solutions/{slug}", tags=["System"])
async def solution_page(slug: str):
    if not persona_templates.get_template(slug):
        raise HTTPException(404, f"Solution '{slug}' not found")
    p = os.path.join(FRONTEND_DIR, "solution.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Solution page not found")


@app.get("/admin", tags=["System"])
async def admin(_user: str = Depends(auth.verify_admin)):
    p = os.path.join(FRONTEND_DIR, "admin.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Admin panel not found")


@app.get("/signup", tags=["System"])
async def signup_page():
    p = os.path.join(FRONTEND_DIR, "signup.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Signup page not found")


@app.get("/login", tags=["System"])
async def login_page():
    p = os.path.join(FRONTEND_DIR, "login.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Login page not found")


@app.get("/onboarding", tags=["System"])
async def onboarding_page():
    p = os.path.join(FRONTEND_DIR, "onboarding.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Onboarding page not found")


@app.get("/pricing", tags=["System"])
async def pricing_page():
    p = os.path.join(FRONTEND_DIR, "pricing.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Pricing page not found")


# ── Personas ──────────────────────────────────────────────────────────────────

@app.get("/api/personas", tags=["Personas"])
async def list_personas():
    return {"personas": [p.model_dump() for p in _get_personas().values()]}


@app.post("/api/personas", tags=["Personas"])
async def create_persona(config: PersonaConfig, _user: str = Depends(auth.verify_admin)):
    personas = _get_personas()
    personas[config.persona_id] = config
    persona_store.save_all(personas)
    _invalidate_tenant_persona_cache()
    return {"persona_id": config.persona_id, "status": "created"}


@app.get("/api/personas/{persona_id}", tags=["Personas"])
async def get_persona(persona_id: str):
    p = _get_personas().get(persona_id)
    if not p:
        raise HTTPException(404, f"Persona '{persona_id}' not found")
    return p.model_dump()


@app.get("/api/personas/{persona_id}/experience", tags=["Personas"])
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


@app.post("/api/personas/{persona_id}/preview-session", tags=["Personas"])
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
        token_data = await liveavatar.create_session_token(
            avatar_id=avatar_id,
            context_id=la_context_id,
            voice_id=voice_id,
            is_sandbox=settings.LIVEAVATAR_USE_SANDBOX,
        )
        if token_data:
            la_session_token = token_data["session_token"]
            stream_avatar_id = token_data.get("avatar_id") or avatar_id
            start_data = await liveavatar.start_session(la_session_token)
            if start_data:
                la_session_id = start_data["session_id"]
                livekit_url = start_data["livekit_url"]
                livekit_client_token = start_data["livekit_client_token"]

    _sessions[session_id] = {
        "persona_id": persona_id,
        "persona_name": persona.persona_name,
        "company_name": persona.company_name,
        "tone": persona.tone.value,
        "la_session_id": la_session_id,
        "la_session_token": la_session_token,
        "la_context_id": la_context_id,
        "opening_text": "",
        "is_preview": True,
    }

    if la_session_id:
        _sessions[session_id]["keepalive_task"] = asyncio.create_task(_liveavatar_keepalive_loop(session_id))

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


@app.put("/api/personas/{persona_id}", tags=["Personas"])
async def update_persona(persona_id: str, config: PersonaConfig, _user: str = Depends(auth.verify_admin)):
    config.persona_id = persona_id
    personas = _get_personas()
    personas[persona_id] = config
    persona_store.save_all(personas)
    _invalidate_tenant_persona_cache()
    return {"persona_id": persona_id, "status": "updated"}


@app.delete("/api/personas/{persona_id}", tags=["Personas"])
async def delete_persona(persona_id: str, _user: str = Depends(auth.verify_admin)):
    protected = {
        "default", "hr-interviewer", "onboarding-guide", "support-agent",
        "human-chatbot", "demo-host", "product-demo", "healthcare-guide", "meeting-assistant",
    }
    if persona_id in protected:
        raise HTTPException(400, f"Cannot delete built-in persona '{persona_id}'")
    personas = _get_personas()
    personas.pop(persona_id, None)
    await knowledge.delete_persona_knowledge(persona_id)
    persona_store.save_all(personas)
    _invalidate_tenant_persona_cache()
    return {"persona_id": persona_id, "status": "deleted"}


# ── Solutions & templates ─────────────────────────────────────────────────────

@app.get("/api/solutions", tags=["Solutions"])
async def list_solutions():
    return {"solutions": [t.to_api_dict() for t in persona_templates.get_all_templates()]}


@app.get("/api/solutions/{slug}", tags=["Solutions"])
async def get_solution(slug: str):
    t = persona_templates.get_template(slug)
    if not t:
        raise HTTPException(404, f"Solution '{slug}' not found")
    data = t.to_api_dict()
    data["system_prompt_preview"] = t.system_prompt[:200] + "…"
    return data


@app.get("/api/templates", tags=["Solutions"])
async def list_templates():
    return {"templates": [t.to_api_dict() for t in persona_templates.get_all_templates()]}


@app.post("/api/personas/from-template/{slug}", tags=["Personas"])
async def create_persona_from_template(
    slug: str,
    company_name: Optional[str] = None,
    _user: str = Depends(auth.verify_admin),
):
    """Clone a solution template into a new custom persona (optional company name)."""
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


@app.get("/api/languages", tags=["System"])
async def list_languages():
    return {"languages": [{"code": k, "name": v} for k, v in languages.SUPPORTED_LANGUAGES.items()]}


@app.patch("/api/sessions/{session_id}/language", tags=["Sessions"])
async def update_session_language(session_id: str, req: UpdateSessionLanguageRequest):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    lang = languages.normalize_language(req.language)
    session["language"] = lang
    return {
        "session_id": session_id,
        "language": lang,
        "language_name": languages.language_name(lang),
    }


@app.get("/api/personas/{persona_id}/product-cards", tags=["Personas"])
async def get_persona_product_cards(persona_id: str):
    return {"persona_id": persona_id, "product_cards": product_cards.get_product_cards(persona_id)}


@app.post("/api/personas/{persona_id}/product-cards", tags=["Personas"])
async def upsert_persona_product_card(
    persona_id: str,
    req: ProductCardRequest,
    _user: str = Depends(auth.verify_admin),
):
    card = product_cards.upsert_product_card(persona_id, req.model_dump(exclude={"persona_id"}))
    return {"persona_id": persona_id, "product_card": card}


@app.delete("/api/personas/{persona_id}/product-cards/{card_id}", tags=["Personas"])
async def delete_persona_product_card(
    persona_id: str,
    card_id: str,
    _user: str = Depends(auth.verify_admin),
):
    if not product_cards.delete_product_card(persona_id, card_id):
        raise HTTPException(404, "Product card not found")
    return {"persona_id": persona_id, "card_id": card_id, "status": "deleted"}


@app.post("/api/uploads/product-slides", tags=["Uploads"])
async def upload_product_slide(
    file: UploadFile = File(...),
    _user: str = Depends(auth.verify_admin),
):
    allowed = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
    }
    ext = allowed.get((file.content_type or "").lower())
    if not ext:
        raise HTTPException(400, "Only PNG, JPG, JPEG, or WEBP slide images are supported")
    uploads_dir = os.path.join(FRONTEND_DIR, "assets", "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    filename = f"product-slide-{uuid.uuid4().hex}{ext}"
    path = os.path.join(uploads_dir, filename)
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "Slide image must be under 10MB")
    with open(path, "wb") as out:
        out.write(content)
    return {"url": f"/static/uploads/{filename}", "filename": filename}


# ── Sessions (LiveAvatar) ─────────────────────────────────────────────────────

@app.post("/api/sessions", tags=["Sessions"])
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
    tenant = tenants.get_current_tenant_optional(request)
    if tenant:
        ok, reason = tenants.can_start_session(tenant)
        if not ok:
            raise HTTPException(402, reason)
    _personas_dict = _get_personas()
    persona    = _personas_dict.get(req.persona_id, _personas_dict.get("default"))
    session_id = str(uuid.uuid4())
    lang       = languages.normalize_language(req.language)

    persona_name = persona.persona_name
    company_name = persona.company_name

    # Build system prompt with knowledge + in-product user context
    kb_query = _knowledge_query_for_persona(req.persona_id)
    knowledge_ctx = await knowledge.query_knowledge(req.persona_id, kb_query)
    user_ctx_text = _format_user_context(req)
    system_prompt = agent.build_system_prompt(
        persona_name, company_name, knowledge_ctx, persona.tone.value,
        prompt_override=persona.system_prompt_override,
        language=lang,
        calendly_url=getattr(persona, "calendly_url", None),
        user_context=user_ctx_text or None,
    )

    # Generate opening pitch (auto-demo)
    role_hint = _role_hint_for_persona(req.persona_id)
    fallback = _opening_fallback_for_persona(req.persona_id)
    opening_text = (
        _localized_aiza_opening(lang, req.visitor_name)
        if req.persona_id == "default"
        else fallback or f"Hi! I'm {persona_name} from {company_name}. How can I help?"
    )
    if req.persona_id != "default" and req.visitor_name and fallback:
        opening_text = fallback.replace("Hi,", f"Hi {req.visitor_name},").replace("Hello,", f"Hello {req.visitor_name},")
    # Use template opening for faster session start (LLM pitch adds 2–4s latency)

    la_context_id = None
    la_session_token = ""
    la_session_id = ""
    livekit_url = ""
    livekit_client_token = ""
    stream_avatar_id = None

    if settings.LIVEAVATAR_API_KEY:
        # Create LiveAvatar context
        la_context_id = await liveavatar.create_context(
            prompt=system_prompt,
            opening_text=opening_text,
            display_name=f"{persona_name} @ {company_name} {session_id[:8]}",
        )

        requested_avatar, stream_voice = _stream_avatar_and_voice(req.persona_id, persona)
        token_data = await liveavatar.create_session_token(
            avatar_id=requested_avatar,
            context_id=la_context_id,
            voice_id=stream_voice,
            language=lang,
            is_sandbox=settings.LIVEAVATAR_USE_SANDBOX,
        )

        stream_avatar_id = requested_avatar
        if token_data:
            la_session_token = token_data["session_token"]
            stream_avatar_id = token_data.get("avatar_id") or requested_avatar

            # Start session to get LiveKit credentials
            start_data = await liveavatar.start_session(la_session_token)
            if start_data:
                la_session_id        = start_data["session_id"]
                livekit_url          = start_data["livekit_url"]
                livekit_client_token = start_data["livekit_client_token"]

    merged_metadata = dict(req.metadata or {})
    user_block = {
        "user_id":      req.user_id or merged_metadata.get("user_id"),
        "user_plan":    req.user_plan or merged_metadata.get("user_plan"),
        "user_stage":   req.user_stage or merged_metadata.get("user_stage"),
        "page_context": req.page_context or merged_metadata.get("page_context"),
    }
    # Drop empty keys so the metadata dict stays tidy
    user_block = {k: v for k, v in user_block.items() if v}
    if user_block:
        merged_metadata["user"] = user_block
    if user_ctx_text:
        merged_metadata["user_context"] = user_ctx_text

    _sessions[session_id] = {
        "persona_id":         req.persona_id,
        "persona_name":       persona_name,
        "company_name":       company_name,
        "tone":               persona.tone.value,
        "visitor_name":       req.visitor_name,
        "visitor_email":      req.visitor_email,
        "language":           lang,
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
    }

    if opening_text:
        _append_transcript(session_id, "assistant", opening_text)

    if la_session_id:
        _sessions[session_id]["keepalive_task"] = asyncio.create_task(_liveavatar_keepalive_loop(session_id))

    return {
        "session_id":           session_id,
        "persona_id":           req.persona_id,
        "persona_name":         persona_name,
        "company_name":         company_name,
        "language":             lang,
        "product_cards":        product_cards.get_product_cards(req.persona_id),
        "livekit_url":          livekit_url,
        "livekit_client_token": livekit_client_token,
        "la_session_id":        la_session_id,
        "opening_text":         opening_text,
        "mode":                 "liveavatar" if livekit_url else "voice_only",
        "stream_avatar_id":     stream_avatar_id if livekit_url else None,
        "sandbox_stream":       settings.LIVEAVATAR_USE_SANDBOX,
    }


@app.get("/api/sessions/history/export", tags=["Sessions"])
async def export_session_history(_user: str = Depends(auth.verify_admin)):
    csv_data = session_log_store.export_csv()
    return PlainTextResponse(
        csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=sessions.csv"},
    )


@app.get("/api/compliance/settings", tags=["Compliance"])
async def get_compliance_settings():
    return session_log_store.load_compliance_settings()


@app.put("/api/compliance/settings", tags=["Compliance"])
async def update_compliance_settings(
    req: ComplianceSettingsRequest,
    _user: str = Depends(auth.verify_admin),
):
    return session_log_store.save_compliance_settings(req.model_dump())


# ── Tenant accounts ───────────────────────────────────────────────────────────

@app.post("/api/auth/signup", tags=["Accounts"])
async def auth_signup(req: SignupRequest):
    try:
        tenant = tenants.create_tenant(req.email, req.password, req.company_name, req.plan or "trial")
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = tenants.issue_token(tenant["tenant_id"])
    tenants.set_active_tenant(tenant["tenant_id"])
    # Seed personas for the new tenant so they have a starting Aiza
    seed = _seed_personas()
    if req.company_name and "default" in seed:
        seed["default"] = seed["default"].model_copy(update={"company_name": req.company_name})
    persona_store.save_all(seed)
    _invalidate_tenant_persona_cache()
    return {"token": token, "tenant": tenant}


@app.post("/api/auth/login", tags=["Accounts"])
async def auth_login(req: LoginRequest):
    tenant = tenants.authenticate(req.email, req.password)
    if not tenant:
        raise HTTPException(401, "Invalid email or password")
    token = tenants.issue_token(tenant["tenant_id"])
    return {"token": token, "tenant": tenants._redact(tenant)}


@app.get("/api/auth/me", tags=["Accounts"])
async def auth_me(request: Request):
    tenant = tenants.get_current_tenant_optional(request)
    if not tenant:
        return {"tenant": None}
    return {"tenant": tenants._redact(tenant)}


@app.post("/api/auth/logout", tags=["Accounts"])
async def auth_logout():
    return {"status": "ok"}


@app.get("/api/account", tags=["Accounts"])
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


@app.patch("/api/account", tags=["Accounts"])
async def update_account(req: TenantUpdateRequest, request: Request):
    tenant = tenants.require_tenant(request)
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if "slug" in patch:
        patch["slug"] = tenants.slugify(patch["slug"]) or tenant.get("slug")
    updated = tenants.update_tenant(tenant["tenant_id"], patch)
    return {"tenant": updated}


# ── Billing (Stripe) ─────────────────────────────────────────────────────────

@app.get("/api/billing/plans", tags=["Billing"])
async def billing_plans():
    return {
        "configured": billing.stripe_configured(),
        "plans": [
            {"id": pid, **info, "monthly_price_usd": _plan_price_usd(pid)}
            for pid, info in tenants.PLAN_DEFAULTS.items()
        ],
    }


def _plan_price_usd(plan: str) -> Optional[int]:
    return {
        "trial": 0,
        "pilot": 997,
        "professional": 1999,
        "business": 3499,
        "enterprise": None,
    }.get(plan)


@app.post("/api/billing/checkout", tags=["Billing"])
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


@app.post("/api/billing/portal", tags=["Billing"])
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


@app.post("/api/billing/webhook", tags=["Billing"], include_in_schema=False)
async def billing_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature") or ""
    if settings.STRIPE_WEBHOOK_SECRET and not _verify_stripe_signature(payload, sig):
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
        return True
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

@app.get("/api/integrations/hubspot/status", tags=["Integrations"])
async def hubspot_status(request: Request):
    tenant = tenants.require_tenant(request)
    hs = (tenant.get("integrations") or {}).get("hubspot") or {}
    return {
        "configured_on_server": hubspot.is_configured(),
        "connected": bool(hs.get("access_token")),
        "scope": hs.get("scope") or "",
    }


@app.post("/api/integrations/hubspot/connect", tags=["Integrations"])
async def hubspot_connect(request: Request):
    tenant = tenants.require_tenant(request)
    if not hubspot.is_configured():
        raise HTTPException(400, "HubSpot OAuth is not configured on this server")
    redirect = settings.HUBSPOT_REDIRECT_URI or f"{settings.APP_BASE_URL.rstrip('/')}/integrations/hubspot/callback"
    return {"url": hubspot.build_authorize_url(tenant["tenant_id"], redirect)}


@app.get("/integrations/hubspot/callback", tags=["Integrations"], include_in_schema=False)
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


@app.delete("/api/integrations/hubspot", tags=["Integrations"])
async def hubspot_disconnect(request: Request):
    tenant = tenants.require_tenant(request)
    integrations = dict(tenant.get("integrations") or {})
    integrations.pop("hubspot", None)
    tenants.update_tenant(tenant["tenant_id"], {"integrations": integrations})
    return {"status": "disconnected"}


# ── Integrations: Google Calendar ────────────────────────────────────────────

@app.get("/api/integrations/google-calendar/status", tags=["Integrations"])
async def gcal_status(request: Request):
    tenant = tenants.require_tenant(request)
    gc = (tenant.get("integrations") or {}).get("google_calendar") or {}
    return {
        "configured_on_server": google_calendar.is_configured(),
        "connected": bool(gc.get("access_token") or gc.get("refresh_token")),
    }


@app.post("/api/integrations/google-calendar/connect", tags=["Integrations"])
async def gcal_connect(request: Request):
    tenant = tenants.require_tenant(request)
    if not google_calendar.is_configured():
        raise HTTPException(400, "Google OAuth is not configured on this server")
    redirect = settings.GOOGLE_REDIRECT_URI or f"{settings.APP_BASE_URL.rstrip('/')}/integrations/google-calendar/callback"
    return {"url": google_calendar.build_authorize_url(tenant["tenant_id"], redirect)}


@app.get("/integrations/google-calendar/callback", tags=["Integrations"], include_in_schema=False)
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


@app.delete("/api/integrations/google-calendar", tags=["Integrations"])
async def gcal_disconnect(request: Request):
    tenant = tenants.require_tenant(request)
    integrations = dict(tenant.get("integrations") or {})
    integrations.pop("google_calendar", None)
    tenants.update_tenant(tenant["tenant_id"], {"integrations": integrations})
    return {"status": "disconnected"}


@app.get("/api/integrations/google-calendar/slots", tags=["Integrations"])
async def gcal_slots(request: Request):
    tenant = tenants.require_tenant(request)
    slots = await google_calendar.suggest_slots(tenant)
    return {"slots": slots}


# ── Ride-along meeting bot ───────────────────────────────────────────────────

@app.get("/api/ride-along", tags=["RideAlong"])
async def list_ride_along_bots(_user: str = Depends(auth.verify_admin)):
    """List active and historical ride-along bots for the current tenant."""
    return {"bots": ride_along_store.list_all()}


@app.post("/api/ride-along/join", tags=["RideAlong"])
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


@app.delete("/api/ride-along/{bot_id}", tags=["RideAlong"])
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


@app.post("/api/ride-along/{bot_id}/speak", tags=["RideAlong"])
async def ride_along_speak(bot_id: str, req: RideAlongSpeakRequest, _user: str = Depends(auth.verify_admin)):
    bot = ride_along_store.get_live(bot_id)
    if bot is None:
        raise HTTPException(404, "Bot not found or already left")
    ok = await bot.speak(req.text)
    return {"bot_id": bot_id, "spoke": ok}


@app.get("/ride-along", tags=["System"])
async def ride_along_page(_user: str = Depends(auth.verify_admin)):
    p = os.path.join(FRONTEND_DIR, "ride-along.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Ride-along page not found")


@app.get("/api/notifications/settings", tags=["Notifications"])
async def get_notification_settings(_user: str = Depends(auth.verify_admin)):
    return notifications.get_settings()


@app.put("/api/notifications/settings", tags=["Notifications"])
async def update_notification_settings(
    payload: dict,
    _user: str = Depends(auth.verify_admin),
):
    return notifications.save_settings(payload or {})


@app.post("/api/notifications/test", tags=["Notifications"])
async def test_notification(payload: dict, _user: str = Depends(auth.verify_admin)):
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


@app.get("/api/sessions/history", tags=["Sessions"])
async def session_history(_user: str = Depends(auth.verify_admin)):
    return {"sessions": session_log_store.list_history()}


@app.get("/api/sessions/history/{session_id}", tags=["Sessions"])
async def session_history_detail(session_id: str, _user: str = Depends(auth.verify_admin)):
    record = session_log_store.get_session(session_id)
    if not record:
        raise HTTPException(404, "Session not found")
    return record


@app.delete("/api/sessions/history/{session_id}", tags=["Sessions"])
async def delete_session_history(session_id: str, _user: str = Depends(auth.verify_admin)):
    if not session_log_store.delete_session(session_id):
        raise HTTPException(404, "Session not found")
    return {"session_id": session_id, "status": "deleted"}


@app.get("/api/leads", tags=["Leads"])
async def list_leads(_user: str = Depends(auth.verify_admin)):
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


@app.post("/api/leads", tags=["Leads"])
async def capture_lead(req: LeadCaptureRequest):
    """Public inbound lead from the homepage. Persists the record so it appears in the
    admin Leads page, then fires the existing notification pipeline (email + webhook +
    HubSpot) so the customer is notified within seconds."""
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


@app.get("/api/meetings", tags=["Meetings"])
async def list_meetings(_user: str = Depends(auth.verify_admin)):
    return {"meetings": meeting_store.list_meetings()}


@app.get("/api/meetings/export", tags=["Meetings"])
async def export_meetings(_user: str = Depends(auth.verify_admin)):
    csv_data = meeting_store.export_csv()
    return PlainTextResponse(
        csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=meeting-requests.csv"},
    )


@app.post("/api/meetings", tags=["Meetings"])
async def create_meeting_request(req: MeetingRequest, request: Request):
    payload = req.model_dump()
    meeting = meeting_store.create_meeting(payload)
    if req.session_id and req.session_id in _sessions:
        _sessions[req.session_id].setdefault("meeting_requests", []).append(meeting)
        if req.visitor_name:
            _sessions[req.session_id]["visitor_name"] = req.visitor_name
        if req.visitor_email:
            _sessions[req.session_id]["visitor_email"] = req.visitor_email
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


@app.patch("/api/meetings/{meeting_id}", tags=["Meetings"])
async def update_meeting_request(
    meeting_id: str,
    req: MeetingStatusRequest,
    _user: str = Depends(auth.verify_admin),
):
    meeting = meeting_store.update_meeting(meeting_id, req.status, req.notes)
    if not meeting:
        raise HTTPException(404, "Meeting request not found")
    return {"meeting": meeting}


@app.post("/api/sessions/{session_id}/events", tags=["Sessions"])
async def append_session_event(session_id: str, req: SessionEventRequest):
    if session_id not in _sessions:
        raise HTTPException(404, "Session not found")
    role = "user" if req.role in ("user", "me") else "assistant"
    _append_transcript(session_id, role, req.text.strip(), req.event_type)
    return {"status": "ok"}


@app.patch("/api/sessions/{session_id}/visitor", tags=["Sessions"])
async def update_session_visitor(session_id: str, req: UpdateVisitorRequest):
    session = _sessions.get(session_id)
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


@app.get("/api/sessions/{session_id}/visual", tags=["Sessions"])
async def session_visual(session_id: str, topic: str = ""):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    card = product_cards.match_product_card(session["persona_id"], topic)
    if card:
        interests = session.setdefault("product_interests", [])
        if card.get("title") and card.get("title") not in interests:
            interests.append(card.get("title"))
    return {"product_card": card}


@app.post("/api/sessions/{session_id}/liveavatar/reconnect", tags=["Sessions"])
async def reconnect_liveavatar_session(session_id: str):
    session = _sessions.get(session_id)
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


@app.post("/api/sessions/{session_id}/message", tags=["Sessions"])
async def session_text_message(session_id: str, req: SessionMessageRequest):
    """Log typed messages; LiveAvatar delivery happens client-side via LiveKit."""
    if session_id not in _sessions:
        raise HTTPException(404, "Session not found")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "Message text required")
    _append_transcript(session_id, "user", text)
    return {"status": "ok", "logged": True}


@app.delete("/api/sessions/{session_id}", tags=["Sessions"])
async def end_session(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(404, "Session not found")
    await _cleanup_session(session_id)
    return {"session_id": session_id, "status": "ended"}


@app.get("/api/sessions", tags=["Sessions"])
async def list_sessions():
    hidden = {"la_session_token", "keepalive_task"}
    return {"sessions": [{"session_id": sid, **{k: v for k, v in d.items() if k not in hidden}}
                         for sid, d in _sessions.items()]}


# ── Voice WebSocket (fallback for voice-only mode) ────────────────────────────

@app.websocket("/ws/voice/{session_id}")
async def voice_websocket(websocket: WebSocket, session_id: str):
    """Fallback voice pipeline when LiveAvatar is not configured."""
    await websocket.accept()
    session = _sessions.get(session_id)
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

@app.get("/api/avatar/list", tags=["Avatar"])
async def avatar_list():
    """List public LiveAvatar stock avatars (no API key required)."""
    avatars = await liveavatar.list_public_avatars(page_size=40)
    return {"avatars": avatars}


# ── Knowledge Base ────────────────────────────────────────────────────────────

@app.post("/api/knowledge/add", tags=["Knowledge"])
async def add_knowledge(req: AddKnowledgeRequest, _user: str = Depends(auth.verify_admin)):
    if req.source_type.value == "url":
        count = await knowledge.add_knowledge_from_url(req.persona_id, req.content, req.title)
    else:
        count = await knowledge.add_knowledge(req.persona_id, req.content, req.title, req.tags)
    if count and not product_cards.has_custom_cards(req.persona_id):
        title = req.title or ("Website Knowledge" if req.source_type.value == "url" else "Product Knowledge")
        product_cards.upsert_product_card(req.persona_id, {
            "id": "knowledge-overview",
            "eyebrow": "Knowledge-based presentation",
            "title": title,
            "subtitle": "Starter card generated from the knowledge you added. Customize it in Admin > Product Cards.",
            "keywords": ["overview", "product", "service", "pricing", "features", "faq"],
            "bullets": ["Answer questions using your uploaded knowledge", "Explain services and features clearly", "Handle common objections and FAQs"],
            "value_points": ["Keeps the conversation grounded in your data", "Helps visitors understand fit faster", "Turns product interest into follow-up intent"],
            "default": True,
        })
    return {"persona_id": req.persona_id, "chunks_stored": count, "status": "indexed"}


@app.post("/api/knowledge/upload", tags=["Knowledge"])
async def upload_knowledge(
    persona_id: str = Form(default="default"),
    title: Optional[str] = Form(default=None),
    file: UploadFile = File(...),
    _user: str = Depends(auth.verify_admin),
):
    content_bytes = await file.read()
    if file.filename and file.filename.lower().endswith(".pdf"):
        import io
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content_bytes))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            raise HTTPException(500, "pypdf not installed")
    else:
        text = content_bytes.decode("utf-8", errors="replace")
    count = await knowledge.add_knowledge(persona_id, text, title=title or file.filename)
    if count and not product_cards.has_custom_cards(persona_id):
        product_cards.upsert_product_card(persona_id, {
            "id": "knowledge-overview",
            "eyebrow": "Knowledge-based presentation",
            "title": title or file.filename or "Uploaded Knowledge",
            "subtitle": "Starter card generated from the uploaded knowledge. Customize it in Admin > Product Cards.",
            "keywords": ["overview", "product", "service", "pricing", "features", "faq"],
            "bullets": ["Answer questions using your uploaded document", "Explain services and features clearly", "Handle common objections and FAQs"],
            "value_points": ["Keeps the conversation grounded in your data", "Helps visitors understand fit faster", "Turns product interest into follow-up intent"],
            "default": True,
        })
    return {"persona_id": persona_id, "filename": file.filename, "chunks_stored": count, "status": "indexed"}


@app.post("/api/knowledge/query", response_model=KnowledgeQueryResponse, tags=["Knowledge"])
async def query_knowledge_endpoint(req: KnowledgeQueryRequest):
    context = await knowledge.query_knowledge(req.persona_id, req.query, req.top_k)
    return KnowledgeQueryResponse(
        results=[KnowledgeQueryResult(text=context, title="Combined Context", score=1.0, tags=[])],
        combined_context=context,
    )


@app.get("/api/knowledge/stats/{persona_id}", tags=["Knowledge"])
async def knowledge_stats(persona_id: str):
    return await knowledge.get_knowledge_stats(persona_id)


@app.delete("/api/knowledge/{persona_id}", tags=["Knowledge"])
async def delete_knowledge(persona_id: str, _user: str = Depends(auth.verify_admin)):
    ok = await knowledge.delete_persona_knowledge(persona_id)
    return {"persona_id": persona_id, "status": "deleted" if ok else "error"}

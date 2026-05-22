"""
SuperHuman AI Persona Platform -- FastAPI v2
Uses LiveAvatar (new HeyGen API) for photorealistic streaming avatar via LiveKit.
"""
import logging
import uuid
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .config import settings
from .models import (
    CreateSessionRequest, AddKnowledgeRequest,
    KnowledgeQueryRequest, KnowledgeQueryResponse, KnowledgeQueryResult,
    HealthResponse, PersonaConfig,
)
from . import liveavatar, agent, knowledge, persona_templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s -- %(message)s")
logger = logging.getLogger(__name__)

_sessions: dict[str, dict] = {}
_default_template = persona_templates.get_template("sales-demo")
_personas: dict[str, PersonaConfig] = {
    "default": PersonaConfig(
        persona_id="default",
        persona_name=settings.DEFAULT_PERSONA_NAME,
        company_name="our company",
        system_prompt_override=_default_template.system_prompt if _default_template else None,
    ),
    **persona_templates.get_persona_configs(),
}


def _role_hint_for_persona(persona_id: str) -> str:
    hints = {
        "hr-interviewer": "hr",
        "onboarding-guide": "onboarding",
        "support-agent": "support",
        "human-chatbot": "support",
        "demo-host": "demo",
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    logger.info("LiveAvatar API key set: %s", bool(settings.LIVEAVATAR_API_KEY))
    logger.info("Sandbox mode: %s", settings.LIVEAVATAR_USE_SANDBOX)
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
    await agent.stop_voice_pipeline(session_id)
    if session.get("la_session_id") and session.get("la_session_token"):
        await liveavatar.stop_session(session["la_session_id"], session["la_session_token"])
    if session.get("la_context_id"):
        await liveavatar.delete_context(session["la_context_id"])
    logger.info("Session %s cleaned up", session_id)


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
async def admin():
    p = os.path.join(FRONTEND_DIR, "admin.html")
    if os.path.isfile(p):
        return FileResponse(p)
    raise HTTPException(404, "Admin panel not found")


# ── Personas ──────────────────────────────────────────────────────────────────

@app.get("/api/personas", tags=["Personas"])
async def list_personas():
    return {"personas": [p.model_dump() for p in _personas.values()]}


@app.post("/api/personas", tags=["Personas"])
async def create_persona(config: PersonaConfig):
    _personas[config.persona_id] = config
    return {"persona_id": config.persona_id, "status": "created"}


@app.get("/api/personas/{persona_id}", tags=["Personas"])
async def get_persona(persona_id: str):
    p = _personas.get(persona_id)
    if not p:
        raise HTTPException(404, f"Persona '{persona_id}' not found")
    return p.model_dump()


@app.put("/api/personas/{persona_id}", tags=["Personas"])
async def update_persona(persona_id: str, config: PersonaConfig):
    config.persona_id = persona_id
    _personas[persona_id] = config
    return {"persona_id": persona_id, "status": "updated"}


@app.delete("/api/personas/{persona_id}", tags=["Personas"])
async def delete_persona(persona_id: str):
    protected = {"default", "hr-interviewer", "onboarding-guide", "support-agent",
                 "human-chatbot", "demo-host", "meeting-assistant"}
    if persona_id in protected:
        raise HTTPException(400, f"Cannot delete built-in persona '{persona_id}'")
    _personas.pop(persona_id, None)
    await knowledge.delete_persona_knowledge(persona_id)
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
async def create_persona_from_template(slug: str, company_name: Optional[str] = None):
    """Clone a solution template into a new custom persona (optional company name)."""
    t = persona_templates.get_template(slug)
    if not t:
        raise HTTPException(404, f"Template '{slug}' not found")
    new_id = f"{t.persona_id}-custom-{uuid.uuid4().hex[:6]}"
    cfg = t.to_persona_config()
    cfg.persona_id = new_id
    if company_name:
        cfg.company_name = company_name
    _personas[new_id] = cfg
    return {"persona_id": new_id, "status": "created", "template": slug}


# ── Sessions (LiveAvatar) ─────────────────────────────────────────────────────

@app.post("/api/sessions", tags=["Sessions"])
async def create_session(req: CreateSessionRequest):
    """
    Full session creation flow:
    1. Generate opening pitch from knowledge base
    2. Create LiveAvatar context (system prompt + opening text)
    3. Create session token
    4. Start session -> get LiveKit credentials
    5. Return LiveKit URL + token to frontend
    """
    persona    = _personas.get(req.persona_id, _personas["default"])
    session_id = str(uuid.uuid4())

    persona_name = persona.persona_name
    company_name = persona.company_name

    # Build system prompt with knowledge
    kb_query = _knowledge_query_for_persona(req.persona_id)
    knowledge_ctx = await knowledge.query_knowledge(req.persona_id, kb_query)
    system_prompt = agent.build_system_prompt(
        persona_name, company_name, knowledge_ctx, persona.tone.value,
        prompt_override=persona.system_prompt_override,
    )

    # Generate opening pitch (auto-demo)
    role_hint = _role_hint_for_persona(req.persona_id)
    fallback = _opening_fallback_for_persona(req.persona_id)
    opening_text = fallback or f"Hi! I'm {persona_name} from {company_name}. How can I help?"
    if knowledge_ctx and len(knowledge_ctx.strip()) > 100 and settings.OPENAI_API_KEY:
        opening_text = await agent.generate_opening_pitch(
            persona_name, company_name, knowledge_ctx, req.visitor_name,
            role_hint=role_hint, opening_fallback=fallback or None,
        )
    elif req.visitor_name and fallback:
        opening_text = fallback.replace("Hi,", f"Hi {req.visitor_name},")

    la_context_id = None
    la_session_token = ""
    la_session_id = ""
    livekit_url = ""
    livekit_client_token = ""

    if settings.LIVEAVATAR_API_KEY:
        # Create LiveAvatar context
        la_context_id = await liveavatar.create_context(
            prompt=system_prompt,
            opening_text=opening_text,
            display_name=f"{persona_name} @ {company_name}",
        )

        # Create session token
        token_data = await liveavatar.create_session_token(
            avatar_id=persona.avatar_id or settings.LIVEAVATAR_AVATAR_ID or None,
            context_id=la_context_id,
            voice_id=persona.voice_id or settings.LIVEAVATAR_VOICE_ID or None,
            is_sandbox=settings.LIVEAVATAR_USE_SANDBOX,
        )

        if token_data:
            la_session_token = token_data["session_token"]

            # Start session to get LiveKit credentials
            start_data = await liveavatar.start_session(la_session_token)
            if start_data:
                la_session_id        = start_data["session_id"]
                livekit_url          = start_data["livekit_url"]
                livekit_client_token = start_data["livekit_client_token"]

    _sessions[session_id] = {
        "persona_id":         req.persona_id,
        "persona_name":       persona_name,
        "company_name":       company_name,
        "tone":               persona.tone.value,
        "visitor_name":       req.visitor_name,
        "visitor_email":      req.visitor_email,
        "la_session_id":      la_session_id,
        "la_session_token":   la_session_token,
        "la_context_id":      la_context_id,
        "opening_text":       opening_text,
    }

    return {
        "session_id":           session_id,
        "persona_id":           req.persona_id,
        "persona_name":         persona_name,
        "company_name":         company_name,
        "livekit_url":          livekit_url,
        "livekit_client_token": livekit_client_token,
        "la_session_id":        la_session_id,
        "opening_text":         opening_text,
        "mode":                 "liveavatar" if livekit_url else "voice_only",
    }


@app.delete("/api/sessions/{session_id}", tags=["Sessions"])
async def end_session(session_id: str):
    if session_id not in _sessions:
        raise HTTPException(404, "Session not found")
    await _cleanup_session(session_id)
    return {"session_id": session_id, "status": "ended"}


@app.get("/api/sessions", tags=["Sessions"])
async def list_sessions():
    return {"sessions": [{"session_id": sid, **{k: v for k, v in d.items() if k != "la_session_token"}}
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
    persona = _personas.get(session["persona_id"], _personas["default"])
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
    )


# ── Avatars ───────────────────────────────────────────────────────────────────

@app.get("/api/avatar/list", tags=["Avatar"])
async def avatar_list():
    """List public LiveAvatar stock avatars (no API key required)."""
    avatars = await liveavatar.list_public_avatars(page_size=40)
    return {"avatars": avatars}


# ── Knowledge Base ────────────────────────────────────────────────────────────

@app.post("/api/knowledge/add", tags=["Knowledge"])
async def add_knowledge(req: AddKnowledgeRequest):
    if req.source_type.value == "url":
        count = await knowledge.add_knowledge_from_url(req.persona_id, req.content, req.title)
    else:
        count = await knowledge.add_knowledge(req.persona_id, req.content, req.title, req.tags)
    return {"persona_id": req.persona_id, "chunks_stored": count, "status": "indexed"}


@app.post("/api/knowledge/upload", tags=["Knowledge"])
async def upload_knowledge(
    persona_id: str = Form(default="default"),
    title: Optional[str] = Form(default=None),
    file: UploadFile = File(...),
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
async def delete_knowledge(persona_id: str):
    ok = await knowledge.delete_persona_knowledge(persona_id)
    return {"persona_id": persona_id, "status": "deleted" if ok else "error"}

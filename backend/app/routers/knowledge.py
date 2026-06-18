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


@router.post("/api/uploads/product-slides", tags=["Uploads"])
async def upload_product_slide(
    file: UploadFile = File(...),
    _scope: str = Depends(require_authed),
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
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    filename = f"product-slide-{uuid.uuid4().hex}{ext}"
    path = os.path.join(UPLOADS_DIR, filename)
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "Slide image must be under 10MB")
    with open(path, "wb") as out:
        out.write(content)
    return {"url": f"/uploads/{filename}", "filename": filename}


# ── Sessions (LiveAvatar) ─────────────────────────────────────────────────────



@router.post("/api/studio/train", tags=["Knowledge"])
async def studio_train(req: StudioTrainRequest):
    """Public homepage studio training — indexes URL + product text for a persona."""
    persona_id = (req.persona_id or "default").strip()
    company = (req.company or "your company").strip()
    total_chunks = 0
    lang_code = languages.normalize_language(req.lang)

    url_warning: Optional[str] = None
    if req.url and req.url.strip():
        try:
            total_chunks += await knowledge.add_knowledge_from_url(
                persona_id, req.url.strip(), title=f"{company} Website",
            )
        except Exception as e:
            logger.warning("Studio URL crawl failed: %s", e)
            url_warning = f"Could not read website ({e}). Trained on your product description instead."

    parts = [f"Company name: {company}"]
    if req.product and req.product.strip():
        parts.append(f"Products and services: {req.product.strip()}")
    parts.append(f"Preferred language: {languages.language_name(lang_code)}")
    total_chunks += await knowledge.add_knowledge(
        persona_id, "\n".join(parts), title=f"{company} Overview", tags=["studio"],
    )

    if total_chunks:
        product_cards.seed_from_studio_training(persona_id, company)
        personas = _get_personas()
        persona_name = "Aiza"
        if persona_id in personas:
            cfg = personas[persona_id]
            persona_name = cfg.persona_name
            if company and cfg.company_name in ("our company", "your company", "Savant.ai"):
                updated = cfg.model_copy(update={"company_name": company})
                personas[persona_id] = updated
                persona_store.save_all(personas)
                _invalidate_tenant_persona_cache()
                company_name = company
            else:
                company_name = cfg.company_name
        else:
            company_name = company
    else:
        company_name = company
        persona_name = "Aiza"

    knowledge_ctx = await knowledge.query_knowledge(
        persona_id,
        f"{company} {req.product or ''} overview products services".strip(),
    )
    product_line = (req.product or "").strip()
    pitch_fallback = (
        f"Hi — I'm {persona_name}, your {company} Superhuman. "
        f"{product_line + '. ' if product_line else ''}"
        "I've just learned about your business — ask me anything or tell me what you'd like to explore."
    )
    opening_text = await agent.generate_opening_pitch(
        persona_name, company_name if total_chunks else company, knowledge_ctx,
        role_hint="demo",
        opening_fallback=pitch_fallback,
        language=lang_code,
    )

    return {
        "status": "indexed",
        "persona_id": persona_id,
        "chunks_stored": total_chunks,
        "company_name": company,
        "opening_text": opening_text,
        "url_warning": url_warning,
    }


@router.post("/api/knowledge/add", tags=["Knowledge"])
async def add_knowledge(req: AddKnowledgeRequest, _scope: str = Depends(require_authed)):
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


@router.post("/api/knowledge/upload", tags=["Knowledge"])
async def upload_knowledge(
    persona_id: str = Form(default="default"),
    title: Optional[str] = Form(default=None),
    file: UploadFile = File(...),
    _scope: str = Depends(require_authed),
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


@router.post("/api/knowledge/query", response_model=KnowledgeQueryResponse, tags=["Knowledge"])
async def query_knowledge_endpoint(req: KnowledgeQueryRequest, _scope: str = Depends(require_authed)):
    # Dashboard "test retrieval" tool. Auth-gated; retrieval is tenant-scoped via
    # the active tenant bound by the middleware, so it only ever reads this
    # tenant's chunks.
    context = await knowledge.query_knowledge(req.persona_id, req.query, req.top_k)
    return KnowledgeQueryResponse(
        results=[KnowledgeQueryResult(text=context, title="Combined Context", score=1.0, tags=[])],
        combined_context=context,
    )


@router.get("/api/knowledge/stats/{persona_id}", tags=["Knowledge"])
async def knowledge_stats(persona_id: str):
    return await knowledge.get_knowledge_stats(persona_id)


@router.delete("/api/knowledge/{persona_id}", tags=["Knowledge"])
async def delete_knowledge(persona_id: str, _scope: str = Depends(require_authed)):
    ok = await knowledge.delete_persona_knowledge(persona_id)
    return {"persona_id": persona_id, "status": "deleted" if ok else "error"}

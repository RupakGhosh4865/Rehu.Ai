"""
Savant.ai -- Shared runtime core (Prompt 1.4)

Extracted from main.py so the route handlers can live in app/routers/* without a
circular import (routers import `core`; `core` never imports a router or main).

Holds ONLY shared module-level state + helpers — the live-session store, the
process-local runtime (keepalive Task / conv_state), persona state, and the
small glue helpers the routes call. No FastAPI app, no routes, no middleware:
those stay in main.py (the app factory).
"""
import asyncio
import json
import logging
import os
import time
from contextlib import suppress
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request

from .config import settings
from .models import PersonaConfig
from . import (
    liveavatar, agent, knowledge, persona_templates, persona_experience,
    persona_store, auth, session_log_store, notifications, tenants, metering,
    audit, security, db, monitoring, intelligence, session_store,
)

logger = logging.getLogger(__name__)

# ── Durable session state (Redis prod / in-memory dev). See session_store.py. ──
store = session_store.build_store()

# Process-local runtime that CANNOT be serialized into the durable store: the
# asyncio keepalive Task and the orchestrator ConversationState. Keyed by
# session_id. The worker that created an avatar session owns its keepalive Task.
_local_runtime: dict[str, dict] = {}


def _runtime(session_id: str) -> dict:
    return _local_runtime.setdefault(session_id, {})


_avatar_cache: list = []
_default_template = persona_templates.get_template("sales-demo")
KEEPALIVE_INTERVAL_SECONDS = 25
_MAX_SYSTEM_PROMPT_CHARS = 12000


def _tenant_plan(request: Request) -> str:
    """Current tenant's plan id (defaults to trial / when unauthenticated)."""
    t = tenants.get_current_tenant_optional(request)
    return (t or {}).get("plan", "trial")

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")

# Insecure factory defaults that must never run in production.
_INSECURE_DEFAULTS = {
    "JWT_SECRET":  "change-me-jwt-secret",
    "SECRET_KEY":  "change-me-in-production",
}


def _enforce_security_config() -> None:
    """Boot-time security gate. Delegates to the single source of truth
    (security.check_secrets), which logs every problem and — when
    ENVIRONMENT=production — refuses to boot.

    For non-production: default/empty secrets are tolerated UNLESS DEBUG is off,
    in which case we still refuse to boot on the most dangerous defaults (forgeable
    JWTs, unauthenticated admin) so a misconfigured staging box doesn't ship open.
    """
    problems = security.check_secrets()  # raises in production; logs everywhere
    if not problems or security.is_production():
        return
    fatal: list[str] = []
    if not settings.ADMIN_PASSWORD:
        fatal.append("ADMIN_PASSWORD is empty — admin panel and write APIs unauthenticated.")
    for name, insecure in _INSECURE_DEFAULTS.items():
        if getattr(settings, name, "") == insecure:
            fatal.append(f"{name} is still the shipped default.")
    if fatal and not settings.DEBUG:
        raise RuntimeError(
            "Refusing to start with insecure configuration:\n  - "
            + "\n  - ".join(fatal)
            + "\n\nFix these in your .env, or set DEBUG=true for local development only."
        )


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
        try:
            for pid in persona_store.load_deleted():
                cached.pop(pid, None)
        except Exception:
            pass
        _tenant_personas_cache[tid] = cached
    return cached


def _invalidate_tenant_persona_cache() -> None:
    tid = tenants.active_tenant_id()
    _tenant_personas_cache.pop(tid, None)


def _save_personas() -> None:
    persona_store.save_all(_personas)


def reload_personas() -> None:
    """Rebuild the default-tenant persona set from disk (called at startup).
    Mutates the module global in place so importers see the refresh."""
    global _personas
    _personas = persona_store.merge_into(_seed_personas(), persona_store.load_all())


def set_avatar_cache(avatars: list) -> None:
    global _avatar_cache
    _avatar_cache = avatars


def get_avatar_cache() -> list:
    return _avatar_cache


def _format_user_context(req) -> str:
    """Render an in-product user context line for the system prompt."""
    parts: list[str] = []
    if getattr(req, "user_id", None):
        parts.append("Logged-in user")
    if getattr(req, "page_context", None):
        pc = req.page_context.strip()
        if len(pc) > 2000:
            pc = pc[:2000] + "…"
        location = "on " + pc
        if parts:
            parts[0] = f"{parts[0]} {location}"
        else:
            parts.append(f"Visitor {location}")
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


def _persona_tone(persona: PersonaConfig) -> str:
    tone = getattr(persona, "tone", None)
    if tone is None:
        return "professional"
    return tone.value if hasattr(tone, "value") else str(tone)


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


_last_credit_alert_at = 0.0


def _alert_avatar_credits_exhausted(raw_error: str) -> None:
    """LiveAvatar refused a session for credits: page the founder, loudly but at
    most once per hour."""
    global _last_credit_alert_at
    logger.critical("AVATAR CREDITS EXHAUSTED — sessions are being refused: %s", raw_error)
    try:
        audit.record("avatar.credits_exhausted", actor="system", tenant=tenants.active_tenant_id())
    except Exception:
        pass
    now = time.time()
    if now - _last_credit_alert_at < 3600:
        return
    _last_credit_alert_at = now
    try:
        asyncio.create_task(notifications.send_email(
            settings.ALERT_EMAIL,
            "[Savant ALERT] LiveAvatar credits exhausted — Aiza is refusing calls",
            ("LiveAvatar just refused to start an avatar session:\n\n"
             f"  {raw_error}\n\n"
             "Every visitor call will fail until credits are topped up at "
             "app.liveavatar.com. Visitors currently see: 'Aiza is temporarily "
             "unavailable.'\n\nThis alert fires at most once per hour."),
        ))
    except Exception:
        logger.exception("Could not send credit-exhaustion alert email")


def _voice_for_locale(locale: str) -> Optional[str]:
    """Accent voice for a locale variant (en-GB -> imported British voice)."""
    try:
        mapping = json.loads(settings.LIVEAVATAR_VOICE_BY_LOCALE or "{}")
        return (mapping.get(locale) or "").strip() or None
    except Exception:
        logger.warning("LIVEAVATAR_VOICE_BY_LOCALE is not valid JSON; ignoring")
        return None


def _voice_candidate(name: Optional[str]) -> Optional[str]:
    """Named audition voice (?voice=lauren). Allowlist only."""
    if not name:
        return None
    try:
        mapping = json.loads(settings.LIVEAVATAR_VOICE_CANDIDATES or "{}")
        return (mapping.get(name.strip().lower()) or "").strip() or None
    except Exception:
        logger.warning("LIVEAVATAR_VOICE_CANDIDATES is not valid JSON; ignoring")
        return None


def _stream_avatar_and_voice(
    persona_id: str, persona: PersonaConfig, locale: str = "en",
    voice_candidate: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Sandbox always uses Wayne + default voice so video and speech both work."""
    if settings.LIVEAVATAR_USE_SANDBOX:
        return liveavatar.SANDBOX_AVATAR_ID, None
    aid = settings.LIVEAVATAR_AVATAR_ID or persona.avatar_id or persona_experience.resolve_avatar_id(persona_id)
    vid = (_voice_candidate(voice_candidate)
           or _voice_for_locale(locale)
           or settings.LIVEAVATAR_VOICE_ID or persona.voice_id
           or persona_experience.resolve_voice_id(persona_id))
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


def _set_session_cookie(response, token: str) -> None:
    """Set the auth token as an HttpOnly cookie (Secure in production) so it can't
    be read or stolen by client-side scripts (XSS)."""
    response.set_cookie(
        key="savant_token", value=token,
        max_age=settings.JWT_TTL_HOURS * 3600, path="/",
        httponly=True, secure=settings.FORCE_HTTPS, samesite="lax",
    )


def _plan_price_usd(plan: str) -> Optional[int]:
    return {
        "trial": 0,
        "pilot": 997,
        "professional": 1999,
        "business": 3499,
        "enterprise": None,
    }.get(plan)


def require_authed(request: Request) -> str:
    """Auth for dashboard data endpoints (sessions / leads / meetings)."""
    if getattr(request.state, "tenant", None):
        return request.state.tenant_id
    if auth.is_admin_request(request):
        return tenants.DEFAULT_TENANT_ID
    raise HTTPException(status_code=401, detail="Sign in to view this data.")


# ── Session lifecycle ─────────────────────────────────────────────────────────

async def _prune_idle_sessions(keep: int = 6) -> None:
    """Drop oldest sessions so LiveAvatar sandbox limits are not exhausted."""
    active = store.list_active()
    if len(active) <= keep:
        return
    ordered = sorted(
        active,
        key=lambda item: (
            0 if item[1].get("is_preview") else 1,
            item[1].get("started_at") or "",
        ),
    )
    for sid, _ in ordered[: len(active) - keep]:
        logger.info("Pruning idle session %s (pool size %d)", sid[:8], len(active))
        await _cleanup_session(sid)


async def _post_session_pipeline(record: dict):
    """Background work after a session is persisted: intelligence, then notifications."""
    try:
        enriched = await intelligence.enrich(record)
        if enriched.get("intelligence") and enriched is not record:
            session_log_store.finalize_session(enriched)
        record = enriched
    except Exception:
        logger.exception("Post-session intelligence failed; notifying without it")
    if record.get("visitor_email") or (record.get("lead_summary") or {}).get("lead_score"):
        await notifications.notify_lead_captured(record)
    await notifications.notify_session_ended(record)
    try:
        await notifications.send_visitor_recap(record)
    except Exception:
        logger.exception("Visitor recap email failed")


async def _cleanup_session(session_id: str):
    session = store.remove(session_id)
    runtime = _local_runtime.pop(session_id, {})
    if not session:
        return
    keepalive_task = runtime.get("keepalive_task")
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
        # Bill exactly once per session, whether it ends cleanly here or via the
        # crash-recovery sweep. mark_metered is atomic (Redis SET NX).
        if (tenant_id and tenant_id != tenants.DEFAULT_TENANT_ID
                and store.mark_metered(session_id)):
            try:
                started = session.get("started_at")
                ended = session.get("ended_at")
                from datetime import datetime as _dt
                if started and ended:
                    sec = (_dt.fromisoformat(ended.replace("Z", "+00:00")) -
                           _dt.fromisoformat(started.replace("Z", "+00:00"))).total_seconds()
                    metering.record_minutes(
                        tenant_id, max(0.0, sec),
                        used_avatar=bool(session.get("used_avatar", True)),
                    )
            except Exception:
                logger.exception("Could not record minutes for tenant %s", tenant_id)
        asyncio.create_task(_post_session_pipeline(record))
    await agent.stop_voice_pipeline(session_id)
    if session.get("la_session_id") and session.get("la_session_token"):
        await liveavatar.stop_session(session["la_session_id"], session["la_session_token"])
    if session.get("la_context_id"):
        await liveavatar.delete_context(session["la_context_id"])
    logger.info("Session %s cleaned up", session_id)


async def _liveavatar_keepalive_loop(session_id: str):
    """Keep the avatar session warm; idle-kill + pool-kill enforce the cost cap."""
    idle_limit = metering.idle_timeout_seconds()
    try:
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL_SECONDS)
            session = store.get(session_id)
            if not session:
                return
            la_session_id = session.get("la_session_id")
            if not la_session_id:
                return
            idle_for = time.time() - float(session.get("last_activity_at") or time.time())
            if idle_for >= idle_limit:
                logger.info("Idle-kill: ending avatar session %s after %.0fs of silence",
                            session_id[:8], idle_for)
                asyncio.create_task(_cleanup_session(session_id))
                return
            tenant_id = session.get("tenant_id")
            if tenant_id and tenant_id != tenants.DEFAULT_TENANT_ID:
                try:
                    t = tenants.get_tenant(tenant_id)
                    remaining = metering.avatar_remaining(t) if t else None
                    if remaining is not None:
                        started = session.get("started_at")
                        elapsed_min = 0.0
                        if started:
                            elapsed_min = max(0.0, (datetime.now(timezone.utc) -
                                              datetime.fromisoformat(started.replace("Z", "+00:00"))
                                              ).total_seconds() / 60.0)
                        if elapsed_min >= remaining:
                            logger.info("Pool-kill: tenant %s exhausted mid-session %s (%.1f min)",
                                        tenant_id[:8], session_id[:8], elapsed_min)
                            asyncio.create_task(_cleanup_session(session_id))
                            return
                except Exception:
                    logger.exception("Pool-kill check failed for %s", session_id[:8])
            ok = await liveavatar.keep_session_alive(la_session_id)
            store.update(session_id,
                         last_keepalive_at=datetime.now(timezone.utc).isoformat(),
                         last_keepalive_ok=ok)
            if not ok:
                logger.warning("LiveAvatar keep-alive failed for app session %s", session_id)
    except asyncio.CancelledError:
        raise


async def _orphan_sweep_loop():
    """Single leader-elected sweeper; reclaims crashed-worker sessions. No-op for
    the in-memory store (single worker)."""
    if not getattr(store, "is_redis", lambda: False)():
        return
    interval = max(5, int(settings.SESSION_SWEEP_INTERVAL_SECONDS))
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                if not store.try_acquire_sweep_lock(interval):
                    continue
                for sid in store.orphans():
                    await _recover_orphan_session(sid)
            except Exception:
                logger.exception("Orphan sweep iteration failed")
    except asyncio.CancelledError:
        raise


async def _recover_orphan_session(session_id: str):
    """Best-effort recovery of a crashed-worker session: bill once (idempotent)
    and release the paid stream."""
    if store.mark_metered(session_id):
        logger.warning("Orphan recovery: session %s — owning worker likely crashed", session_id[:8])
    store.forget_orphan(session_id)


def _owned_session(session_id: str) -> Optional[dict]:
    """Return the session only if it belongs to the active tenant (else None)."""
    return store.owned_by_tenant(session_id, tenants.active_tenant_id())


def _append_transcript(session_id: str, role: str, text: str, event_type: str = "transcript") -> bool:
    session = store.get(session_id)
    if not session or not text:
        return False
    session.setdefault("transcript", []).append({
        "role": role,
        "text": text,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    session["last_activity_at"] = time.time()
    store.update(session_id, transcript=session["transcript"],
                 last_activity_at=session["last_activity_at"])
    store.touch(session_id)
    return True

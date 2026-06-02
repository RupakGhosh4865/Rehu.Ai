"""
Savant.ai -- LiveAvatar Client
New HeyGen API: https://api.liveavatar.com  (replaces deprecated api.heygen.com)

Flow:
  1. Backend: POST /v1/sessions/token  (X-API-KEY auth) -> session_token
  2. Backend: POST /v1/sessions/start  (Bearer session_token) -> livekit_url + livekit_client_token
  3. Frontend: connect to LiveKit room with those credentials
  4. Avatar video/audio streams via LiveKit tracks
  5. Events via LiveKit data channel topics: agent-control / agent-response

Sandbox mode:
  - is_sandbox=True, avatar_id=SANDBOX_AVATAR_ID (Wayne)
  - Free, no credits, ~1 min sessions
  - Perfect for development/testing
"""
import asyncio
import logging
import uuid
from typing import Optional
import httpx

from .config import settings

logger = logging.getLogger(__name__)

_last_api_error: str = ""

LA_BASE          = "https://api.liveavatar.com"
SANDBOX_AVATAR_ID = "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a"   # Wayne - free sandbox avatar


def last_api_error() -> str:
    """Human-readable detail from the most recent LiveAvatar API failure."""
    return _last_api_error


def _set_api_error(message: str) -> None:
    global _last_api_error
    _last_api_error = (message or "").strip()


def sandbox_preview_url(avatars: list) -> str:
    """Preview image for Wayne — matches sandbox live stream."""
    for a in avatars:
        if a.get("id") == SANDBOX_AVATAR_ID:
            return a.get("preview_url") or ""
    return ""


def _headers() -> dict:
    return {
        "X-API-KEY": settings.LIVEAVATAR_API_KEY,
        "Content-Type": "application/json",
    }


def _bearer(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ── Context (persona personality + knowledge) ─────────────────────────────────

async def create_context(
    prompt: str,
    opening_text: str = "",
    display_name: str = "Aiza Context",
) -> Optional[str]:
    """
    Create a LiveAvatar context (system prompt + opening greeting).
    Returns context_id, or None if creation fails.
    """
    if not settings.LIVEAVATAR_API_KEY:
        return None

    # Names must be globally unique in LiveAvatar — always suffix with a random id.
    unique_name = f"{(display_name or 'Aiza Context')[:72]} · {uuid.uuid4().hex[:10]}"
    payload = {
        "name": unique_name,
        "prompt": prompt,
    }
    if opening_text:
        payload["opening_text"] = opening_text

    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{LA_BASE}/v1/contexts", headers=_headers(), json=payload)
            data = r.json()

        if r.status_code in (200, 201) and data.get("code") == 1000:
            ctx_id = data.get("data", {}).get("id")
            logger.info("LiveAvatar context created: %s", ctx_id)
            return ctx_id
        msg = data.get("message") or f"HTTP {r.status_code}"
        _set_api_error(f"Context creation failed: {msg}")
        logger.error("Context creation failed %s: %s", r.status_code, data)
        return None
    except Exception as e:
        _set_api_error(f"Context creation error: {e}")
        logger.warning("LiveAvatar context error: %s", e)
        return None


async def delete_context(context_id: str) -> bool:
    """Clean up a context after session ends."""
    if not context_id or not settings.LIVEAVATAR_API_KEY:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.delete(f"{LA_BASE}/v1/contexts/{context_id}", headers=_headers())
            return r.status_code in (200, 204)
    except Exception:
        return False


# ── Session lifecycle ──────────────────────────────────────────────────────────

async def create_session_token(
    avatar_id: Optional[str] = None,
    context_id: Optional[str] = None,
    voice_id: Optional[str] = None,
    language: str = "en",
    is_sandbox: bool = False,
) -> dict:
    """
    Create a short-lived session token on the backend.
    Returns: {session_token, session_id, avatar_id} or empty dict on failure.

    Sandbox mode only supports the Wayne avatar for video — if a persona avatar
    is rejected, we automatically retry with Wayne so video still streams.
    """
    if not settings.LIVEAVATAR_API_KEY:
        logger.warning("LIVEAVATAR_API_KEY not set")
        return {}

    use_sandbox = is_sandbox or settings.LIVEAVATAR_USE_SANDBOX

    # Sandbox: Wayne only — persona avatars are rejected and custom voices can break speech
    if use_sandbox:
        candidates = [SANDBOX_AVATAR_ID]
        voice_id = None
    else:
        candidates = []
        for aid in (avatar_id, settings.LIVEAVATAR_AVATAR_ID):
            if aid and aid not in candidates:
                candidates.append(aid)
        if SANDBOX_AVATAR_ID not in candidates:
            candidates.append(SANDBOX_AVATAR_ID)

    avatar_persona: dict = {"language": language}
    if voice_id:
        avatar_persona["voice_id"] = voice_id
    if context_id:
        avatar_persona["context_id"] = context_id

    last_error = None
    for attempt in range(3):
        for try_avatar in candidates:
            payload = {
                "mode":           "FULL",
                "avatar_id":      try_avatar,
                "is_sandbox":     use_sandbox,
                "avatar_persona": avatar_persona,
                "video_settings": {"quality": "high", "encoding": "H264"},
            }
            try:
                async with httpx.AsyncClient(timeout=25) as c:
                    r = await c.post(f"{LA_BASE}/v1/sessions/token", headers=_headers(), json=payload)
                    data = r.json()

                if r.status_code == 200 and data.get("code") == 1000:
                    session_data = data["data"]
                    if try_avatar != (avatar_id or try_avatar):
                        logger.info(
                            "Sandbox fallback: using avatar %s (requested %s)",
                            try_avatar[:8], (avatar_id or "")[:8],
                        )
                    logger.info("LiveAvatar session token created (sandbox=%s avatar=%s)", use_sandbox, try_avatar[:8])
                    return {
                        "session_token": session_data["session_token"],
                        "session_id":    session_data.get("session_id", ""),
                        "avatar_id":     try_avatar,
                    }

                last_error = data
                msg = str(data.get("message", "")) + str(data.get("data", ""))
                if use_sandbox and try_avatar != SANDBOX_AVATAR_ID and "sandbox" in msg.lower():
                    logger.warning("Avatar %s not in sandbox — trying Wayne", try_avatar[:8])
                    continue
                _set_api_error(data.get("message") or f"Session token failed (HTTP {r.status_code})")
                logger.error("Session token failed %s: %s", r.status_code, data)
            except Exception as e:
                _set_api_error(f"Session token error: {e}")
                logger.error("LiveAvatar create_session_token error: %s", e)

        if attempt < 2:
            await asyncio.sleep(0.6 * (attempt + 1))

    if last_error:
        logger.error("All avatar candidates failed: %s", last_error)
    return {}


async def start_session(session_token: str) -> dict:
    """
    Start the session and get LiveKit room credentials.
    Returns: {session_id, livekit_url, livekit_client_token} or empty dict.
    """
    if not session_token:
        return {}

    for attempt in range(4):
        try:
            async with httpx.AsyncClient(timeout=35) as c:
                r = await c.post(
                    f"{LA_BASE}/v1/sessions/start",
                    headers=_bearer(session_token),
                )
                data = r.json()

            if r.status_code in (200, 201) and data.get("code") == 1000:
                sd = data["data"]
                logger.info("LiveAvatar session started: %s", sd.get("session_id"))
                return {
                    "session_id":           sd["session_id"],
                    "livekit_url":          sd["livekit_url"],
                    "livekit_client_token": sd["livekit_client_token"],
                }

            msg = data.get("message") or f"HTTP {r.status_code}"
            _set_api_error(f"Session start failed: {msg}")
            logger.error("Session start failed %s: %s", r.status_code, data)
        except Exception as e:
            _set_api_error(f"Session start error: {e}")
            logger.error("LiveAvatar start_session error: %s", e)

        if attempt < 3:
            await asyncio.sleep(0.75 * (attempt + 1))

    return {}


async def provision_stream(
    *,
    avatar_id: Optional[str] = None,
    context_id: Optional[str] = None,
    voice_id: Optional[str] = None,
    language: str = "en",
    is_sandbox: Optional[bool] = None,
) -> dict:
    """
    Create session token + start stream (with retries).
    Returns livekit_url, livekit_client_token, la_session_id, la_session_token, stream_avatar_id.
    """
    token_data = await create_session_token(
        avatar_id=avatar_id,
        context_id=context_id,
        voice_id=voice_id,
        language=language,
        is_sandbox=is_sandbox if is_sandbox is not None else settings.LIVEAVATAR_USE_SANDBOX,
    )
    if not token_data:
        return {}

    start_data = await start_session(token_data["session_token"])
    if not start_data:
        return {}

    return {
        "session_token":        token_data["session_token"],
        "stream_avatar_id":     token_data.get("avatar_id"),
        "la_session_id":        start_data["session_id"],
        "livekit_url":          start_data["livekit_url"],
        "livekit_client_token": start_data["livekit_client_token"],
    }


async def stop_session(session_id: str, session_token: str = "") -> bool:
    """End a LiveAvatar session."""
    if not session_id:
        return True
    try:
        headers = _bearer(session_token) if session_token else _headers()
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.delete(f"{LA_BASE}/v1/sessions/{session_id}", headers=headers)
        logger.info("LiveAvatar session stopped: %s", session_id)
        return r.status_code in (200, 204)
    except Exception as e:
        logger.warning("LiveAvatar stop_session error: %s", e)
        return False


async def keep_session_alive(session_id: str) -> bool:
    """Reset LiveAvatar idle timeout for an active session."""
    if not session_id or not settings.LIVEAVATAR_API_KEY:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{LA_BASE}/v1/sessions/keep-alive",
                headers=_headers(),
                json={"session_id": session_id},
            )
            data = r.json() if r.content else {}
        ok = r.status_code == 200 and data.get("code", 100) in (100, 1000)
        if ok:
            logger.debug("LiveAvatar keep-alive sent: %s", session_id)
        else:
            logger.warning("LiveAvatar keep-alive failed %s: %s", r.status_code, data)
        return ok
    except Exception as e:
        logger.warning("LiveAvatar keep-alive error: %s", e)
        return False


# ── Avatars ────────────────────────────────────────────────────────────────────

async def list_public_avatars(page_size: int = 20) -> list:
    """
    List public stock avatars. No API key required.
    Each avatar has: id, name, preview_url, default_voice
    """
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{LA_BASE}/v1/avatars/public",
                params={"page_size": page_size},
                headers={"Content-Type": "application/json"},
            )
            data = r.json()
        if r.status_code == 200:
            return data.get("data", {}).get("results", [])
    except Exception as e:
        logger.warning("LiveAvatar list_public_avatars error: %s", e)
    return []


async def list_user_avatars() -> list:
    """List avatars owned by this account (requires API key)."""
    if not settings.LIVEAVATAR_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{LA_BASE}/v1/avatars", headers=_headers())
            data = r.json()
        if r.status_code == 200:
            return data.get("data", {}).get("results", [])
    except Exception as e:
        logger.warning("LiveAvatar list_user_avatars error: %s", e)
    return []


# ── Keep-alive ─────────────────────────────────────────────────────────────────

async def keep_alive(session_token: str) -> bool:
    """Ping to prevent session timeout (call every 30s for long sessions)."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{LA_BASE}/v1/sessions/keep-alive",
                headers=_bearer(session_token),
            )
        return r.status_code == 200
    except Exception:
        return False

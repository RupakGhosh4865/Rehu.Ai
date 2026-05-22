"""
SuperHuman AI Persona Platform -- HeyGen Streaming Avatar Client
HeyGen Interactive Avatar v2 API: https://docs.heygen.com/reference/streaming-avatar

WebRTC flow:
  1. create_streaming_session()  -> session_id, access_token, ice_servers
  2. Browser creates RTCPeerConnection, creates SDP offer
  3. start_streaming_session()   -> forwards offer to HeyGen, returns SDP answer
  4. Browser sets remoteDescription with answer -> video stream begins
  5. ICE candidates exchanged via send_ice_candidate()
"""
import logging
from typing import Optional
import httpx

from .config import settings

logger = logging.getLogger(__name__)

HEYGEN_BASE = settings.HEYGEN_API_BASE


def _headers() -> dict:
    return {
        "x-api-key": settings.HEYGEN_API_KEY,
        "Content-Type": "application/json",
    }


def _empty_session() -> dict:
    return {
        "session_id": "",
        "access_token": "",
        "url": "",
        "ice_servers": [],
    }


async def create_streaming_session(
    avatar_id: Optional[str] = None,
    voice_id: Optional[str] = None,
    quality: Optional[str] = None,
) -> dict:
    """
    Create a new HeyGen streaming session.
    Returns session_id, access_token, ice_servers for WebRTC setup.
    Falls back to empty session (voice-only) if HeyGen is unavailable.
    """
    if not settings.HEYGEN_API_KEY:
        logger.warning("HEYGEN_API_KEY not set -- starting voice-only session")
        return _empty_session()

    avatar_id = avatar_id or settings.HEYGEN_AVATAR_ID
    heygen_voice = voice_id or settings.HEYGEN_VOICE_ID
    quality = quality or settings.HEYGEN_QUALITY

    payload = {
        "quality": quality,
        "avatar_id": avatar_id,
        "version": "v2",
        "video_encoding": "H264",
    }
    if heygen_voice:
        payload["voice"] = {"voice_id": heygen_voice}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{HEYGEN_BASE}/v1/streaming.new",
                headers=_headers(),
                json=payload,
            )
            if resp.status_code >= 400:
                logger.warning("HeyGen streaming.new HTTP %s: %s", resp.status_code, resp.text[:500])
                return _empty_session()

            data = resp.json()

        if data.get("code") != 100:
            logger.warning("HeyGen session creation failed: %s", data.get("message", "Unknown error"))
            return _empty_session()

        sd = data["data"]
        logger.info("HeyGen session created: %s", sd["session_id"])
        return {
            "session_id": sd["session_id"],
            "access_token": sd["access_token"],
            "url": sd.get("url", ""),
            "ice_servers": sd.get("ice_servers2", sd.get("ice_servers", [])),
        }
    except Exception as e:
        logger.warning("HeyGen unavailable, voice-only session: %s", e)
        return _empty_session()


async def start_streaming_session(session_id: str, sdp_offer: dict) -> dict:
    """
    Complete WebRTC handshake.
    Browser sends its SDP offer -> HeyGen returns SDP answer -> browser sets remoteDescription.
    Returns: {"success": bool, "sdp": {type, sdp}}
    """
    if not session_id:
        return {"success": False, "sdp": None}

    payload = {
        "session_id": session_id,
        "sdp": sdp_offer,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{HEYGEN_BASE}/v1/streaming.start",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") == 100:
            sdp_answer = data["data"].get("sdp")
            logger.info("HeyGen WebRTC handshake complete: %s", session_id)
            return {"success": True, "sdp": sdp_answer}
        else:
            logger.error("HeyGen session start failed: %s", data.get("message"))
            return {"success": False, "sdp": None}
    except Exception as e:
        logger.error("HeyGen start_streaming_session error: %s", e)
        return {"success": False, "sdp": None}


async def send_ice_candidate(session_id: str, candidate: dict) -> bool:
    """Forward a browser ICE candidate to HeyGen."""
    if not session_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{HEYGEN_BASE}/v1/streaming.ice",
                headers=_headers(),
                json={"session_id": session_id, "candidate": candidate},
            )
            return resp.status_code == 200
    except Exception as e:
        logger.warning("HeyGen ICE error: %s", e)
        return False


async def speak(session_id: str, text: str, task_type: str = "talk") -> dict:
    """
    Send text to the avatar for real-time lip-synced speech.
    task_type: 'talk' = natural pacing, 'repeat' = immediate.
    """
    if not session_id:
        return {"task_id": "", "status": "skipped"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{HEYGEN_BASE}/v1/streaming.task",
                headers=_headers(),
                json={"session_id": session_id, "text": text, "task_type": task_type},
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") == 100:
            return {"task_id": data["data"].get("task_id", ""), "status": "sent"}
        else:
            logger.warning("HeyGen speak failed: %s", data.get("message"))
            return {"task_id": "", "status": "error", "message": data.get("message")}
    except Exception as e:
        logger.warning("HeyGen speak error: %s", e)
        return {"task_id": "", "status": "error"}


async def interrupt(session_id: str) -> bool:
    """Stop the avatar mid-speech (user interrupted)."""
    if not session_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{HEYGEN_BASE}/v1/streaming.interrupt",
                headers=_headers(),
                json={"session_id": session_id},
            )
            return resp.status_code == 200
    except Exception as e:
        logger.warning("HeyGen interrupt error: %s", e)
        return False


async def stop_session(session_id: str) -> bool:
    """End and clean up a HeyGen streaming session."""
    if not session_id:
        return True
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{HEYGEN_BASE}/v1/streaming.stop",
                headers=_headers(),
                json={"session_id": session_id},
            )
            logger.info("HeyGen session stopped: %s", session_id)
            return resp.status_code == 200
    except Exception as e:
        logger.warning("HeyGen stop error: %s", e)
        return False


async def list_avatars() -> list:
    """List available HeyGen streaming avatars. Tries v2 then v1 endpoints."""
    endpoints = [
        ("/v2/avatars",           lambda d: d.get("data", {}).get("avatars", []) if isinstance(d.get("data"), dict) else d.get("data", [])),
        ("/v1/streaming_avatar.list", lambda d: d.get("data", {}).get("avatars", [])),
    ]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for path, extractor in endpoints:
                resp = await client.get(f"{HEYGEN_BASE}{path}", headers=_headers())
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        avatars = extractor(data)
                        if avatars:
                            logger.info("HeyGen avatars loaded from %s: %d", path, len(avatars))
                            return avatars
                    except Exception:
                        pass
    except Exception as e:
        logger.warning("HeyGen list_avatars error: %s", e)
    return []


async def list_voices() -> list:
    """List available HeyGen voices."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{HEYGEN_BASE}/v1/voices", headers=_headers())
            data = resp.json()
        if data.get("code") == 100:
            return data.get("data", {}).get("voices", [])
    except Exception as e:
        logger.warning("HeyGen list_voices error: %s", e)
    return []

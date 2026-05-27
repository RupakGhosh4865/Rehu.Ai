"""
Savant.ai — Daily.co Room Management
Creates/deletes Daily.co rooms and generates short-lived participant tokens.
"""
import logging
import time
from typing import Optional
import httpx

from .config import settings

logger = logging.getLogger(__name__)

DAILY_BASE = settings.DAILY_API_BASE


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.DAILY_API_KEY}",
        "Content-Type": "application/json",
    }


async def create_room(room_name: Optional[str] = None) -> dict:
    """
    Create a Daily.co room for a session.
    Returns room name, url, and expiry timestamp.
    """
    import uuid
    name = room_name or f"sh-{uuid.uuid4().hex[:12]}"
    exp = int(time.time()) + settings.DAILY_ROOM_EXPIRY_SECONDS

    payload = {
        "name": name,
        "properties": {
            "exp": exp,
            "enable_prejoin_ui": False,
            "start_video_off": True,
            "start_audio_off": False,
            "enable_screenshare": False,
            "enable_chat": False,
            "max_participants": 2,  # visitor + AI bot
        },
    }

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            f"{DAILY_BASE}/rooms",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    logger.info(f"Daily.co room created: {data['name']}")
    return {
        "name": data["name"],
        "url": data["url"],
        "exp": exp,
    }


async def create_token(room_name: str, is_owner: bool = False) -> str:
    """
    Create a short-lived meeting token for a participant.
    Owners (the bot) get is_owner=True; visitors get is_owner=False.
    """
    exp = int(time.time()) + settings.DAILY_ROOM_EXPIRY_SECONDS

    payload = {
        "properties": {
            "room_name": room_name,
            "exp": exp,
            "is_owner": is_owner,
            "enable_screenshare": False,
            "start_video_off": True,
        }
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{DAILY_BASE}/meeting-tokens",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    return data["token"]


async def delete_room(room_name: str) -> bool:
    """Delete a Daily.co room after a session ends."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.delete(
            f"{DAILY_BASE}/rooms/{room_name}",
            headers=_headers(),
        )
        success = resp.status_code in (200, 204)
        if success:
            logger.info(f"Daily.co room deleted: {room_name}")
        return success


async def get_room_info(room_name: str) -> Optional[dict]:
    """Get current info about a room (participant count, etc.)."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{DAILY_BASE}/rooms/{room_name}",
            headers=_headers(),
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

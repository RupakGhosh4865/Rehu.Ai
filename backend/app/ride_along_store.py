"""
Tiny JSON-backed registry of active ride-along meeting bots.

Holds two layers:

  - On-disk snapshot (`ride_along.json` under the active tenant's data dir) so
    Admin can see what's running across restarts.
  - In-memory map of live `MeetingBot` instances so we can call `.speak()` /
    `.leave()` on them by id.

Each record:
  {
    "bot_id": "...",
    "meeting_url": "https://meet.google.com/abc-defg-hij",
    "persona_id": "default",
    "bot_name": "Maya | AI Specialist",
    "platform": "meet|zoom|teams|unknown",
    "status":   "pending|joined|leaving|left|error",
    "joined_at": "2026-05-27T...Z",
    "tenant_id": "default",
    "la_session_id": "..."
  }
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from . import tenants

if TYPE_CHECKING:  # pragma: no cover
    from .meeting_bot import MeetingBot

logger = logging.getLogger(__name__)

_active_bots: dict[str, "MeetingBot"] = {}


def _store_file() -> Path:
    return tenants.tenant_dir(tenants.active_tenant_id()) / "ride_along.json"


def _ensure_dir() -> None:
    _store_file().parent.mkdir(parents=True, exist_ok=True)


def _load_all() -> list[dict]:
    path = _store_file()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or []
        return data if isinstance(data, list) else []
    except Exception as e:  # noqa: BLE001
        logger.warning("ride_along_store: could not load: %s", e)
        return []


def _save_all(records: list[dict]) -> None:
    _ensure_dir()
    with open(_store_file(), "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


# ── CRUD ──────────────────────────────────────────────────────────────────

def register(bot: "MeetingBot") -> dict:
    snapshot = bot.snapshot()
    snapshot["tenant_id"] = tenants.active_tenant_id()
    _active_bots[bot.bot_id] = bot
    records = [r for r in _load_all() if r.get("bot_id") != bot.bot_id]
    records.append(snapshot)
    _save_all(records)
    return snapshot


def update(bot_id: str, **patch) -> Optional[dict]:
    records = _load_all()
    updated: Optional[dict] = None
    for r in records:
        if r.get("bot_id") == bot_id:
            r.update({k: v for k, v in patch.items() if v is not None})
            updated = r
            break
    if updated is not None:
        _save_all(records)
    return updated


def get(bot_id: str) -> Optional[dict]:
    for r in _load_all():
        if r.get("bot_id") == bot_id:
            return r
    return None


def get_live(bot_id: str) -> Optional["MeetingBot"]:
    return _active_bots.get(bot_id)


def remove(bot_id: str) -> bool:
    records = [r for r in _load_all() if r.get("bot_id") != bot_id]
    _save_all(records)
    _active_bots.pop(bot_id, None)
    return True


def list_all() -> list[dict]:
    return _load_all()

"""
Persist meeting requests captured from live conversations.
"""
import csv
import io
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import settings
from . import tenants

logger = logging.getLogger(__name__)


def _meetings_file() -> Path:
    return tenants.tenant_dir(tenants.active_tenant_id()) / "meetings.json"


def _ensure_dir() -> None:
    _meetings_file().parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict]:
    path = _meetings_file()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("Could not load meetings: %s", e)
        return []


def _save(meetings: list[dict]) -> None:
    _ensure_dir()
    with open(_meetings_file(), "w", encoding="utf-8") as f:
        json.dump(meetings, f, indent=2, ensure_ascii=False)


def create_meeting(payload: dict) -> dict:
    meetings = _load()
    meeting = {
        "meeting_id": uuid.uuid4().hex,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "status": payload.get("status") or "new",
        **payload,
    }
    meetings.append(meeting)
    _save(meetings)
    return meeting


def list_meetings(limit: int = 300) -> list[dict]:
    meetings = _load()
    meetings.sort(key=lambda m: m.get("created_at") or "", reverse=True)
    return meetings[:limit]


def update_meeting(meeting_id: str, status: str, notes: str | None = None) -> dict | None:
    meetings = _load()
    for meeting in meetings:
        if meeting.get("meeting_id") == meeting_id:
            meeting["status"] = status
            if notes is not None:
                meeting["notes"] = notes
            meeting["updated_at"] = _now_iso()
            _save(meetings)
            return meeting
    return None


def export_csv(limit: int = 500) -> str:
    rows = list_meetings(limit)
    buf = io.StringIO()
    fields = [
        "meeting_id", "created_at", "status", "visitor_name", "visitor_email",
        "company_name", "preferred_time", "timezone", "topic", "session_id", "persona_id",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fields})
    return buf.getvalue()

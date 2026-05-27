"""
Persist completed conversation sessions to disk.
"""
import csv
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import settings
from . import tenants

logger = logging.getLogger(__name__)


def _sessions_dir() -> Path:
    return tenants.tenant_dir(tenants.active_tenant_id()) / "sessions"


def _compliance_file() -> Path:
    return tenants.tenant_dir(tenants.active_tenant_id()) / "compliance_settings.json"
DEFAULT_COMPLIANCE_SETTINGS = {
    "consent_required": True,
    "consent_text": "This conversation may be transcribed and saved so the team can follow up and improve service.",
    "retention_days": 90,
    "store_audio": False,
    "pii_redaction": False,
}


def _ensure_dir() -> None:
    _sessions_dir().mkdir(parents=True, exist_ok=True)


def _session_path(session_id: str) -> Path:
    return _sessions_dir() / f"{session_id}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def finalize_session(record: dict) -> None:
    """Write a completed session record to disk."""
    _ensure_dir()
    session_id = record.get("session_id")
    if not session_id:
        return
    if not record.get("ended_at"):
        record["ended_at"] = _now_iso()
    path = _session_path(session_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
    logger.info("Session log saved: %s", session_id[:8])


def list_history(limit: int = 200) -> list[dict]:
    """List completed sessions, newest first."""
    _ensure_dir()
    sessions: list[dict] = []
    for path in _sessions_dir().glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                sessions.append(_summary(data))
        except Exception as e:
            logger.warning("Could not read session log %s: %s", path.name, e)
    sessions.sort(key=lambda s: s.get("started_at") or "", reverse=True)
    return sessions[:limit]


def get_session(session_id: str) -> Optional[dict]:
    path = _session_path(session_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Could not read session %s: %s", session_id, e)
        return None


def delete_session(session_id: str) -> bool:
    path = _session_path(session_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def _summary(data: dict) -> dict:
    transcript = data.get("transcript") or []
    return {
        "session_id": data.get("session_id"),
        "persona_id": data.get("persona_id"),
        "persona_name": data.get("persona_name"),
        "company_name": data.get("company_name"),
        "visitor_name": data.get("visitor_name"),
        "visitor_email": data.get("visitor_email"),
        "language": data.get("language", "en"),
        "started_at": data.get("started_at"),
        "ended_at": data.get("ended_at"),
        "message_count": len(transcript),
        "consent": data.get("consent", {}),
        "lead_summary": data.get("lead_summary", {}),
        "meeting_requests": data.get("meeting_requests", []),
        "product_interests": data.get("product_interests", []),
        "widget_mode": (data.get("metadata") or {}).get("widget_mode", False),
    }


def export_csv(limit: int = 500) -> str:
    """Export session summaries as CSV string."""
    rows = list_history(limit=limit)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "session_id", "started_at", "ended_at", "persona_id", "persona_name",
            "company_name", "visitor_name", "visitor_email", "language", "message_count",
            "lead_score", "next_best_action", "meeting_count",
        ],
    )
    writer.writeheader()
    for row in rows:
        summary = row.get("lead_summary") or {}
        payload = {k: row.get(k, "") for k in writer.fieldnames}
        payload["lead_score"] = summary.get("lead_score", "")
        payload["next_best_action"] = summary.get("next_best_action", "")
        payload["meeting_count"] = len(row.get("meeting_requests") or [])
        writer.writerow(payload)
    return buf.getvalue()


def load_compliance_settings() -> dict:
    _ensure_dir()
    path = _compliance_file()
    if not path.exists():
        return dict(DEFAULT_COMPLIANCE_SETTINGS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {**DEFAULT_COMPLIANCE_SETTINGS, **(data if isinstance(data, dict) else {})}
    except Exception as e:
        logger.warning("Could not load compliance settings: %s", e)
        return dict(DEFAULT_COMPLIANCE_SETTINGS)


def save_compliance_settings(settings_payload: dict) -> dict:
    _ensure_dir()
    current = load_compliance_settings()
    current.update(settings_payload or {})
    with open(_compliance_file(), "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
    return current

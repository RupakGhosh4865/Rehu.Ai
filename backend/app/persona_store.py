"""
Persist persona configs to disk (survives server restarts).
"""
import json
import logging
from pathlib import Path
from typing import Optional

from .config import settings
from .models import PersonaConfig
from . import tenants

logger = logging.getLogger(__name__)


def _personas_file() -> Path:
    return tenants.tenant_dir(tenants.active_tenant_id()) / "personas.json"


def _ensure_dir() -> None:
    _personas_file().parent.mkdir(parents=True, exist_ok=True)


def load_all() -> dict[str, PersonaConfig]:
    """Load saved persona overrides from disk for the current tenant."""
    path = _personas_file()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        out: dict[str, PersonaConfig] = {}
        for pid, data in (raw or {}).items():
            if isinstance(data, dict):
                out[pid] = PersonaConfig(**data)
        logger.info("Loaded %d persona(s) from %s", len(out), path)
        return out
    except Exception as e:
        logger.warning("Could not load personas from disk: %s", e)
        return {}


def save_all(personas: dict[str, PersonaConfig]) -> None:
    """Persist all persona configs for the current tenant."""
    _ensure_dir()
    path = _personas_file()
    payload = {pid: p.model_dump() for pid, p in personas.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logger.debug("Saved %d persona(s) to %s", len(payload), path)


def merge_into(base: dict[str, PersonaConfig], saved: dict[str, PersonaConfig]) -> dict[str, PersonaConfig]:
    """Overlay saved configs onto seeded defaults; add custom personas."""
    merged = dict(base)
    for pid, cfg in saved.items():
        if pid in merged:
            merged[pid] = merged[pid].model_copy(update={
                "persona_name": cfg.persona_name,
                "company_name": cfg.company_name,
                "tone": cfg.tone,
                "avatar_id": cfg.avatar_id,
                "voice_id": cfg.voice_id,
                "system_prompt_override": cfg.system_prompt_override,
                "calendly_url": cfg.calendly_url,
                "notification_email": cfg.notification_email,
            })
        else:
            merged[pid] = cfg
    return merged

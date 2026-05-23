"""
Persist persona configs to disk (survives server restarts).
"""
import json
import logging
from pathlib import Path
from typing import Optional

from .config import settings
from .models import PersonaConfig

logger = logging.getLogger(__name__)

PERSONAS_FILE = Path(settings.CHROMA_PERSIST_DIR).parent / "personas.json"


def _ensure_dir() -> None:
    PERSONAS_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_all() -> dict[str, PersonaConfig]:
    """Load saved persona overrides from disk."""
    if not PERSONAS_FILE.exists():
        return {}
    try:
        with open(PERSONAS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        out: dict[str, PersonaConfig] = {}
        for pid, data in (raw or {}).items():
            if isinstance(data, dict):
                out[pid] = PersonaConfig(**data)
        logger.info("Loaded %d persona(s) from %s", len(out), PERSONAS_FILE)
        return out
    except Exception as e:
        logger.warning("Could not load personas from disk: %s", e)
        return {}


def save_all(personas: dict[str, PersonaConfig]) -> None:
    """Persist all persona configs."""
    _ensure_dir()
    payload = {pid: p.model_dump() for pid, p in personas.items()}
    with open(PERSONAS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logger.debug("Saved %d persona(s) to %s", len(payload), PERSONAS_FILE)


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
            })
        else:
            merged[pid] = cfg
    return merged

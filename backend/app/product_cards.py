"""
Product presentation cards surfaced during live demos.
"""
import json
import logging
import re
from pathlib import Path
from typing import Optional

from .config import settings
from . import tenants

logger = logging.getLogger(__name__)


def _product_cards_file() -> Path:
    return tenants.tenant_dir(tenants.active_tenant_id()) / "product_cards.json"

PERSONA_PRODUCT_CARDS: dict[str, list[dict]] = {
    "product-demo": [
        {
            "id": "overview",
            "title": "Product Suite",
            "subtitle": "Intelligent automation for your operations",
            "image_url": "",
            "keywords": ["welcome", "overview", "product", "suite", "company", "demo"],
            "default": True,
        },
        {
            "id": "automation",
            "title": "Autonomous Data Collection",
            "subtitle": "Inspect, monitor, and capture data in challenging environments",
            "image_url": "",
            "keywords": ["automation", "autonomous", "data", "collection", "inspect", "monitor", "sensor"],
        },
        {
            "id": "humanoid",
            "title": "Advanced Humanoid Platform",
            "subtitle": "World-class mobility and manipulation for complex tasks",
            "image_url": "",
            "keywords": ["humanoid", "atlas", "mobility", "manipulation", "advanced", "robot"],
        },
        {
            "id": "warehouse",
            "title": "Warehouse Automation",
            "subtitle": "Flexible case handling and logistics at scale",
            "image_url": "",
            "keywords": ["warehouse", "stretch", "logistics", "case", "handling", "fulfillment"],
        },
    ],
    "demo-host": [
        {
            "id": "overview",
            "title": "Product Suite",
            "subtitle": "Live walkthrough tailored to your needs",
            "image_url": "",
            "keywords": ["welcome", "overview", "product", "demo"],
            "default": True,
        },
    ],
    "default": [
        {
            "id": "overview",
            "eyebrow": "Website inbound superhuman",
            "title": "More qualified meetings, zero extra headcount",
            "subtitle": "Aiza answers questions, qualifies interest, and guides visitors to the right next step in real time.",
            "image_url": "",
            "slide_images": [],
            "keywords": ["welcome", "overview", "product", "solution", "service", "demo", "pricing"],
            "bullets": [
                "Answer buyer questions in real time",
                "Qualify visitors and capture next-step intent",
                "Uncover pain points and align the right solution",
                "Share tailored product or service value",
            ],
            "value_points": [
                "Create a frictionless always-on buyer journey",
                "Increase qualified conversation volume",
                "Turn website traffic into actionable pipeline",
                "Give every visitor your best expert instantly",
            ],
            "default": True,
        },
    ],
}


def get_product_cards(persona_id: str) -> list[dict]:
    custom = _load_custom_cards()
    return custom.get(persona_id) or PERSONA_PRODUCT_CARDS.get(persona_id) or PERSONA_PRODUCT_CARDS.get("default", [])


def match_product_card(persona_id: str, text: str) -> Optional[dict]:
    """Return the best-matching product card for transcript text."""
    cards = get_product_cards(persona_id)
    if not cards or not text:
        default = next((c for c in cards if c.get("default")), cards[0])
        return default

    lower = text.lower()
    best, best_score = None, 0
    for card in cards:
        score = sum(1 for kw in card.get("keywords", []) if kw in lower)
        if score > best_score:
            best_score, best = score, card

    if best_score > 0:
        return best
    return next((c for c in cards if c.get("default")), cards[0])


def _ensure_dir() -> None:
    _product_cards_file().parent.mkdir(parents=True, exist_ok=True)


def _load_custom_cards() -> dict[str, list[dict]]:
    path = _product_cards_file()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("Could not load product cards: %s", e)
        return {}


def _save_custom_cards(cards_by_persona: dict[str, list[dict]]) -> None:
    _ensure_dir()
    with open(_product_cards_file(), "w", encoding="utf-8") as f:
        json.dump(cards_by_persona, f, indent=2, ensure_ascii=False)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "card"


def upsert_product_card(persona_id: str, card: dict) -> dict:
    custom = _load_custom_cards()
    cards = list(custom.get(persona_id) or [])
    card_id = card.get("id") or _slugify(card.get("title", "card"))
    clean = {
        "id": card_id,
        "title": card.get("title", "Untitled"),
        "subtitle": card.get("subtitle", ""),
        "eyebrow": card.get("eyebrow", ""),
        "image_url": card.get("image_url", ""),
        "slide_images": card.get("slide_images", []),
        "keywords": card.get("keywords", []),
        "bullets": card.get("bullets", []),
        "value_points": card.get("value_points", []),
        "cta_label": card.get("cta_label", "Learn more"),
        "cta_url": card.get("cta_url", ""),
        "default": bool(card.get("default", False)),
    }
    if clean["default"]:
        for existing in cards:
            existing["default"] = False
    for idx, existing in enumerate(cards):
        if existing.get("id") == card_id:
            cards[idx] = clean
            break
    else:
        cards.append(clean)
    custom[persona_id] = cards
    _save_custom_cards(custom)
    return clean


def has_custom_cards(persona_id: str) -> bool:
    return bool((_load_custom_cards().get(persona_id) or []))


def delete_product_card(persona_id: str, card_id: str) -> bool:
    custom = _load_custom_cards()
    cards = custom.get(persona_id) or []
    next_cards = [card for card in cards if card.get("id") != card_id]
    if len(next_cards) == len(cards):
        return False
    custom[persona_id] = next_cards
    _save_custom_cards(custom)
    return True

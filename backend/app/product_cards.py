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
            "eyebrow": "Live product demo",
            "title": "AI Superhumans for your business",
            "subtitle": "Deploy expert avatars on your website, in your product, and on calls — trained on your knowledge.",
            "image_url": "",
            "slide_images": [],
            "keywords": ["welcome", "overview", "product", "demo", "company", "services", "offerings", "walkthrough"],
            "bullets": [
                "Answer buyer questions in real time",
                "Qualify visitors and capture intent",
                "Walk through features live on demand",
                "Book follow-ups without extra headcount",
            ],
            "value_points": [
                "Always-on expert coverage on every page",
                "Higher qualified conversation volume",
                "Faster path from interest to pipeline",
                "Consistent messaging from your knowledge base",
            ],
            "default": True,
        },
        {
            "id": "features",
            "eyebrow": "Platform capabilities",
            "title": "Train, deploy, and convert",
            "subtitle": "Upload your site and docs — Aiza learns your tone, products, and FAQs in minutes.",
            "image_url": "",
            "slide_images": [],
            "keywords": ["features", "capabilities", "how it works", "platform", "train", "knowledge", "rag"],
            "bullets": [
                "Knowledge base from URLs, PDFs, and pasted text",
                "Persona prompts tuned per use case",
                "LiveAvatar video with natural voice",
                "Lead capture and CRM sync on every call",
            ],
            "value_points": [
                "No engineering sprint to go live",
                "Grounded answers from your data",
                "Feels human, not a chatbot widget",
                "Works on web, in-app, and meeting mocks",
            ],
        },
        {
            "id": "pricing",
            "eyebrow": "Commercial model",
            "title": "Sales-led setup",
            "subtitle": "We scope your use case, train your Superhuman, and deploy with a setup fee plus monthly contract.",
            "image_url": "",
            "slide_images": [],
            "keywords": ["pricing", "cost", "plans", "budget", "contract", "setup", "monthly", "fee"],
            "bullets": [
                "Discovery call to define goals and persona",
                "Custom knowledge and slide deck setup",
                "Production LiveAvatar after contract",
                "Ongoing training and optimization",
            ],
            "value_points": [
                "Right-sized for your traffic and use cases",
                "White-glove onboarding from Savant team",
                "Predictable monthly investment",
                "ROI vs hiring dedicated demo staff",
            ],
        },
        {
            "id": "integrations",
            "eyebrow": "Connect your stack",
            "title": "CRM, calendar, and webhooks",
            "subtitle": "Leads flow to Smartsheet, HubSpot, email, and webhooks — meetings book on Google Calendar.",
            "image_url": "",
            "slide_images": [],
            "keywords": ["integrate", "integration", "crm", "smartsheet", "hubspot", "calendar", "webhook", "api"],
            "bullets": [
                "Smartsheet and HubSpot lead sync",
                "Google Calendar booking with Meet links",
                "Webhook payloads for Zapier or Make",
                "Embed widget on any site in minutes",
            ],
            "value_points": [
                "No manual lead copying",
                "Sales gets context from every conversation",
                "Fits existing ops workflows",
                "Enterprise-ready notification controls",
            ],
        },
        {
            "id": "next-steps",
            "eyebrow": "What happens next",
            "title": "Book your Savant setup call",
            "subtitle": "Share your name and email — our team will scope your Superhuman and send a proposal.",
            "image_url": "",
            "slide_images": [],
            "keywords": ["book", "demo", "meeting", "trial", "next step", "follow up", "contact", "proposal"],
            "bullets": [
                "5-minute live demo on this site",
                "Full production avatar after signup",
                "Custom slides and knowledge indexing",
                "Dedicated onboarding from Savant",
            ],
            "value_points": [
                "See your use case live before you commit",
                "Clear setup fee and contract terms",
                "Go live on your domain quickly",
                "Ongoing support from the Savant team",
            ],
            "cta_label": "Talk to Savant",
            "cta_url": "https://savant.ai/#contact",
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


def seed_from_studio_training(persona_id: str, company_name: str) -> None:
    """Create starter product cards after homepage studio training if none exist yet."""
    if has_custom_cards(persona_id):
        upsert_product_card(persona_id, {
            "id": "studio-overview",
            "eyebrow": f"{company_name} Superhuman",
            "title": f"What {company_name} offers",
            "subtitle": "Trained from your website and product description — customize slides in Admin.",
            "keywords": ["overview", "product", "service", "company", "welcome", "demo", "walkthrough"],
            "bullets": [
                f"Represents {company_name} with your tone and facts",
                "Answers questions from indexed website content",
                "Captures visitor name and email for follow-up",
                "Presents slides when visitors ask for an overview",
            ],
            "value_points": [
                "Always-on expert on your site",
                "Grounded in the knowledge you just added",
                "Ready to test in Studio after training",
                "Expand with Admin → Product Cards",
            ],
            "default": True,
        })
        return
    for card in PERSONA_PRODUCT_CARDS.get(persona_id) or PERSONA_PRODUCT_CARDS.get("default", []):
        customized = dict(card)
        if customized.get("id") == "overview":
            customized["eyebrow"] = f"{company_name} Superhuman"
            customized["title"] = f"What {company_name} offers"
            customized["subtitle"] = (
                f"Live demo specialist for {company_name} — trained on your site and product copy."
            )
        upsert_product_card(persona_id, customized)

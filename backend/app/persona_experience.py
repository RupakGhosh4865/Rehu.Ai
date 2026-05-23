"""
Persona presentation metadata: avatars, roles, immersive connect copy.
"""
from typing import Optional

# persona_id -> avatar name keywords (first match wins)
AVATAR_KEYWORDS: dict[str, list[str]] = {
    "default": ["sarah", "maya", "emma", "angela", "sofia", "woman", "female"],
    "hr-interviewer": ["james", "david", "michael", "alex", "man", "male", "professional"],
    "onboarding-guide": ["sam", "chris", "ryan", "friendly", "man"],
    "human-chatbot": ["jordan", "lily", "grace", "woman"],
    "support-agent": ["riley", "anna", "emily", "woman"],
    "product-demo": ["casey", "nicole", "jessica", "woman", "presenter"],
    "demo-host": ["casey", "nicole", "woman"],
    "healthcare-guide": ["elena", "nina", "sarah", "woman", "doctor"],
    "meeting-assistant": ["taylor", "thomas", "man", "professional"],
}

PERSONA_EXPERIENCE: dict[str, dict] = {
    "default": {
        "role_title": "Sales & Product Specialist",
        "brand_line": "SuperHuman",
        "connecting_messages": [
            "SuperHuman Maya is joining the call in a few seconds…",
            "Connecting you with your AI product specialist…",
            "Preparing a secure live video session…",
            "Almost ready — Maya will greet you personally…",
        ],
        "fallback_preview": "https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?w=800&q=85",
    },
    "hr-interviewer": {
        "role_title": "HR Interview Assistant",
        "brand_line": "SuperHuman",
        "connecting_messages": [
            "SuperHuman Alex is joining your interview room…",
            "Connecting you with your AI talent specialist…",
            "Loading structured screening protocol…",
            "Almost ready — your interview will begin shortly…",
        ],
        "fallback_preview": "https://images.unsplash.com/photo-1560250097-0b93528c311a?w=800&q=85",
    },
    "product-demo": {
        "role_title": "Product Demo Assistant",
        "brand_line": "SuperHuman",
        "connecting_messages": [
            "SuperHuman Casey is joining to walk you through the product…",
            "Connecting you with your interactive demo specialist…",
            "Preparing your personalized product tour…",
            "Almost ready — Casey will start the demo in moments…",
        ],
        "fallback_preview": "https://images.unsplash.com/photo-1580489944761-15a19d654956?w=800&q=85",
    },
    "demo-host": {
        "role_title": "Product Demo Assistant",
        "brand_line": "SuperHuman",
        "connecting_messages": [
            "SuperHuman Casey is joining to walk you through the product…",
            "Connecting you with your interactive demo specialist…",
            "Preparing your personalized product tour…",
            "Almost ready — your demo will begin shortly…",
        ],
        "fallback_preview": "https://images.unsplash.com/photo-1580489944761-15a19d654956?w=800&q=85",
    },
    "healthcare-guide": {
        "role_title": "Healthcare Patient Guide",
        "brand_line": "SuperHuman",
        "connecting_messages": [
            "SuperHuman Elena is joining to assist you…",
            "Connecting you with your healthcare guide…",
            "Preparing a private, supportive conversation…",
            "Almost ready — Elena will help you step by step…",
        ],
        "fallback_preview": "https://images.unsplash.com/photo-1559839734-2b71ea197ec2?w=800&q=85",
    },
    "onboarding-guide": {
        "role_title": "Employee Onboarding Guide",
        "brand_line": "SuperHuman",
        "connecting_messages": [
            "SuperHuman Sam is joining your onboarding session…",
            "Connecting you with your workplace guide…",
            "Loading your company handbook context…",
            "Almost ready — Sam will welcome you shortly…",
        ],
        "fallback_preview": "https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=800&q=85",
    },
    "human-chatbot": {
        "role_title": "Customer Experience Specialist",
        "brand_line": "SuperHuman",
        "connecting_messages": [
            "SuperHuman Jordan is joining the conversation…",
            "Connecting you with a live specialist — not a chatbot…",
            "Preparing voice and video support…",
            "Almost ready — Jordan will be with you in seconds…",
        ],
        "fallback_preview": "https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=800&q=85",
    },
    "support-agent": {
        "role_title": "Customer Support Specialist",
        "brand_line": "SuperHuman",
        "connecting_messages": [
            "SuperHuman Riley is joining to help resolve your request…",
            "Connecting you with your support specialist…",
            "Reviewing common solutions for your issue…",
            "Almost ready — Riley will assist you shortly…",
        ],
        "fallback_preview": "https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=800&q=85",
    },
    "meeting-assistant": {
        "role_title": "Meeting Assistant",
        "brand_line": "SuperHuman",
        "connecting_messages": [
            "SuperHuman Taylor is joining the meeting…",
            "Connecting your AI meeting participant…",
            "Preparing presentation and Q&A mode…",
            "Almost ready — Taylor will join shortly…",
        ],
        "fallback_preview": "https://images.unsplash.com/photo-1519085360753-af0119f7cbe7?w=800&q=85",
    },
}


def get_experience(persona_id: str, persona_name: str = "Maya") -> dict:
    base = PERSONA_EXPERIENCE.get(persona_id) or PERSONA_EXPERIENCE["default"]
    messages = [
        m.replace("Maya", persona_name).replace("Alex", persona_name)
         .replace("Casey", persona_name).replace("Elena", persona_name)
         .replace("Sam", persona_name).replace("Jordan", persona_name)
         .replace("Riley", persona_name).replace("Taylor", persona_name)
        for m in base["connecting_messages"]
    ]
    return {
        "persona_id": persona_id,
        "persona_name": persona_name,
        "role_title": base["role_title"],
        "brand_line": base["brand_line"],
        "connecting_messages": messages,
        "preview_url": base.get("fallback_preview"),
        "avatar_keywords": AVATAR_KEYWORDS.get(persona_id, AVATAR_KEYWORDS["default"]),
    }


def pick_avatar_preview(persona_id: str, avatars: list, persona_name: str = "") -> Optional[str]:
    keywords = AVATAR_KEYWORDS.get(persona_id, AVATAR_KEYWORDS["default"])
    if persona_name:
        keywords = [persona_name.lower()] + keywords
    for kw in keywords:
        for av in avatars:
            name = (av.get("name") or "").lower()
            if kw in name and av.get("preview_url"):
                return av["preview_url"]
    for av in avatars:
        if av.get("preview_url"):
            return av["preview_url"]
    exp = get_experience(persona_id, persona_name)
    return exp.get("preview_url")

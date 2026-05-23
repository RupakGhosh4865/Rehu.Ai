"""
Persona presentation metadata: LiveAvatar bindings, roles, immersive connect copy.
UI previews use the same LiveAvatar stock avatar that joins the live call.
"""
from typing import Optional

# Explicit LiveAvatar public avatar per persona (same face in preview + live call)
PERSONA_LIVEAVATAR_MAP: dict[str, dict] = {
    "default": {
        "avatar_id": "26393b8e-e944-4367-98ef-e2bc75c4b792",
        "avatar_name": "Katya in Black Suit",
    },
    "hr-interviewer": {
        "avatar_id": "9650a758-1085-4d49-8bf3-f347565ec229",
        "avatar_name": "Silas HR",
    },
    "healthcare-guide": {
        "avatar_id": "567e8371-f69f-49ec-9f2d-054083431165",
        "avatar_name": "Ann Doctor Standing",
    },
    "support-agent": {
        "avatar_id": "dc2935cf-5863-4f08-943b-c7478aea59fb",
        "avatar_name": "Silas Customer Support",
    },
    "product-demo": {
        "avatar_id": "64b526e4-741c-43b6-a918-4e40f3261c7a",
        "avatar_name": "Bryan Tech Expert",
    },
    "demo-host": {
        "avatar_id": "64b526e4-741c-43b6-a918-4e40f3261c7a",
        "avatar_name": "Bryan Tech Expert",
    },
    "onboarding-guide": {
        "avatar_id": "509609b9-cda3-4f74-b1b2-97b4d98834fd",
        "avatar_name": "Anthony in White Suit",
    },
    "human-chatbot": {
        "avatar_id": "7b888024-f8c9-4205-95e1-78ce01497bda",
        "avatar_name": "Shawn Therapist",
    },
    "meeting-assistant": {
        "avatar_id": "0930fd59-c8ad-434d-ad53-b391a1768720",
        "avatar_name": "Dexter Lawyer",
    },
}

AVATAR_KEYWORDS: dict[str, list[str]] = {
    "default": ["katya", "amina", "rika", "woman", "suit"],
    "hr-interviewer": ["silas hr", "june hr", "judy hr", "graham", "anthony", "hr"],
    "product-demo": ["bryan tech", "elenora tech", "tech expert"],
    "healthcare-guide": ["ann doctor", "judy doctor", "dexter doctor", "doctor"],
    "support-agent": ["silas customer", "support", "shawn"],
    "human-chatbot": ["shawn", "therapist", "ann therapist"],
    "onboarding-guide": ["anthony", "graham", "bryan"],
    "demo-host": ["bryan tech", "elenora tech"],
    "meeting-assistant": ["dexter", "lawyer", "graham"],
}

PERSONA_EXPERIENCE: dict[str, dict] = {
    "default": {
        "role_title": "Business Consultant · Sales & Product",
        "connecting_messages": [
            "Hold on — we're connecting you to your smartest AI person…",
            "Your specialist is joining now…",
            "Preparing your real-time conversation experience…",
            "Maya is almost here — get ready to talk…",
        ],
    },
    "hr-interviewer": {
        "role_title": "Professional Recruiter · HR Interviews",
        "connecting_messages": [
            "Hold on — Alex is preparing your interview room…",
            "Your recruiter is joining now…",
            "Preparing your real-time interview experience…",
            "Almost ready — your screening conversation starts shortly…",
        ],
    },
    "product-demo": {
        "role_title": "Product Demo Specialist",
        "connecting_messages": [
            "Hold on — Casey is setting up your product walkthrough…",
            "Your demo specialist is joining now…",
            "Preparing your interactive demo experience…",
        ],
    },
    "demo-host": {
        "role_title": "Product Demo Specialist",
        "connecting_messages": [
            "Hold on — Casey is setting up your product walkthrough…",
            "Your demo specialist is joining now…",
            "Preparing your interactive demo experience…",
        ],
    },
    "healthcare-guide": {
        "role_title": "Caring Medical Assistant · Patient Guide",
        "connecting_messages": [
            "Hold on — Elena is preparing a private conversation for you…",
            "Your care guide is joining now…",
            "Preparing your real-time support experience…",
        ],
    },
    "support-agent": {
        "role_title": "Friendly Support Specialist",
        "connecting_messages": [
            "Hold on — Riley is connecting to help you…",
            "Your support specialist is joining now…",
            "Preparing your real-time assistance experience…",
        ],
    },
    "human-chatbot": {
        "role_title": "Customer Experience Specialist",
        "connecting_messages": [
            "Hold on — we're connecting you to a live specialist…",
            "Jordan is joining now — not a chatbot, a person…",
            "Preparing your real-time conversation experience…",
        ],
    },
    "onboarding-guide": {
        "role_title": "Employee Onboarding Guide",
        "connecting_messages": [
            "Hold on — Sam is preparing your onboarding session…",
            "Your workplace guide is joining now…",
            "Preparing your real-time conversation experience…",
        ],
    },
    "meeting-assistant": {
        "role_title": "Meeting Assistant",
        "connecting_messages": [
            "Hold on — Taylor is joining the meeting…",
            "Your meeting assistant is connecting now…",
            "Preparing your real-time session…",
        ],
    },
}

_avatar_bindings: dict[str, dict] = {}


def bind_live_avatars(avatars: list) -> None:
    """Resolve LiveAvatar IDs and preview URLs for each persona from the public catalog."""
    global _avatar_bindings
    _avatar_bindings = {}
    if not avatars:
        return

    by_id = {a["id"]: a for a in avatars if a.get("id")}
    by_name = {(a.get("name") or "").lower(): a for a in avatars}
    used_ids: set[str] = set()

    def _bind(persona_id: str, avatar: dict) -> None:
        used_ids.add(avatar["id"])
        voice = avatar.get("default_voice")
        if isinstance(voice, dict):
            voice = voice.get("id")
        _avatar_bindings[persona_id] = {
            "avatar_id": avatar["id"],
            "avatar_name": avatar.get("name", ""),
            "preview_url": avatar.get("preview_url") or "",
            "voice_id": voice or None,
        }

    for persona_id, spec in PERSONA_LIVEAVATAR_MAP.items():
        av = by_id.get(spec["avatar_id"]) or by_name.get(spec["avatar_name"].lower())
        if av and av["id"] not in used_ids:
            _bind(persona_id, av)

    for persona_id in PERSONA_EXPERIENCE:
        if persona_id in _avatar_bindings:
            continue
        av = _match_avatar(persona_id, avatars, used_ids)
        if av:
            _bind(persona_id, av)


def _match_avatar(persona_id: str, avatars: list, used: set) -> Optional[dict]:
    keywords = AVATAR_KEYWORDS.get(persona_id, AVATAR_KEYWORDS["default"])
    best, best_score = None, 0
    for av in avatars:
        if av.get("id") in used:
            continue
        name = (av.get("name") or "").lower()
        score = sum(2 if kw in name else 0 for kw in keywords)
        if score > best_score:
            best_score, best = score, av
    if best:
        return best
    available = [a for a in avatars if a.get("id") not in used]
    if not available:
        return None
    return available[hash(persona_id) % len(available)]


def get_avatar_binding(persona_id: str) -> Optional[dict]:
    return _avatar_bindings.get(persona_id) or _avatar_bindings.get("default")


def resolve_avatar_id(persona_id: str, fallback: Optional[str] = None) -> Optional[str]:
    b = get_avatar_binding(persona_id)
    return (b or {}).get("avatar_id") or fallback


def resolve_voice_id(persona_id: str, fallback: Optional[str] = None) -> Optional[str]:
    b = get_avatar_binding(persona_id)
    return (b or {}).get("voice_id") or fallback


def get_experience(persona_id: str, persona_name: str = "Maya") -> dict:
    base = PERSONA_EXPERIENCE.get(persona_id) or PERSONA_EXPERIENCE["default"]
    binding = get_avatar_binding(persona_id)
    messages = []
    for m in base["connecting_messages"]:
        msg = m
        for old in ("Maya", "Alex", "Casey", "Elena", "Sam", "Jordan", "Riley", "Taylor"):
            msg = msg.replace(old, persona_name)
        messages.append(msg)
    preview = (binding or {}).get("preview_url") or ""
    return {
        "persona_id": persona_id,
        "persona_name": persona_name,
        "role_title": base["role_title"],
        "connecting_messages": messages,
        "preview_url": preview,
        "avatar_id": (binding or {}).get("avatar_id"),
        "live_avatar_name": (binding or {}).get("avatar_name"),
        "voice_id": (binding or {}).get("voice_id"),
    }


def pick_avatar_preview(persona_id: str, avatars: list, persona_name: str = "") -> str:
    """Return LiveAvatar preview URL so landing page matches the live session face."""
    binding = get_avatar_binding(persona_id)
    if binding and binding.get("preview_url"):
        return binding["preview_url"]
    exp = get_experience(persona_id, persona_name or "Maya")
    return exp.get("preview_url") or ""

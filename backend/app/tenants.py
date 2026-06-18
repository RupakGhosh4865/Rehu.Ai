"""
Multi-tenant accounts: signup, login, JWT, plan tracking, and tenant-scoped data.

Storage is JSON (keeps parity with existing stores). Each tenant gets a folder
under data/tenants/{tenant_id}/ for personas, knowledge, sessions, etc. The
legacy single-tenant data layout maps to the implicit tenant "default" so that
existing deployments continue to work without migration.
"""
import base64
import contextvars
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Header, HTTPException, Request, Depends, Cookie

from .config import settings
from . import db

_active_tenant_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "active_tenant_id", default="default"
)


def set_active_tenant(tenant_id: str) -> None:
    _active_tenant_id.set(tenant_id or DEFAULT_TENANT_ID)


def active_tenant_id() -> str:
    return _active_tenant_id.get() or DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "default"

TENANTS_FILE = Path(settings.CHROMA_PERSIST_DIR).parent / "tenants.json"
TENANTS_DIR = Path(settings.CHROMA_PERSIST_DIR).parent / "tenants"

# `minute_limit` is the monthly AVATAR-minute pool (the metered, costly unit).
# Text + voice are effectively unlimited (near-zero cost to serve). `public`
# plans are the two we sell self-serve; the rest are trial + legacy aliases kept
# so existing Stripe wiring and older tenant rows keep working.
PLAN_DEFAULTS = {
    "trial": {
        # Every signup gets a SMALL pool of REAL avatar minutes — enough to feel
        # the product, scarce enough to convert. When it runs out the experience
        # degrades to chat (never goes dark) until they buy a plan.
        "minute_limit": 5,
        "persona_limit": 1,
        "knowledge_mb_limit": 25,
        "production_avatar": True,
        "avatar_choice": False,   # trial is locked to the default avatar
        "label": "Trial",
        "price_inr": 0,
        "price_usd": 0,
        "public": False,
        "tagline": "5 free avatar minutes to try your Superhuman.",
    },
    "growth": {
        "minute_limit": 500,
        "persona_limit": 3,
        "knowledge_mb_limit": 500,
        "production_avatar": True,
        "avatar_choice": True,
        "label": "Growth",
        "price_inr": 24999,
        "price_usd": 299,
        "public": True,
        "tagline": "500 avatar minutes/mo — for teams getting started.",
    },
    "scale": {
        "minute_limit": 1000,
        "persona_limit": 10,
        "knowledge_mb_limit": 2000,
        "production_avatar": True,
        "avatar_choice": True,
        "label": "Scale",
        "price_inr": 44999,
        "price_usd": 529,
        "public": True,
        "tagline": "1,000 avatar minutes/mo — for growing demand.",
    },
    "enterprise": {
        "minute_limit": None,         # negotiated pool; no hard cap
        "persona_limit": None,
        "knowledge_mb_limit": None,
        "production_avatar": True,
        "avatar_choice": True,
        "label": "Enterprise",
        "price_inr": None,            # custom, from ₹75,000/mo
        "price_usd": None,
        "public": True,
        "tagline": "Custom minute pool, isolation, SSO & SLA.",
    },
    # ── Legacy aliases (not shown publicly; preserved for back-compat) ──────────
    "pilot":        {"minute_limit": 250,  "persona_limit": 1, "knowledge_mb_limit": 25,   "production_avatar": False, "avatar_choice": False, "label": "Pilot",        "public": False},
    "professional": {"minute_limit": 500,  "persona_limit": 3, "knowledge_mb_limit": 500,  "production_avatar": True,  "avatar_choice": True,  "label": "Professional", "public": False},
    "business":     {"minute_limit": 1000, "persona_limit": 10,"knowledge_mb_limit": 2000, "production_avatar": True,  "avatar_choice": True,  "label": "Business",     "public": False},
}


def public_plans() -> list[dict]:
    """The plans we sell self-serve, in display order (for the pricing page/API)."""
    order = ["growth", "scale", "enterprise"]
    out = []
    for key in order:
        p = PLAN_DEFAULTS.get(key)
        if p and p.get("public"):
            out.append({"id": key, **p})
    return out


# ─────────────────────────── Storage ─────────────────────────────────────────

def _ensure_dir() -> None:
    TENANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TENANTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_all() -> dict:
    """Tenant registry, now in the DB (one row per tenant)."""
    return db.accounts_load_all()


def _save_all(data: dict) -> None:
    db.accounts_save_all(data)


def list_tenants() -> list[dict]:
    tenants = _load_all()
    return [_redact(t) for t in tenants.values()]


def get_tenant(tenant_id: str) -> Optional[dict]:
    return _load_all().get(tenant_id)


def get_tenant_by_email(email: str) -> Optional[dict]:
    email = (email or "").strip().lower()
    for t in _load_all().values():
        if (t.get("email") or "").lower() == email:
            return t
    return None


def get_tenant_by_slug(slug: str) -> Optional[dict]:
    slug = (slug or "").strip().lower()
    for t in _load_all().values():
        if (t.get("slug") or "").lower() == slug:
            return t
    return None


def _redact(t: dict) -> dict:
    return {k: v for k, v in t.items() if k not in {"password_hash", "password_salt"}}


# ─────────────────────────── Passwords ───────────────────────────────────────

def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return base64.b64encode(h).decode("utf-8"), salt


def _verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = _hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


# ─────────────────────────── Slugs ───────────────────────────────────────────

def slugify(value: str) -> str:
    value = (value or "").lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "team"


def _unique_slug(base: str) -> str:
    base = slugify(base) or "team"
    if base == DEFAULT_TENANT_ID:
        base = f"{base}-1"
    candidate = base
    n = 1
    while get_tenant_by_slug(candidate):
        n += 1
        candidate = f"{base}-{n}"
    return candidate


# ─────────────────────────── CRUD ────────────────────────────────────────────

def create_tenant(
    email: str,
    password: str,
    company_name: str = "",
    plan: str = "trial",
    name: str = "",
) -> dict:
    if get_tenant_by_email(email):
        raise ValueError("Email is already registered")
    tenant_id = uuid.uuid4().hex
    pw_hash, salt = _hash_password(password)
    slug = _unique_slug(company_name or email.split("@")[0])
    record = {
        "tenant_id": tenant_id,
        "name": name.strip(),
        "email": email.strip().lower(),
        "company_name": company_name.strip() or slug.title(),
        "slug": slug,
        "password_hash": pw_hash,
        "password_salt": salt,
        "plan": plan if plan in PLAN_DEFAULTS else "trial",
        "stripe_customer_id": "",
        "stripe_subscription_id": "",
        "minutes_used": 0,
        "minutes_period_start": time.time(),
        "integrations": {},
        "created_at": time.time(),
        "is_admin": False,
        # Server-side onboarding flag (Prompt 1.2). localStorage is only a UX hint;
        # this is the source of truth that gates /onboarding vs /dashboard.
        "onboarding_completed": False,
        # Per-tenant feature flags, toggled by the global admin (Prompt 1.6).
        "feature_flags": {},
    }
    tenants = _load_all()
    tenants[tenant_id] = record
    _save_all(tenants)
    tenant_dir(tenant_id).mkdir(parents=True, exist_ok=True)
    return _redact(record)


def update_tenant(tenant_id: str, patch: dict) -> Optional[dict]:
    tenants = _load_all()
    if tenant_id not in tenants:
        return None
    tenant = tenants[tenant_id]
    safe_keys = {
        "name", "company_name", "slug", "plan", "stripe_customer_id", "stripe_subscription_id",
        "minutes_used", "minutes_period_start", "integrations", "is_admin",
        "last_login", "login_count", "onboarding_completed", "feature_flags",
    }
    for k, v in (patch or {}).items():
        if k in safe_keys:
            tenant[k] = v
    tenants[tenant_id] = tenant
    _save_all(tenants)
    return _redact(tenant)


def reset_password(tenant_id: str, new_password: str) -> bool:
    tenants = _load_all()
    if tenant_id not in tenants:
        return False
    pw_hash, salt = _hash_password(new_password)
    tenants[tenant_id]["password_hash"] = pw_hash
    tenants[tenant_id]["password_salt"] = salt
    _save_all(tenants)
    return True


def authenticate(email: str, password: str) -> Optional[dict]:
    t = get_tenant_by_email(email)
    if not t:
        return None
    if not _verify_password(password, t.get("password_hash", ""), t.get("password_salt", "")):
        return None
    return t


# ─────────────────────────── RBAC: roles & members ──────────────────────────
# Role hierarchy (higher number = more privilege). Legacy tokens (no role) are
# treated as 'owner' so existing single-user accounts keep full access.
ROLE_LEVELS = {"viewer": 1, "editor": 2, "admin": 3, "owner": 4}
ASSIGNABLE_ROLES = {"viewer", "editor", "admin"}  # owner is the account holder


def list_members(tenant_id: str) -> list[dict]:
    """All users in the org: the owner plus invited members (no secrets)."""
    t = _load_all().get(tenant_id)
    if not t:
        return []
    members = [{"email": t.get("email"), "role": "owner"}]
    for m in (t.get("members") or []):
        members.append({"email": m.get("email"), "role": m.get("role"),
                        "added_at": m.get("added_at")})
    return members


def add_member(tenant_id: str, email: str, password: str, role: str) -> dict:
    if role not in ASSIGNABLE_ROLES:
        raise ValueError(f"Role must be one of {sorted(ASSIGNABLE_ROLES)}")
    email = (email or "").strip().lower()
    if not email or not password:
        raise ValueError("Email and password are required")
    tenants = _load_all()
    t = tenants.get(tenant_id)
    if not t:
        raise ValueError("Organization not found")
    if email == (t.get("email") or "").lower():
        raise ValueError("That email is the account owner")
    members = t.get("members") or []
    if any((m.get("email") or "").lower() == email for m in members):
        raise ValueError("A member with that email already exists")
    pw_hash, salt = _hash_password(password)
    members.append({"email": email, "role": role, "password_hash": pw_hash,
                    "password_salt": salt, "added_at": time.time()})
    t["members"] = members
    tenants[tenant_id] = t
    _save_all(tenants)
    return {"email": email, "role": role}


def remove_member(tenant_id: str, email: str) -> bool:
    email = (email or "").strip().lower()
    tenants = _load_all()
    t = tenants.get(tenant_id)
    if not t:
        return False
    members = t.get("members") or []
    kept = [m for m in members if (m.get("email") or "").lower() != email]
    if len(kept) == len(members):
        return False
    t["members"] = kept
    tenants[tenant_id] = t
    _save_all(tenants)
    return True


def authenticate_user(email: str, password: str) -> Optional[dict]:
    """Authenticate an owner OR an invited member. Returns {tenant, role, email}."""
    email = (email or "").strip().lower()
    owner = get_tenant_by_email(email)
    if owner and _verify_password(password, owner.get("password_hash", ""), owner.get("password_salt", "")):
        return {"tenant": owner, "role": "owner", "email": email}
    for t in _load_all().values():
        for m in (t.get("members") or []):
            if (m.get("email") or "").lower() == email and _verify_password(
                password, m.get("password_hash", ""), m.get("password_salt", "")):
                return {"tenant": t, "role": m.get("role", "viewer"), "email": email}
    return None


# ─────────────────────────── JWT (HS256) ────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def issue_token(tenant_id: str, *, role: str = "owner", actor: Optional[str] = None,
                ttl_hours: Optional[int] = None) -> str:
    ttl = (ttl_hours or settings.JWT_TTL_HOURS) * 3600
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": tenant_id,
        "role": role if role in ROLE_LEVELS else "viewer",
        "actor": actor,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl,
    }
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(settings.JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"


def verify_token(token: str) -> Optional[dict]:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        return None
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(settings.JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        actual = _b64url_decode(sig_b64)
    except Exception:
        return None
    if not hmac.compare_digest(expected, actual):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        return None
    if payload.get("exp") and payload["exp"] < time.time():
        return None
    return payload


# ─────────────────────────── Password-reset tokens ──────────────────────────

def _sign(payload: dict) -> str:
    header_b64 = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(settings.JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url(sig)}"


def issue_reset_token(email: str, ttl_minutes: int = 60) -> str:
    now = int(time.time())
    return _sign({"email": (email or "").strip().lower(), "purpose": "pwreset",
                  "iat": now, "exp": now + ttl_minutes * 60})


def verify_reset_token(token: str) -> Optional[str]:
    payload = verify_token(token)
    if payload and payload.get("purpose") == "pwreset":
        return payload.get("email")
    return None


# ─────────────────────────── Tenant data directory ──────────────────────────

def tenant_dir(tenant_id: str) -> Path:
    base = Path(settings.CHROMA_PERSIST_DIR).parent
    if tenant_id == DEFAULT_TENANT_ID:
        return base
    return TENANTS_DIR / tenant_id


# ─────────────────────────── FastAPI helpers ─────────────────────────────────

def _extract_token_from_request(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    cookie = request.cookies.get("savant_token")
    if cookie:
        return cookie
    return None


def _tenant_from_subdomain(request: Request) -> Optional[dict]:
    root = (settings.APP_ROOT_DOMAIN or "").strip().lower()
    if not root:
        return None
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if not host or host == root or not host.endswith("." + root):
        return None
    sub = host[: -(len(root) + 1)]
    if sub in {"www", "app", "api", "admin"}:
        return None
    t = get_tenant_by_slug(sub)
    return t


def get_current_tenant_optional(request: Request) -> Optional[dict]:
    """Resolve tenant from JWT (preferred) or subdomain. May return None for legacy single-tenant."""
    token = _extract_token_from_request(request)
    if token:
        payload = verify_token(token)
        if payload and payload.get("sub"):
            tenant = get_tenant(payload["sub"])
            if tenant:
                return tenant
    sub_tenant = _tenant_from_subdomain(request)
    if sub_tenant:
        return sub_tenant
    return None


def require_tenant(request: Request) -> dict:
    tenant = get_current_tenant_optional(request)
    if not tenant:
        raise HTTPException(401, "Authentication required")
    return tenant


def current_tenant_id(request: Request) -> str:
    """Returns the active tenant id, or DEFAULT_TENANT_ID for legacy/admin use."""
    tenant = get_current_tenant_optional(request)
    if tenant:
        return tenant.get("tenant_id") or DEFAULT_TENANT_ID
    return DEFAULT_TENANT_ID


# ─────────────────────────── Plan helpers ───────────────────────────────────

def plan_limits(plan: str) -> dict:
    return PLAN_DEFAULTS.get(plan) or PLAN_DEFAULTS["trial"]


def add_minutes_used(tenant_id: str, minutes: float) -> dict:
    tenants = _load_all()
    if tenant_id not in tenants:
        return {}
    tenant = tenants[tenant_id]
    period_start = tenant.get("minutes_period_start", time.time())
    if time.time() - period_start > 30 * 86400:
        tenant["minutes_used"] = 0
        tenant["minutes_period_start"] = time.time()
    tenant["minutes_used"] = round((tenant.get("minutes_used") or 0) + minutes, 2)
    tenants[tenant_id] = tenant
    _save_all(tenants)
    return _redact(tenant)


def can_start_session(tenant: dict) -> tuple[bool, str]:
    limits = plan_limits(tenant.get("plan", "trial"))
    minute_limit = limits.get("minute_limit")
    if minute_limit is not None and (tenant.get("minutes_used") or 0) >= minute_limit:
        return False, f"Plan limit reached: {minute_limit} minutes/month used. Upgrade to continue."
    return True, ""

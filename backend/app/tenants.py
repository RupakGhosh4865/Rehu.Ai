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

PLAN_DEFAULTS = {
    "trial": {
        "minute_limit": 60,
        "persona_limit": 1,
        "knowledge_mb_limit": 5,
        "production_avatar": False,
        "label": "Trial",
    },
    "pilot": {
        "minute_limit": 250,
        "persona_limit": 1,
        "knowledge_mb_limit": 25,
        "production_avatar": False,
        "label": "Pilot",
    },
    "professional": {
        "minute_limit": 500,
        "persona_limit": 1,
        "knowledge_mb_limit": 200,
        "production_avatar": True,
        "label": "Professional",
    },
    "business": {
        "minute_limit": 2000,
        "persona_limit": 3,
        "knowledge_mb_limit": 1000,
        "production_avatar": True,
        "label": "Business",
    },
    "enterprise": {
        "minute_limit": None,
        "persona_limit": None,
        "knowledge_mb_limit": None,
        "production_avatar": True,
        "label": "Enterprise",
    },
}


# ─────────────────────────── Storage ─────────────────────────────────────────

def _ensure_dir() -> None:
    TENANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TENANTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_all() -> dict:
    _ensure_dir()
    if not TENANTS_FILE.exists():
        return {}
    try:
        with open(TENANTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("Could not load tenants: %s", e)
        return {}


def _save_all(data: dict) -> None:
    _ensure_dir()
    with open(TENANTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
) -> dict:
    if get_tenant_by_email(email):
        raise ValueError("Email is already registered")
    tenant_id = uuid.uuid4().hex
    pw_hash, salt = _hash_password(password)
    slug = _unique_slug(company_name or email.split("@")[0])
    record = {
        "tenant_id": tenant_id,
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
        "company_name", "slug", "plan", "stripe_customer_id", "stripe_subscription_id",
        "minutes_used", "minutes_period_start", "integrations", "is_admin",
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


# ─────────────────────────── JWT (HS256) ────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def issue_token(tenant_id: str, *, ttl_hours: Optional[int] = None) -> str:
    ttl = (ttl_hours or settings.JWT_TTL_HOURS) * 3600
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": tenant_id,
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

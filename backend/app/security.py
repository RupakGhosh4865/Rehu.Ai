"""
Savant.ai -- Enterprise security middleware

Code-level controls expected in an enterprise security review:
  • Security response headers (nosniff, referrer policy, HSTS, frame protection).
  • Rate limiting on public / expensive endpoints (auth, lead capture, sessions).
  • A startup check that refuses weak/default secrets in production.

Frame protection is applied selectively: the avatar call page, the widget SDK,
and static assets MUST stay cross-origin embeddable (that's the product), so
only sensitive surfaces (admin) get X-Frame-Options.
"""
import logging
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import settings

logger = logging.getLogger(__name__)

# Paths that must remain embeddable on customer sites — never frame-block these.
_EMBEDDABLE_PREFIXES = ("/call", "/sdk", "/static", "/api/")
_FRAME_PROTECT_PREFIXES = ("/admin",)

def _parse_limit(spec: str, default: tuple[int, int]) -> tuple[int, int]:
    """Parse a 'max/window' string (e.g. '40/300') into (max_requests, window_seconds)."""
    try:
        max_s, win_s = str(spec).split("/", 1)
        return int(max_s), int(win_s)
    except Exception:
        return default


def _rate_rules() -> list[tuple[str, int, int]]:
    """The SINGLE source of truth for rate-limit rules, built from config.
    (path prefix, max requests, window seconds). First match wins; longest/most
    specific prefixes are listed first."""
    return [
        ("/api/auth/",      *_parse_limit(settings.RATE_LIMIT_AUTH,      (20, 300))),
        ("/api/sessions",   *_parse_limit(settings.RATE_LIMIT_SESSIONS,  (40, 300))),
        ("/api/knowledge/", *_parse_limit(settings.RATE_LIMIT_KNOWLEDGE, (60, 300))),
        ("/api/leads",      *_parse_limit(settings.RATE_LIMIT_LEADS,     (30,  60))),
        # Catch-all for any other mutating API call. Keep last (broadest prefix).
        ("/api/",           *_parse_limit(settings.RATE_LIMIT_DEFAULT,  (120, 60))),
    ]

_WEAK_SECRETS = {"", "change-me", "change-me-in-production", "change-me-jwt-secret", "change-me-secret"}

# A secret is "strong enough" if it isn't a known default and is reasonably long.
_MIN_SECRET_LEN = 24


def _is_strong_secret(value: str) -> bool:
    v = (value or "").strip()
    return v.lower() not in _WEAK_SECRETS and len(v) >= _MIN_SECRET_LEN


# Service keys the product cannot run without in production, with a known-default /
# placeholder sentinel that means "not really configured".
_REQUIRED_SERVICE_KEYS = {
    "OPENAI_API_KEY":     ("sk-...", "sk-proj-..."),
    "LIVEAVATAR_API_KEY": ("", "...",),
}


def _required_service_key_problems() -> list[str]:
    """Flag required service keys that are missing or still a placeholder default."""
    out: list[str] = []
    for name, placeholders in _REQUIRED_SERVICE_KEYS.items():
        val = (getattr(settings, name, "") or "").strip()
        if not val or val in placeholders:
            out.append(f"{name} is unset or a placeholder (required for production)")
    return out


# ── Security headers ──────────────────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("X-XSS-Protection", "0")  # modern browsers; rely on CSP instead
        if settings.FORCE_HTTPS:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        path = request.url.path
        if path.startswith(_FRAME_PROTECT_PREFIXES):
            h["X-Frame-Options"] = "DENY"
            h["Permissions-Policy"] = "geolocation=(), payment=(), usb=()"
            h["Cache-Control"] = h.get("Cache-Control", "no-store")
        return response


# ── Rate limiting (single limiter; Redis-backed with in-memory fallback) ──────
#
# Backends share one interface: allow(key, limit, window) -> bool. The in-memory
# sliding window is correct for a single process; Redis makes the limit hold
# across multiple workers/replicas (we're scaling horizontally). The backend is
# chosen once at import time from REDIS_URL.

class _InMemorySlidingWindow:
    """Per-key sliding window. Single-process only — state is not shared."""
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, limit: int, window: int) -> bool:
        now = time.monotonic()
        dq = self._hits[key]
        cutoff = now - window
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        return True


class _RedisFixedWindow:
    """Atomic INCR+EXPIRE fixed-window counter shared across workers via Redis.
    Fails OPEN (allows the request) if Redis is unreachable, so a Redis outage
    degrades availability of the limiter, not of the product."""
    def __init__(self, client):
        self._r = client

    def allow(self, key: str, limit: int, window: int) -> bool:
        try:
            bucket = int(time.time() // window)
            redis_key = f"rl:{key}:{bucket}"
            count = self._r.incr(redis_key)
            if count == 1:
                self._r.expire(redis_key, window)
            return count <= limit
        except Exception as e:  # Redis down -> don't block traffic
            logger.warning("Rate limiter Redis error (failing open): %s", e)
            return True


def _build_limiter():
    url = (settings.REDIS_URL or "").strip()
    if url:
        try:
            import redis  # optional dependency
            client = redis.Redis.from_url(url, socket_connect_timeout=2, decode_responses=True)
            client.ping()
            logger.info("Rate limiter backend: Redis (%s)", url.split("@")[-1])
            return _RedisFixedWindow(client)
        except Exception as e:
            logger.warning("REDIS_URL set but Redis unavailable (%s) — using in-memory limiter", e)
    logger.info("Rate limiter backend: in-memory (single process)")
    return _InMemorySlidingWindow()


_limiter = _build_limiter()


def _client_ip(request) -> str:
    # Honour the first hop of X-Forwarded-For when behind a proxy (Railway).
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return (request.client.host if request.client else "unknown")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if settings.RATE_LIMIT_ENABLED and request.method in ("POST", "PUT", "DELETE", "PATCH"):
            path = request.url.path
            for prefix, limit, window in _rate_rules():
                if path.startswith(prefix):
                    key = f"{_client_ip(request)}:{prefix}"
                    if not _limiter.allow(key, limit, window):
                        logger.warning("Rate limit hit: %s on %s", _client_ip(request), path)
                        return JSONResponse(
                            status_code=429,
                            content={"detail": "Too many requests. Please slow down and try again shortly."},
                            headers={"Retry-After": str(window)},
                        )
                    break
        return await call_next(request)


# ── Startup secret-strength check ─────────────────────────────────────────────

def is_production() -> bool:
    return settings.ENVIRONMENT.strip().lower() == "production"


def check_secrets() -> list[str]:
    """The single source of truth for boot-time security enforcement.

    Returns a list of security problems. Always logs CRITICAL for each problem;
    in production it additionally RAISES so the app refuses to boot.

    Dev (ENVIRONMENT != production): problems are logged but the app still boots,
    so local development with default secrets keeps working.
    """
    problems: list[str] = []
    prod = is_production()

    # ── Secrets that must be strong everywhere we enforce ──────────────────────
    if not _is_strong_secret(settings.SECRET_KEY):
        problems.append("SECRET_KEY is unset, a default, or too short (need >= 24 chars)")
    if not _is_strong_secret(settings.JWT_SECRET):
        problems.append("JWT_SECRET is unset, a default, or too short (need >= 24 chars)")
    if not settings.ADMIN_PASSWORD:
        problems.append("ADMIN_PASSWORD is empty (admin panel is unauthenticated)")
    elif prod and len(settings.ADMIN_PASSWORD.strip()) < 12:
        problems.append("ADMIN_PASSWORD is too weak for production (need >= 12 chars)")

    # CORS wildcard is logged everywhere, but only fatal in production.
    if list(settings.CORS_ORIGINS) == ["*"]:
        problems.append("CORS_ORIGINS is a wildcard '*' (set explicit origins for production)")

    # ── Production-only requirements ───────────────────────────────────────────
    if prod:
        if not settings.STRIPE_WEBHOOK_SECRET:
            problems.append("STRIPE_WEBHOOK_SECRET is unset (billing webhooks cannot be trusted)")
        if not settings.FORCE_HTTPS:
            problems.append("FORCE_HTTPS must be true in production (HSTS / TLS enforcement)")
        db_url = (settings.DATABASE_URL or "").strip().lower()
        if not db_url or db_url.startswith("sqlite"):
            problems.append("DATABASE_URL must point at Postgres in production (not SQLite)")
        # Required service keys: the product cannot function without these, and a
        # placeholder/default value means a misconfigured deploy. Refuse to boot.
        problems.extend(_required_service_key_problems())

    if problems:
        for p in problems:
            logger.critical("SECURITY: %s", p)
        if prod:
            raise RuntimeError(
                "Refusing to start in production with insecure config: " + "; ".join(problems)
            )
    else:
        logger.info("Security config check passed.")
    return problems

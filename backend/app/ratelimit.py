"""
Lightweight in-memory rate limiter for public, cost-bearing endpoints.

Single-worker in-process design (matches the rest of the app's state model).
Each public endpoint group has a fixed-window limit per client IP. This is the
first line of defense against cost-amplification abuse (every session creation
spends OpenAI + LiveAvatar credits) and signup/lead spam. For multi-worker or
multi-replica deployments this should move to Redis.
"""
import time
from typing import Optional

# route group -> (max_requests, window_seconds)
LIMITS: dict[str, tuple[int, int]] = {
    "session": (30, 60),   # POST /api/sessions
    "train":   (10, 60),   # POST /api/studio/train (also triggers SSRF-guarded fetch)
    "lead":    (20, 60),   # POST /api/leads
    "auth":    (15, 60),   # signup / login (brute-force + account spam)
}

# (client_ip, group) -> (window_reset_epoch, count)
_buckets: dict[tuple[str, str], tuple[float, int]] = {}
_MAX_BUCKETS = 20_000


def route_group(path: str, method: str) -> Optional[str]:
    if method != "POST":
        return None
    if path == "/api/sessions":
        return "session"
    if path == "/api/studio/train":
        return "train"
    if path == "/api/leads":
        return "lead"
    if path in ("/api/auth/signup", "/api/auth/login"):
        return "auth"
    return None


def client_ip(request) -> str:
    # Railway / proxies put the real client first in X-Forwarded-For.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune(now: float) -> None:
    if len(_buckets) <= _MAX_BUCKETS:
        return
    for key, (reset_at, _) in list(_buckets.items()):
        if now >= reset_at:
            _buckets.pop(key, None)


def check(request) -> bool:
    """Return True if the request is allowed, False if it should be rejected (429)."""
    group = route_group(request.url.path, request.method)
    if group is None:
        return True
    max_requests, window = LIMITS[group]
    now = time.time()
    key = (client_ip(request), group)
    reset_at, count = _buckets.get(key, (0.0, 0))
    if now >= reset_at:
        _prune(now)
        _buckets[key] = (now + window, 1)
        return True
    count += 1
    _buckets[key] = (reset_at, count)
    return count <= max_requests

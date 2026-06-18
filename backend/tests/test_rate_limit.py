"""Single rate limiter (Prompt 0.3).

After collapsing the two limiters into security.RateLimitMiddleware, these tests
pin: (a) the limiter backend behaviour, (b) config-driven rules, and (c) an
end-to-end 429 when an endpoint is hit past its limit.
"""
import pytest

from app import security
from app.config import settings


# ── Backend unit tests ─────────────────────────────────────────────────────────

def test_in_memory_window_blocks_past_limit():
    w = security._InMemorySlidingWindow()
    key = "1.2.3.4:/api/leads"
    assert all(w.allow(key, limit=3, window=60) for _ in range(3))  # first 3 ok
    assert w.allow(key, limit=3, window=60) is False                # 4th blocked


def test_in_memory_window_is_per_key():
    w = security._InMemorySlidingWindow()
    assert w.allow("a:/x", 1, 60) is True
    assert w.allow("a:/x", 1, 60) is False   # same key blocked
    assert w.allow("b:/x", 1, 60) is True    # different key independent


def test_redis_backend_fails_open_when_unreachable():
    class _Broken:
        def incr(self, *a, **k):
            raise ConnectionError("redis down")
    limiter = security._RedisFixedWindow(_Broken())
    # A Redis outage must not block product traffic.
    assert limiter.allow("k", 1, 60) is True


# ── Config-driven rules ──────────────────────────────────────────────────────────

def test_parse_limit():
    assert security._parse_limit("40/300", (1, 1)) == (40, 300)
    assert security._parse_limit("garbage", (9, 9)) == (9, 9)


def test_rate_rules_read_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH", "5/120")
    rules = dict((p, (m, w)) for p, m, w in security._rate_rules())
    assert rules["/api/auth/"] == (5, 120)
    # catch-all default is present and last-matching
    assert "/api/" in rules


def test_only_one_limiter_module_exists():
    # ratelimit.py was deleted; importing it must fail.
    with pytest.raises(ModuleNotFoundError):
        import app.ratelimit  # noqa: F401


# ── End-to-end 429 through the middleware ────────────────────────────────────────

def test_endpoint_returns_429_past_limit(client, monkeypatch):
    # Force a tiny limit on /api/leads and a fresh in-memory limiter so the test
    # is independent of any limiter state accumulated by other tests.
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)   # the suite default is off
    monkeypatch.setattr(settings, "RATE_LIMIT_LEADS", "2/60")
    monkeypatch.setattr(security, "_limiter", security._InMemorySlidingWindow())

    body = {"email": "rl@test.example", "name": "RL", "source": "test"}
    codes = [client.post("/api/leads", json=body).status_code for _ in range(4)]
    # First 2 allowed (any non-429), then 429s.
    assert codes[2] == 429 and codes[3] == 429
    assert 429 not in codes[:2]

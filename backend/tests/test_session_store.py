"""Durable session state (Prompt 1.1).

Pins the SessionStore contract for both backends, plus the multi-worker
properties the durable store exists to provide:
  - create / get / update / remove round-trip
  - touch refreshes the idle-kill TTL; expiry removes the session
  - cross-worker visibility: a session created on "worker A" is seen on "worker B"
  - crash recovery: metering is billed exactly once (idempotent flag), and the
    sweeper can detect orphaned sessions
  - tenant isolation: owned_by_tenant rejects cross-tenant access
"""
import time

import pytest

from app import session_store


# ── Backend fixtures ──────────────────────────────────────────────────────────

def _redis_store():
    import fakeredis
    return session_store.RedisSessionStore(fakeredis.FakeStrictRedis())


def _mem_store():
    return session_store.InMemorySessionStore()


@pytest.fixture(params=["memory", "redis"])
def st(request):
    return _mem_store() if request.param == "memory" else _redis_store()


def _session(tenant="t_alpha", **extra):
    base = {"tenant_id": tenant, "persona_id": "default", "transcript": [],
            "started_at": "2026-01-01T00:00:00+00:00", "last_activity_at": time.time()}
    base.update(extra)
    return base


# ── Contract: both backends behave identically ────────────────────────────────

def test_create_get_roundtrip(st):
    st.create("s1", _session(persona_id="hr"))
    got = st.get("s1")
    assert got and got["persona_id"] == "hr" and got["tenant_id"] == "t_alpha"


def test_get_missing_returns_none(st):
    assert st.get("nope") is None


def test_update_merges_fields(st):
    st.create("s1", _session())
    st.update("s1", visitor_email="x@y.z", lead_score=80)
    got = st.get("s1")
    assert got["visitor_email"] == "x@y.z" and got["lead_score"] == 80


def test_remove_deletes(st):
    st.create("s1", _session())
    assert st.remove("s1") is not None
    assert st.get("s1") is None


def test_count_and_list_active(st):
    st.create("s1", _session())
    st.create("s2", _session())
    assert st.count() == 2
    ids = {sid for sid, _ in st.list_active()}
    assert ids == {"s1", "s2"}


# ── Tenant isolation (the sacred rule) ─────────────────────────────────────────

def test_owned_by_tenant_allows_owner(st):
    st.create("s1", _session(tenant="t_alpha"))
    assert st.owned_by_tenant("s1", "t_alpha") is not None


def test_owned_by_tenant_blocks_other_tenant(st):
    st.create("s1", _session(tenant="t_alpha"))
    assert st.owned_by_tenant("s1", "t_beta") is None       # cross-tenant -> None
    assert st.owned_by_tenant("missing", "t_alpha") is None  # missing -> None (same 404)


def test_list_for_tenant_filters(st):
    st.create("a", _session(tenant="t_alpha"))
    st.create("b", _session(tenant="t_beta"))
    alpha = {sid for sid, _ in st.list_for_tenant("t_alpha")}
    assert alpha == {"a"}


# ── Metering idempotency (bill exactly once across clean + crash paths) ────────

def test_mark_metered_only_wins_once(st):
    st.create("s1", _session())
    assert st.mark_metered("s1") is True    # first caller bills
    assert st.mark_metered("s1") is False   # second caller must NOT double-bill


# ── Redis-specific: TTL idle-kill, cross-worker, orphan sweep ──────────────────

def test_touch_refreshes_ttl():
    import fakeredis
    from app.config import settings
    r = fakeredis.FakeStrictRedis()
    st = session_store.RedisSessionStore(r)
    st.create("s1", _session())
    key = session_store._KEY_PREFIX + "s1"
    ttl_after_create = r.ttl(key)
    assert ttl_after_create > 0
    st.touch("s1")
    assert r.ttl(key) > 0   # still has a positive TTL after touch


def test_expiry_removes_session():
    import fakeredis
    r = fakeredis.FakeStrictRedis()
    st = session_store.RedisSessionStore(r)
    st.create("s1", _session())
    # Simulate the idle TTL elapsing: drop the state key (what Redis does on expiry).
    r.delete(session_store._KEY_PREFIX + "s1")
    assert st.get("s1") is None


def test_cross_worker_visibility():
    # Two store instances backed by the SAME redis = two workers sharing state.
    import fakeredis
    shared = fakeredis.FakeStrictRedis()
    worker_a = session_store.RedisSessionStore(shared)
    worker_b = session_store.RedisSessionStore(shared)
    worker_a.create("s1", _session(persona_id="sales"))
    seen = worker_b.get("s1")
    assert seen and seen["persona_id"] == "sales"   # B sees A's session


def test_orphan_detection_and_idempotent_recovery():
    # A session whose state key expired but whose id lingers in the active set =
    # a crashed-worker orphan. The sweeper must detect it and bill exactly once.
    import fakeredis
    r = fakeredis.FakeStrictRedis()
    st = session_store.RedisSessionStore(r)
    st.create("s1", _session())
    r.delete(session_store._KEY_PREFIX + "s1")   # state gone, id still in active set
    assert "s1" in st.orphans()
    assert st.mark_metered("s1") is True          # recovery bills once
    assert st.mark_metered("s1") is False         # never twice
    st.forget_orphan("s1")
    assert "s1" not in st.orphans()


def test_sweep_lock_is_single_leader():
    import fakeredis
    shared = fakeredis.FakeStrictRedis()
    a = session_store.RedisSessionStore(shared)
    b = session_store.RedisSessionStore(shared)
    assert a.try_acquire_sweep_lock(30) is True    # A becomes leader
    assert b.try_acquire_sweep_lock(30) is False   # B blocked this round


def test_build_store_falls_back_to_memory(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "REDIS_URL", "")
    assert isinstance(session_store.build_store(), session_store.InMemorySessionStore)

"""
Savant.ai -- Durable live-session state (Prompt 1.1)

Live session state used to live in a process-local dict in main.py. That capped
us at one worker and lost every active call on restart. This module moves the
SERIALIZABLE part of session state into a durable store so we can run >1 worker
and survive restarts, while keeping the current lifecycle (keepalive, idle-kill,
metering) intact.

State is split by serializability:
  • Durable (this module): transcript, tokens, timestamps, tenant_id, flags —
    plain JSON. Backed by Redis in prod, in-memory dict in dev (no REDIS_URL).
  • Process-local (main.py `_local_runtime`): the asyncio keepalive Task and the
    orchestrator ConversationState object — neither is serializable. The worker
    that created the avatar session owns its keepalive Task (it holds the
    la_session_token); conv_state is lazily rebuilt from the durable transcript
    on whichever worker handles /respond.

Idle-kill is TTL-driven: every touch() refreshes the key TTL to idle_timeout +
margin. When the key expires the session is gone — no cross-worker timer needed.
A single leader-elected sweeper reclaims orphans (crashed-worker sessions): it
bills metering exactly once (idempotent flag) and releases the paid LiveAvatar
stream.

TENANT ISOLATION: owned_by_tenant() is the single gate the API uses; a session is
visible to a tenant only if its stored tenant_id matches the active tenant. Missing
and cross-tenant both return None so callers raise an identical 404.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "session:"
_ACTIVE_SET = "sessions:active"          # set of live session ids (for sweeper orphan detection)
_METERED_PREFIX = "metered:"            # idempotency flag: this session's minutes were billed
_SWEEP_LOCK = "sessions:sweep:lock"      # leader election for the single sweeper


# ── In-memory backend (dev / single worker) ──────────────────────────────────

class InMemorySessionStore:
    """Process-local store. Identical behavior to the original _sessions dict.
    Idle-kill is enforced by the keepalive loop (as today), not by TTL."""

    def __init__(self):
        self._data: dict[str, dict] = {}
        self._metered: set[str] = set()

    def create(self, session_id: str, data: dict) -> None:
        self._data[session_id] = data

    def get(self, session_id: str) -> Optional[dict]:
        return self._data.get(session_id)

    def update(self, session_id: str, **fields) -> None:
        s = self._data.get(session_id)
        if s is not None:
            s.update(fields)

    def touch(self, session_id: str) -> None:
        s = self._data.get(session_id)
        if s is not None:
            s["last_activity_at"] = time.time()

    def list_active(self) -> list[tuple[str, dict]]:
        return list(self._data.items())

    def list_for_tenant(self, tenant_id: str) -> list[tuple[str, dict]]:
        from . import tenants
        return [(sid, d) for sid, d in self._data.items()
                if (d.get("tenant_id") or tenants.DEFAULT_TENANT_ID) == tenant_id]

    def remove(self, session_id: str) -> Optional[dict]:
        self._metered.discard(session_id)
        return self._data.pop(session_id, None)

    def count(self) -> int:
        return len(self._data)

    def owned_by_tenant(self, session_id: str, tenant_id: str) -> Optional[dict]:
        from . import tenants
        s = self._data.get(session_id)
        if not s:
            return None
        owner = s.get("tenant_id") or tenants.DEFAULT_TENANT_ID
        return s if owner == tenant_id else None

    # Metering idempotency (mirrors the Redis flag so both backends behave alike).
    def mark_metered(self, session_id: str) -> bool:
        """Return True if this call set the flag (i.e. NOT yet metered)."""
        if session_id in self._metered:
            return False
        self._metered.add(session_id)
        return True

    def is_redis(self) -> bool:
        return False


# ── Redis backend (prod / multi-worker) ──────────────────────────────────────

class RedisSessionStore:
    """Durable JSON session state with TTL-driven idle expiry. Shared across
    workers, survives restarts. Values are JSON; non-serializable runtime state
    (keepalive Task, conv_state) stays process-local in main.py."""

    def __init__(self, client):
        self._r = client

    def _ttl(self) -> int:
        return int(settings.AVATAR_IDLE_TIMEOUT_SECONDS) + int(settings.SESSION_TTL_MARGIN_SECONDS)

    def create(self, session_id: str, data: dict) -> None:
        self._r.set(_KEY_PREFIX + session_id, json.dumps(data, default=str), ex=self._ttl())
        self._r.sadd(_ACTIVE_SET, session_id)

    def get(self, session_id: str) -> Optional[dict]:
        raw = self._r.get(_KEY_PREFIX + session_id)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def update(self, session_id: str, **fields) -> None:
        s = self.get(session_id)
        if s is None:
            return
        s.update(fields)
        # Preserve remaining TTL so update() doesn't accidentally extend idle life.
        ttl = self._r.ttl(_KEY_PREFIX + session_id)
        ttl = ttl if (ttl and ttl > 0) else self._ttl()
        self._r.set(_KEY_PREFIX + session_id, json.dumps(s, default=str), ex=ttl)

    def touch(self, session_id: str) -> None:
        s = self.get(session_id)
        if s is None:
            return
        s["last_activity_at"] = time.time()
        # Activity resets the idle-kill clock by refreshing the full TTL.
        self._r.set(_KEY_PREFIX + session_id, json.dumps(s, default=str), ex=self._ttl())

    def list_active(self) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        for sid in self._r.smembers(_ACTIVE_SET):
            sid = sid.decode() if isinstance(sid, bytes) else sid
            s = self.get(sid)
            if s is not None:
                out.append((sid, s))
        return out

    def list_for_tenant(self, tenant_id: str) -> list[tuple[str, dict]]:
        from . import tenants
        return [(sid, d) for sid, d in self.list_active()
                if (d.get("tenant_id") or tenants.DEFAULT_TENANT_ID) == tenant_id]

    def remove(self, session_id: str) -> Optional[dict]:
        s = self.get(session_id)
        self._r.delete(_KEY_PREFIX + session_id)
        self._r.srem(_ACTIVE_SET, session_id)
        self._r.delete(_METERED_PREFIX + session_id)
        return s

    def count(self) -> int:
        return self._r.scard(_ACTIVE_SET)

    def owned_by_tenant(self, session_id: str, tenant_id: str) -> Optional[dict]:
        from . import tenants
        s = self.get(session_id)
        if not s:
            return None
        owner = s.get("tenant_id") or tenants.DEFAULT_TENANT_ID
        return s if owner == tenant_id else None

    def mark_metered(self, session_id: str) -> bool:
        """Atomically set the metered flag. Returns True iff WE set it (so the
        caller bills exactly once across workers and across clean/crash paths)."""
        # SET NX is atomic: only one caller wins. Flag outlives the session key.
        won = self._r.set(_METERED_PREFIX + session_id, "1", nx=True, ex=self._ttl() * 4)
        return bool(won)

    def is_redis(self) -> bool:
        return True

    # Sweeper support -----------------------------------------------------------

    def orphans(self) -> list[str]:
        """Session ids in the active set whose state key has expired (TTL hit
        without a clean remove() — i.e. the owning worker likely died)."""
        out: list[str] = []
        for sid in self._r.smembers(_ACTIVE_SET):
            sid = sid.decode() if isinstance(sid, bytes) else sid
            if not self._r.exists(_KEY_PREFIX + sid):
                out.append(sid)
        return out

    def forget_orphan(self, session_id: str) -> None:
        self._r.srem(_ACTIVE_SET, session_id)

    def try_acquire_sweep_lock(self, ttl_seconds: int) -> bool:
        """Leader election: only the worker holding this lock runs the sweep."""
        return bool(self._r.set(_SWEEP_LOCK, "1", nx=True, ex=ttl_seconds))


# ── Factory ───────────────────────────────────────────────────────────────────

def build_store():
    url = (settings.REDIS_URL or "").strip()
    if url:
        try:
            import redis
            client = redis.Redis.from_url(url, socket_connect_timeout=2)
            client.ping()
            logger.info("Session store: Redis (%s)", url.split("@")[-1])
            return RedisSessionStore(client)
        except Exception as e:
            logger.warning("REDIS_URL set but Redis unavailable (%s) — using in-memory session store", e)
    logger.info("Session store: in-memory (single worker)")
    return InMemorySessionStore()

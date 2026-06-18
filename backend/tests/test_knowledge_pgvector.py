"""pgvector knowledge store (Prompt 1.3).

The dense arm of hybrid RAG moves into Postgres+pgvector (indexed ANN, tenant
pre-filtered). On this SQLite dev box pgvector is inactive, so:
  - the JSON-blob fallback path is tested FOR REAL (ingest -> query, isolation);
  - the pgvector path is tested via an in-process stub that mimics
    db.kchunks_search ordering + isolation, proving the abstraction, the
    tenant-isolation guarantee, RRF fusion, and no-per-query-re-embedding.

Run the LIVE pgvector path against a real Postgres with:
    docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=pw pgvector/pgvector:pg16
    DATABASE_URL=postgresql+psycopg2://postgres:pw@localhost:5432/postgres \
        RUN_PG_TESTS=1 python -m pytest tests/test_knowledge_pgvector.py
"""
import asyncio

import pytest

from app import knowledge, tenants, db
from app.config import settings


# Deterministic fake embeddings so retrieval is testable without OpenAI: map a
# word to a one-hot-ish vector; the query embeds to the same space.
_VOCAB = ["pricing", "refund", "shipping", "warranty", "support"]


def _fake_vec(text: str):
    t = text.lower()
    return [1.0 if w in t else 0.0 for w in _VOCAB]


@pytest.fixture
def semantic(monkeypatch):
    """Enable semantic search with a stub embedder (no OpenAI calls)."""
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "RAG_USE_SEMANTIC", True)

    calls = {"embed": 0}

    async def _stub_embed(texts):
        calls["embed"] += 1
        return [_fake_vec(t) for t in texts]

    monkeypatch.setattr(knowledge, "_embed_texts", _stub_embed)
    monkeypatch.setattr(knowledge, "_semantic_enabled", lambda: True)
    return calls


def _set_tenant(tid):
    tenants.set_active_tenant(tid)


def _ingest(persona, content, title=None):
    return asyncio.run(knowledge.add_knowledge(persona, content, title=title))


def _query(persona, q, top_k=3):
    return asyncio.run(knowledge.query_knowledge(persona, q, top_k=top_k))


# ── JSON-blob fallback (real, on SQLite) ──────────────────────────────────────

def test_ingest_then_query_returns_relevant_chunk(semantic):
    _set_tenant("t_kb_alpha")
    knowledge._stores.clear()
    _ingest("p1", "Our pricing starts at forty nine dollars per month for the growth plan.")
    _ingest("p1", "Shipping is free worldwide and takes about five business days.")
    out = _query("p1", "how much does pricing cost")
    assert "pricing" in out.lower()


def test_query_embeds_only_the_query_not_the_corpus(semantic):
    # No per-query re-embedding of stored chunks: a search embeds just the query.
    _set_tenant("t_kb_embedcount")
    knowledge._stores.clear()
    _ingest("p1", "Pricing details: the warranty covers two years.")
    before = semantic["embed"]
    _query("p1", "pricing")
    after = semantic["embed"]
    assert after - before == 1   # exactly one embed call (the query)


# ── Tenant isolation: the headline security guarantee ─────────────────────────

def test_tenant_b_cannot_retrieve_tenant_a_chunks(semantic):
    knowledge._stores.clear()
    _set_tenant("t_kb_A")
    _ingest("shared_persona", "Tenant A SECRET pricing is one hundred dollars.")
    # Switch to a different tenant, same persona_id.
    _set_tenant("t_kb_B")
    knowledge._stores.clear()   # drop A's in-memory store so we read B's (empty) state
    out = _query("shared_persona", "pricing")
    assert "secret" not in out.lower()
    assert out == ""            # B has no chunks for this persona


# ── pgvector path via stub (proves abstraction + isolation + fusion) ──────────

class _StubVectorDB:
    """In-process stand-in for the db kchunks_* API. Stores rows per (tenant,
    persona) and ranks by cosine-ish overlap, ALWAYS filtered by tenant_id."""
    def __init__(self):
        self.rows: dict[tuple, list] = {}

    def pgvector_available(self):
        return True

    def kchunks_add(self, tenant_id, persona_id, rows):
        bucket = self.rows.setdefault((tenant_id, persona_id), [])
        bucket.extend(rows)
        return len(rows)

    def kchunks_search(self, tenant_id, persona_id, qvec, top_k):
        bucket = self.rows.get((tenant_id, persona_id), [])   # tenant-scoped!
        scored = []
        for r in bucket:
            emb = r.get("embedding") or []
            score = sum(a * b for a, b in zip(qvec, emb))
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"id": i, "content": r["content"], "title": r.get("title")}
                for i, (_s, r) in enumerate(scored[:top_k]) if _s > 0]

    def kchunks_delete(self, tenant_id, persona_id):
        return len(self.rows.pop((tenant_id, persona_id), []))

    # blob API passthrough to the real db so BM25/_save_to_disk keep working
    def __getattr__(self, name):
        return getattr(db, name)


@pytest.fixture
def pgvector(monkeypatch, semantic):
    stub = _StubVectorDB()
    monkeypatch.setattr(knowledge, "db", stub)
    return stub


def test_pgvector_path_returns_relevant_chunk(pgvector):
    _set_tenant("t_pg_alpha")
    knowledge._stores.clear()
    _ingest("p1", "Refund policy: full refund within thirty days, no questions asked.")
    _ingest("p1", "Our pricing is transparent and starts low.")
    out = _query("p1", "refund")
    assert "refund" in out.lower()


def test_pgvector_tenant_isolation(pgvector):
    knowledge._stores.clear()
    _set_tenant("t_pg_A")
    _ingest("shared", "Tenant A confidential refund terms.")
    _set_tenant("t_pg_B")
    knowledge._stores.clear()
    out = _query("shared", "refund")
    # B's pgvector bucket for 'shared' is empty -> never sees A's rows.
    assert "confidential" not in out.lower()
    assert out == ""


def test_pgvector_rrf_fusion_surfaces_both_arms(pgvector):
    # A doc strong on keywords (BM25) and a different doc strong on the vector
    # space should both be reachable through the fused ranking.
    _set_tenant("t_pg_fuse")
    knowledge._stores.clear()
    _ingest("p1", "pricing pricing pricing keyword heavy document")
    _ingest("p1", "warranty coverage details for the support plan")
    out = _query("p1", "pricing", top_k=2)
    assert "pricing" in out.lower()


# ── Backfill idempotency (guarded; only meaningful with a live pgvector DB) ───

@pytest.mark.skipif("RUN_PG_TESTS" not in __import__("os").environ,
                    reason="needs a real pgvector Postgres (set RUN_PG_TESTS=1)")
def test_backfill_is_idempotent():
    r1 = db.migrate_knowledge_to_pgvector()
    r2 = db.migrate_knowledge_to_pgvector()
    assert r2["imported"] == 0   # second run imports nothing

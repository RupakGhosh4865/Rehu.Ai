"""
Savant.ai -- Database foundation (tenant-isolated)

SQLAlchemy (sync) layer that backs the move off flat JSON files. Every business
table carries a non-null `tenant_id`, and the data-access layer ALWAYS filters by
the active tenant — so cross-tenant reads are impossible by construction (the
core enterprise data-isolation requirement).

Dev:  SQLite file at ./data/savant.db (no driver needed — Python stdlib).
Prod: set DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/savant
"""
import json
import logging
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

logger = logging.getLogger(__name__)


def _resolve_url() -> str:
    url = (settings.DATABASE_URL or "").strip()
    if url:
        # Railway/Heroku hand out 'postgres://'; SQLAlchemy 2.0 requires 'postgresql://'.
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url
    base = Path(settings.CHROMA_PERSIST_DIR).parent
    base.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(base / 'savant.db').as_posix()}"


DATABASE_URL = _resolve_url()
_is_sqlite = DATABASE_URL.startswith("sqlite")
_is_postgres = DATABASE_URL.startswith("postgresql")
engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
Base = declarative_base()

# Embedding dimension for OpenAI text-embedding-3-small (the configured model).
EMBED_DIM = 1536

# pgvector availability is resolved at init_db() time (CREATE EXTENSION probe).
# Until then assume False; knowledge.py reads pgvector_available() to pick its
# dense-search backend (indexed ANN vs the JSON-blob fallback).
_pgvector_ready = False


def pgvector_available() -> bool:
    """True only when we are on Postgres AND the `vector` extension is installed.
    Drives the knowledge dense-arm backend choice; False everywhere on SQLite."""
    return _pgvector_ready


class SessionRecord(Base):
    """A completed conversation/lead record. Full fidelity kept in `payload`;
    key fields are mirrored into columns for querying and summaries."""
    __tablename__ = "session_records"

    id           = Column(Integer, primary_key=True)
    tenant_id    = Column(String(64),  nullable=False, index=True)   # isolation key
    session_id   = Column(String(64),  nullable=False, index=True)
    persona_id   = Column(String(128))
    persona_name = Column(String(256))
    company_name = Column(String(256))
    visitor_name = Column(String(256))
    visitor_email = Column(String(256), index=True)
    language     = Column(String(16))
    started_at   = Column(String(40), index=True)
    ended_at     = Column(String(40))
    payload      = Column(Text, nullable=False)                      # full JSON record

    __table_args__ = (
        UniqueConstraint("tenant_id", "session_id", name="uq_tenant_session"),
        Index("ix_tenant_started", "tenant_id", "started_at"),
    )


class TenantDocument(Base):
    """One JSON document per (tenant, collection) — backs the per-tenant config
    stores (personas, product_cards, meetings, ride_along). Tenant-isolated."""
    __tablename__ = "tenant_documents"
    id         = Column(Integer, primary_key=True)
    tenant_id  = Column(String(64), nullable=False, index=True)
    collection = Column(String(64), nullable=False, index=True)
    payload    = Column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "collection", name="uq_tenant_collection"),)


class TenantAccount(Base):
    """The tenant registry (accounts/auth). One row per tenant."""
    __tablename__ = "tenant_accounts"
    tenant_id = Column(String(64), primary_key=True)
    email     = Column(String(256), index=True)
    payload   = Column(Text, nullable=False)


# ── Knowledge chunks (Prompt 1.3: pgvector dense arm) ─────────────────────────
# The `embedding` column is a pgvector `vector(EMBED_DIM)` when the adapter is
# present; otherwise a Text placeholder so the table still creates on SQLite
# (where the JSON-blob knowledge backend is used instead and never touches it).
try:
    from pgvector.sqlalchemy import Vector as _Vector
    _EmbeddingColumn = lambda: Column(_Vector(EMBED_DIM))   # noqa: E731
    _HAS_PGVECTOR_ADAPTER = True
except Exception:
    _EmbeddingColumn = lambda: Column(Text)                 # noqa: E731
    _HAS_PGVECTOR_ADAPTER = False


class KnowledgeChunk(Base):
    """One retrievable chunk for hybrid RAG. ISOLATION: every query MUST filter
    by tenant_id (and persona_id). The pgvector ANN search is always pre-filtered
    by tenant_id, so a neighbor from another tenant is impossible by construction."""
    __tablename__ = "knowledge_chunks"
    id              = Column(Integer, primary_key=True)
    tenant_id       = Column(String(64),  nullable=False, index=True)   # isolation key
    persona_id      = Column(String(128), nullable=False, index=True)
    content         = Column(Text, nullable=False)
    title           = Column(String(256))
    embedding       = _EmbeddingColumn()
    embedding_model = Column(String(64))
    created_at      = Column(String(40))
    __table_args__ = (Index("ix_kchunk_tenant_persona", "tenant_id", "persona_id"),)


def init_db() -> None:
    global _pgvector_ready
    # On Postgres, try to enable pgvector BEFORE create_all so the vector column /
    # index can be created. If the extension can't be enabled, we degrade to the
    # JSON-blob knowledge backend (pgvector_available() stays False).
    if _is_postgres and _HAS_PGVECTOR_ADAPTER:
        try:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            _pgvector_ready = True
        except Exception as e:
            logger.warning("pgvector unavailable (%s) — knowledge falls back to JSON-blob backend", e)
            _pgvector_ready = False

    Base.metadata.create_all(engine)

    # ivfflat ANN index for cosine distance (only meaningful with pgvector).
    if _pgvector_ready:
        try:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_kchunk_embedding "
                    "ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops) "
                    "WITH (lists = 100)"
                ))
        except Exception as e:
            logger.warning("Could not create pgvector ANN index: %s", e)

    logger.info("Database ready: %s (pgvector=%s)",
                "sqlite" if _is_sqlite else DATABASE_URL.split("@")[-1], _pgvector_ready)


def session_stats_by_tenant() -> dict:
    """One grouped query: tenant_id -> {sessions, last_session_at}. Powers the
    admin all-customers overview without N queries."""
    from sqlalchemy import select, func
    with db_session() as s:
        rows = s.execute(
            select(
                SessionRecord.tenant_id,
                func.count(SessionRecord.id),
                func.max(SessionRecord.started_at),
            ).group_by(SessionRecord.tenant_id)
        ).all()
    return {tid: {"sessions": count, "last_session_at": last}
            for tid, count, last in rows}


# ── Per-tenant JSON blob store (personas / product_cards / meetings / ride_along)

def blob_get(tenant_id: str, collection: str, default=None):
    from sqlalchemy import select
    with db_session() as s:
        row = s.execute(
            select(TenantDocument).where(
                TenantDocument.tenant_id == tenant_id,
                TenantDocument.collection == collection,
            )
        ).scalar_one_or_none()
        if not row:
            return default
        try:
            return json.loads(row.payload)
        except Exception:
            return default


def blob_put(tenant_id: str, collection: str, value) -> None:
    from sqlalchemy import select
    data = json.dumps(value, ensure_ascii=False)
    with db_session() as s:
        row = s.execute(
            select(TenantDocument).where(
                TenantDocument.tenant_id == tenant_id,
                TenantDocument.collection == collection,
            )
        ).scalar_one_or_none()
        if row:
            row.payload = data
        else:
            s.add(TenantDocument(tenant_id=tenant_id, collection=collection, payload=data))


def blob_delete(tenant_id: str, collection: str) -> bool:
    from sqlalchemy import delete as sa_delete
    with db_session() as s:
        res = s.execute(
            sa_delete(TenantDocument).where(
                TenantDocument.tenant_id == tenant_id,
                TenantDocument.collection == collection,
            )
        )
        return (res.rowcount or 0) > 0


# ── Tenant accounts registry ──────────────────────────────────────────────────

def accounts_load_all() -> dict:
    from sqlalchemy import select
    with db_session() as s:
        rows = s.execute(select(TenantAccount)).scalars().all()
        out = {}
        for r in rows:
            try:
                out[r.tenant_id] = json.loads(r.payload)
            except Exception:
                continue
        return out


def accounts_save_all(data: dict) -> None:
    """Upsert every account in `data`; delete accounts no longer present
    (preserves the file-based _save_all(whole dict) semantics)."""
    from sqlalchemy import select
    with db_session() as s:
        existing = {r.tenant_id: r for r in s.execute(select(TenantAccount)).scalars().all()}
        for tid, rec in (data or {}).items():
            payload = json.dumps(rec, ensure_ascii=False)
            email = (rec.get("email") or "")
            if tid in existing:
                existing[tid].payload = payload
                existing[tid].email = email
            else:
                s.add(TenantAccount(tenant_id=tid, email=email, payload=payload))
        for tid, row in existing.items():
            if tid not in (data or {}):
                s.delete(row)


@contextmanager
def db_session():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# ── One-time migration: import existing per-tenant JSON session files ─────────

def migrate_json_sessions() -> dict:
    """Idempotently load legacy data/**/sessions/*.json into the DB. Safe to run
    repeatedly (upserts by tenant_id + session_id)."""
    from sqlalchemy import select
    base = Path(settings.CHROMA_PERSIST_DIR).parent  # .../data
    # (tenant_id, sessions_dir) for the default tenant and each sub-tenant.
    dirs: list[tuple[str, Path]] = []
    default_sessions = base / "sessions"
    if default_sessions.is_dir():
        dirs.append(("default", default_sessions))
    tenants_root = base / "tenants"
    if tenants_root.is_dir():
        for tdir in tenants_root.iterdir():
            sdir = tdir / "sessions"
            if sdir.is_dir():
                dirs.append((tdir.name, sdir))

    imported, skipped = 0, 0
    with db_session() as s:
        for tid, sdir in dirs:
            for path in sdir.glob("*.json"):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    sid = record.get("session_id") or path.stem
                    exists = s.execute(
                        select(SessionRecord.id).where(
                            SessionRecord.tenant_id == tid, SessionRecord.session_id == sid)
                    ).first()
                    if exists:
                        skipped += 1
                        continue
                    s.add(_row_from_record(tid, sid, record))
                    imported += 1
                except Exception as e:
                    logger.warning("Migrate skip %s: %s", path.name, e)
    logger.info("JSON->DB migration: %d imported, %d already present", imported, skipped)
    return {"imported": imported, "skipped": skipped, "tenants": len(dirs)}


def _row_from_record(tenant_id: str, session_id: str, record: dict) -> "SessionRecord":
    return SessionRecord(
        tenant_id=tenant_id,
        session_id=session_id,
        persona_id=record.get("persona_id"),
        persona_name=record.get("persona_name"),
        company_name=record.get("company_name"),
        visitor_name=record.get("visitor_name"),
        visitor_email=record.get("visitor_email"),
        language=record.get("language", "en"),
        started_at=record.get("started_at"),
        ended_at=record.get("ended_at"),
        payload=json.dumps(record, ensure_ascii=False),
    )


_BLOB_FILES = {
    "personas":      "personas.json",
    "product_cards": "product_cards.json",
    "meetings":      "meetings.json",
    "ride_along":    "ride_along.json",
}


def _tenant_dirs() -> list:
    """[(tenant_id, dir)] for the default tenant (data/) and each sub-tenant."""
    base = Path(settings.CHROMA_PERSIST_DIR).parent
    dirs = [("default", base)]
    troot = base / "tenants"
    if troot.is_dir():
        for d in troot.iterdir():
            if d.is_dir():
                dirs.append((d.name, d))
    return dirs


def migrate_json_blobs() -> dict:
    """Idempotently import per-tenant config JSON (personas/cards/meetings/ride_along)."""
    from sqlalchemy import select
    imported = 0
    with db_session() as s:
        for tid, tdir in _tenant_dirs():
            for collection, fname in _BLOB_FILES.items():
                path = tdir / fname
                if not path.exists():
                    continue
                present = s.execute(
                    select(TenantDocument.id).where(
                        TenantDocument.tenant_id == tid,
                        TenantDocument.collection == collection)
                ).first()
                if present:
                    continue
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning("Blob migrate skip %s/%s: %s", tid, fname, e)
                    continue
                s.add(TenantDocument(tenant_id=tid, collection=collection,
                                     payload=json.dumps(value, ensure_ascii=False)))
                imported += 1
    return {"imported": imported}


def migrate_json_accounts() -> dict:
    """Idempotently import the tenant registry (tenants.json)."""
    from sqlalchemy import select
    tfile = Path(settings.CHROMA_PERSIST_DIR).parent / "tenants.json"
    if not tfile.exists():
        return {"imported": 0}
    try:
        data = json.loads(tfile.read_text(encoding="utf-8"))
    except Exception:
        return {"imported": 0}
    if not isinstance(data, dict):
        return {"imported": 0}
    imported = 0
    with db_session() as s:
        existing = set(s.execute(select(TenantAccount.tenant_id)).scalars().all())
        for tid, rec in data.items():
            if tid in existing or not isinstance(rec, dict):
                continue
            s.add(TenantAccount(tenant_id=tid, email=(rec.get("email") or ""),
                                payload=json.dumps(rec, ensure_ascii=False)))
            imported += 1
    return {"imported": imported}


def migrate_json_knowledge() -> dict:
    """Idempotently import per-(tenant, persona) knowledge JSON (chunks + embeddings)."""
    from sqlalchemy import select
    imported = 0
    for tid, tdir in _tenant_dirs():
        chromadir = tdir / "chromadb"
        if not chromadir.is_dir():
            continue
        with db_session() as s:
            for path in chromadir.glob("*.json"):
                collection = f"knowledge:{path.stem}"
                present = s.execute(
                    select(TenantDocument.id).where(
                        TenantDocument.tenant_id == tid,
                        TenantDocument.collection == collection)
                ).first()
                if present:
                    continue
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                s.add(TenantDocument(tenant_id=tid, collection=collection,
                                     payload=json.dumps(value, ensure_ascii=False)))
                imported += 1
    return {"imported": imported}


# ── Knowledge chunk store (pgvector dense arm) ────────────────────────────────
# Every function filters by tenant_id + persona_id. There is no code path that
# reads chunks without both filters — that is the isolation guarantee.

def kchunks_add(tenant_id: str, persona_id: str, rows: list[dict]) -> int:
    """Insert chunks. Each row: {content, title, embedding(list[float]|None), embedding_model}."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with db_session() as s:
        for r in rows:
            s.add(KnowledgeChunk(
                tenant_id=tenant_id, persona_id=persona_id,
                content=r["content"], title=r.get("title"),
                embedding=r.get("embedding"),
                embedding_model=r.get("embedding_model"),
                created_at=now,
            ))
    return len(rows)


def kchunks_for_persona(tenant_id: str, persona_id: str) -> list[dict]:
    """All chunks for (tenant, persona). Used to (re)build the BM25 sparse arm."""
    from sqlalchemy import select
    with db_session() as s:
        rows = s.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.tenant_id == tenant_id,
                KnowledgeChunk.persona_id == persona_id,
            ).order_by(KnowledgeChunk.id)
        ).scalars().all()
        return [{"id": r.id, "content": r.content, "title": r.title} for r in rows]


def kchunks_search(tenant_id: str, persona_id: str, query_embedding: list, top_k: int) -> list[dict]:
    """Dense ANN search, ALWAYS pre-filtered by tenant_id + persona_id, ordered by
    cosine distance. Returns [{id, content, title}] best-first. pgvector only."""
    from sqlalchemy import select
    with db_session() as s:
        stmt = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.tenant_id == tenant_id,
                   KnowledgeChunk.persona_id == persona_id,
                   KnowledgeChunk.embedding.isnot(None))
            .order_by(KnowledgeChunk.embedding.cosine_distance(query_embedding))
            .limit(top_k)
        )
        rows = s.execute(stmt).scalars().all()
        return [{"id": r.id, "content": r.content, "title": r.title} for r in rows]


def kchunks_delete(tenant_id: str, persona_id: str) -> int:
    from sqlalchemy import delete as sa_delete
    with db_session() as s:
        res = s.execute(sa_delete(KnowledgeChunk).where(
            KnowledgeChunk.tenant_id == tenant_id,
            KnowledgeChunk.persona_id == persona_id,
        ))
        return res.rowcount or 0


def kchunks_count(tenant_id: str, persona_id: str) -> int:
    from sqlalchemy import select, func
    with db_session() as s:
        return s.execute(select(func.count(KnowledgeChunk.id)).where(
            KnowledgeChunk.tenant_id == tenant_id,
            KnowledgeChunk.persona_id == persona_id,
        )).scalar_one()


def migrate_knowledge_to_pgvector() -> dict:
    """One-time, idempotent: copy existing JSON-blob knowledge (chunks+embeddings)
    into knowledge_chunks. No-op unless pgvector is active. Skips a (tenant,persona)
    that already has rows so it is safe to run on every boot."""
    if not pgvector_available():
        return {"imported": 0, "skipped": "pgvector inactive"}
    from sqlalchemy import select
    imported = 0
    with db_session() as s:
        blobs = s.execute(
            select(TenantDocument).where(TenantDocument.collection.like("knowledge:%"))
        ).scalars().all()
    for blob in blobs:
        persona_id = blob.collection.split("knowledge:", 1)[1]
        if kchunks_count(blob.tenant_id, persona_id) > 0:
            continue  # already migrated
        try:
            data = json.loads(blob.payload)
        except Exception:
            continue
        chunks = data.get("chunks") or []
        titles = data.get("titles") or []
        embeddings = data.get("embeddings") or []
        model = data.get("embedding_model") or ""
        rows = []
        for i, content in enumerate(chunks):
            rows.append({
                "content": content,
                "title": titles[i] if i < len(titles) else None,
                "embedding": embeddings[i] if i < len(embeddings) else None,
                "embedding_model": model or None,
            })
        if rows:
            kchunks_add(blob.tenant_id, persona_id, rows)
            imported += len(rows)
    return {"imported": imported}


def migrate_all_json() -> dict:
    return {
        "sessions": migrate_json_sessions(),
        "blobs": migrate_json_blobs(),
        "accounts": migrate_json_accounts(),
        "knowledge": migrate_json_knowledge(),
        "pgvector": migrate_knowledge_to_pgvector(),
    }

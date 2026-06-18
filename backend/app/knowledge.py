"""
Savant.ai -- Knowledge Base (Hybrid RAG)

Retrieval fuses two signals:
  • BM25     — keyword / lexical match (pure Python, always available)
  • Semantic — embedding cosine similarity via OpenAI embeddings

When an OpenAI key is present and RAG_USE_SEMANTIC is on, both signals are
combined with Reciprocal Rank Fusion (RRF) for robust ranking. With no key it
transparently degrades to BM25-only — so the platform keeps working offline and
older keyword-only knowledge files load without re-indexing.

No GPU, no heavy ML libraries. Works on Python 3.14+.
"""
import ipaddress
import json
import logging
import math
import re
import socket
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from rank_bm25 import BM25Okapi

from .config import settings
from . import tenants, db

logger = logging.getLogger(__name__)

# Cap on bytes pulled from a remote page during knowledge ingestion.
_URL_FETCH_MAX_BYTES = 5 * 1024 * 1024
_URL_FETCH_MAX_REDIRECTS = 5

# -- In-memory store per (tenant, persona) ------------------------------------
# store = {chunks, titles, bm25, embeddings, embedding_model}
_stores: dict[tuple[str, str], dict] = {}

# Batch size for embedding calls (OpenAI accepts large batches; keep modest).
_EMBED_BATCH = 100


def _semantic_enabled() -> bool:
    return bool(settings.RAG_USE_SEMANTIC and settings.OPENAI_API_KEY)


def _persist_dir() -> Path:
    base = tenants.tenant_dir(tenants.active_tenant_id())
    if tenants.active_tenant_id() == tenants.DEFAULT_TENANT_ID:
        return Path(settings.CHROMA_PERSIST_DIR)
    return base / "chromadb"


def _key(persona_id: str) -> tuple[str, str]:
    return tenants.active_tenant_id(), persona_id


def _tokenize(text: str) -> List[str]:
    return re.findall(r"\b\w+\b", text.lower())


# -- Embeddings ---------------------------------------------------------------

async def _embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a list of texts via OpenAI. Returns [] on failure (caller falls back)."""
    if not texts or not _semantic_enabled():
        return []
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    vectors: List[List[float]] = []
    try:
        for i in range(0, len(texts), _EMBED_BATCH):
            batch = texts[i: i + _EMBED_BATCH]
            resp = await client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=batch,
            )
            vectors.extend([d.embedding for d in resp.data])
        return vectors
    except Exception as e:
        logger.warning("Embedding generation failed (%s) — falling back to BM25.", e)
        return []


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _rrf_fuse(*ranked_lists: List[int], k: int) -> List[int]:
    """Reciprocal Rank Fusion. Each input is a list of chunk indices, best first.
    Returns fused chunk indices, best first."""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, idx in enumerate(ranked):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return [idx for idx, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]


# -- Persistence (tenant-isolated DB blob) ------------------------------------

def _kn_collection(persona_id: str) -> str:
    return f"knowledge:{persona_id}"


def _save_to_disk(persona_id: str):
    store = _stores.get(_key(persona_id), {})
    payload = {
        "chunks": store.get("chunks", []),
        "titles": store.get("titles", []),
    }
    # Persist embeddings only when present, tagged with their model so a model
    # change triggers a clean re-index on next load.
    if store.get("embeddings"):
        payload["embeddings"] = store["embeddings"]
        payload["embedding_model"] = store.get("embedding_model", settings.OPENAI_EMBEDDING_MODEL)
    db.blob_put(tenants.active_tenant_id(), _kn_collection(persona_id), payload)


def _load_from_disk(persona_id: str):
    data = db.blob_get(tenants.active_tenant_id(), _kn_collection(persona_id))
    if not data:
        return
    try:
        chunks = data.get("chunks", [])
        titles = data.get("titles", [])
        if not chunks:
            return
        embeddings = data.get("embeddings") or []
        embedding_model = data.get("embedding_model", "")
        # Discard stale embeddings (model changed or count drifted from chunks).
        if embedding_model != settings.OPENAI_EMBEDDING_MODEL or len(embeddings) != len(chunks):
            embeddings = []
            embedding_model = ""
        _stores[_key(persona_id)] = {
            "chunks": chunks,
            "titles": titles,
            "bm25": BM25Okapi([_tokenize(c) for c in chunks]),
            "embeddings": embeddings,
            "embedding_model": embedding_model,
        }
    except Exception as e:
        logger.warning(f"Could not load knowledge for {persona_id}: {e}")


def _ensure_loaded(persona_id: str):
    if _key(persona_id) not in _stores:
        _load_from_disk(persona_id)


async def _ensure_embeddings(persona_id: str):
    """Lazily backfill embeddings for chunks that don't have them yet (e.g. data
    indexed before semantic search was enabled). Safe no-op when disabled."""
    if not _semantic_enabled():
        return
    store = _stores.get(_key(persona_id))
    if not store or not store.get("chunks"):
        return
    if store.get("embeddings") and len(store["embeddings"]) == len(store["chunks"]):
        return
    vectors = await _embed_texts(store["chunks"])
    if vectors and len(vectors) == len(store["chunks"]):
        store["embeddings"] = vectors
        store["embedding_model"] = settings.OPENAI_EMBEDDING_MODEL
        _save_to_disk(persona_id)
        logger.info("Backfilled %d embeddings for persona '%s'", len(vectors), persona_id)


def chunk_text(text: str, chunk_size: int = 200, overlap: int = 30) -> List[str]:
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i: i + chunk_size])
        if len(chunk.strip()) > 30:
            chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


# -- Core operations ----------------------------------------------------------

async def add_knowledge(
    persona_id: str,
    content: str,
    title: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> int:
    _ensure_loaded(persona_id)
    chunks = chunk_text(content)
    if not chunks:
        return 0

    store = _stores.setdefault(
        _key(persona_id),
        {"chunks": [], "titles": [], "bm25": None, "embeddings": [], "embedding_model": ""},
    )
    store["chunks"].extend(chunks)
    store["titles"].extend([title or "Untitled"] * len(chunks))
    store["bm25"] = BM25Okapi([_tokenize(c) for c in store["chunks"]])

    # Embed the new chunks and append — but only if we already have (or can have)
    # a complete embedding set, so we never end up with a partial vector list.
    new_vectors: List[List[float]] = []
    if _semantic_enabled():
        existing = store.get("embeddings") or []
        if len(existing) == len(store["chunks"]) - len(chunks):
            new_vectors = await _embed_texts(chunks)
            if new_vectors and len(new_vectors) == len(chunks):
                store["embeddings"] = existing + new_vectors
                store["embedding_model"] = settings.OPENAI_EMBEDDING_MODEL
            else:
                new_vectors = []
                store["embeddings"] = []      # force lazy full re-embed on query
                store["embedding_model"] = ""
        else:
            store["embeddings"] = []
            store["embedding_model"] = ""

    _save_to_disk(persona_id)

    # Dense arm: when pgvector is active, ALSO write the new chunks (with their
    # embeddings, computed here at INGEST — never per query) into the indexed
    # knowledge_chunks table, tenant-scoped. The JSON blob above still backs BM25
    # and the no-pgvector fallback.
    if db.pgvector_available():
        rows = [{
            "content": chunks[i],
            "title": title or "Untitled",
            "embedding": new_vectors[i] if i < len(new_vectors) else None,
            "embedding_model": settings.OPENAI_EMBEDDING_MODEL if new_vectors else None,
        } for i in range(len(chunks))]
        try:
            db.kchunks_add(tenants.active_tenant_id(), persona_id, rows)
        except Exception:
            logger.exception("pgvector kchunks_add failed for persona '%s'", persona_id)

    logger.info(f"Added {len(chunks)} chunks for persona '{persona_id}'")
    return len(chunks)


def _assert_public_url(url: str) -> None:
    """Reject anything that is not a public http(s) URL.

    Blocks SSRF: an attacker-supplied URL (e.g. via /api/studio/train) must not be
    able to make the server reach loopback, link-local (cloud metadata), private,
    or otherwise reserved address space. We resolve DNS and check every record so
    a public hostname cannot smuggle in a private A/AAAA answer.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http(s) URLs are allowed")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f"Could not resolve host: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise ValueError("URL resolves to a non-public address and was blocked")


async def _safe_get(url: str):
    """GET a URL with SSRF guards: validate each hop, no auto-redirects, size cap."""
    import httpx

    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        for _ in range(_URL_FETCH_MAX_REDIRECTS + 1):
            _assert_public_url(url)
            resp = await client.get(url, headers={"User-Agent": "SavantBot/1.0"})
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    break
                url = str(httpx.URL(url).join(location))
                continue
            resp.raise_for_status()
            if len(resp.content) > _URL_FETCH_MAX_BYTES:
                raise ValueError("Remote page is too large to ingest")
            return resp
        raise ValueError("Too many redirects")


async def add_knowledge_from_url(persona_id: str, url: str, title: Optional[str] = None) -> int:
    from bs4 import BeautifulSoup

    resp = await _safe_get(url)

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ", strip=True).split())
    return await add_knowledge(persona_id, text, title=title or url)


async def query_knowledge(persona_id: str, query: str, top_k: int = None) -> str:
    top_k = top_k or settings.RAG_TOP_K
    _ensure_loaded(persona_id)
    store = _stores.get(_key(persona_id))
    if not store or not store.get("bm25") or not store["chunks"]:
        return ""

    n = len(store["chunks"])
    # Pull a wider candidate pool from each signal before fusing/truncating.
    pool = min(n, max(top_k * 4, top_k))

    # 1) BM25 keyword ranking (always available)
    bm25_scores = store["bm25"].get_scores(_tokenize(query))
    bm25_ranked = [i for i, s in sorted(enumerate(bm25_scores), key=lambda x: x[1], reverse=True)
                   if s > 0][:pool]

    ordered_idx: List[int]

    # 2) Semantic ranking + fusion (when enabled). The dense arm runs in pgvector
    #    (indexed ANN, tenant-pre-filtered) when available, else the in-process
    #    cosine fallback. Either way we fuse with BM25 via RRF on chunk index.
    if _semantic_enabled():
        q_vec = (await _embed_texts([query]))[:1]   # embed the QUERY once (not the corpus)
        sem_ranked: List[int] = []
        if q_vec:
            if db.pgvector_available():
                # Indexed ANN in Postgres, ALWAYS filtered by tenant_id + persona_id.
                hits = db.kchunks_search(tenants.active_tenant_id(), persona_id, q_vec[0], pool)
                # Map DB hits back to in-memory chunk indices (same chunk set) so
                # RRF fuses cleanly with BM25. Fall back to appending unseen content.
                content_to_idx = {c: i for i, c in enumerate(store["chunks"])}
                for h in hits:
                    idx = content_to_idx.get(h["content"])
                    if idx is not None:
                        sem_ranked.append(idx)
            else:
                await _ensure_embeddings(persona_id)
                embeddings = store.get("embeddings") or []
                if embeddings and len(embeddings) == n:
                    qv = q_vec[0]
                    sims = [(i, _cosine(qv, embeddings[i])) for i in range(n)]
                    sem_ranked = [i for i, s in sorted(sims, key=lambda x: x[1], reverse=True)
                                  if s > 0][:pool]
        if sem_ranked:
            fused = _rrf_fuse(bm25_ranked, sem_ranked, k=settings.RAG_RRF_K)
            ordered_idx = fused[:top_k]
        else:
            ordered_idx = bm25_ranked[:top_k]
    else:
        ordered_idx = bm25_ranked[:top_k]

    if not ordered_idx:
        return ""

    parts = []
    for i in ordered_idx:
        title = store["titles"][i]
        prefix = f"[{title}] " if title and title != "Untitled" else ""
        parts.append(f"{prefix}{store['chunks'][i]}")

    return "\n\n".join(parts)[: settings.RAG_MAX_CONTEXT_CHARS]


async def delete_persona_knowledge(persona_id: str) -> bool:
    _stores.pop(_key(persona_id), None)
    db.blob_delete(tenants.active_tenant_id(), _kn_collection(persona_id))
    if db.pgvector_available():
        try:
            db.kchunks_delete(tenants.active_tenant_id(), persona_id)
        except Exception:
            logger.exception("pgvector kchunks_delete failed for persona '%s'", persona_id)
    return True


async def get_knowledge_stats(persona_id: str) -> dict:
    _ensure_loaded(persona_id)
    store = _stores.get(_key(persona_id))
    count = len(store["chunks"]) if store else 0
    embedded = bool(store and store.get("embeddings") and len(store["embeddings"]) == count)
    return {
        "persona_id": persona_id,
        "chunk_count": count,
        "status": "active" if count > 0 else "empty",
        "retrieval": "hybrid" if (_semantic_enabled() and embedded) else "keyword",
    }

"""
Savant.ai -- Knowledge Base (RAG)
Pure Python BM25 implementation -- no GPU, no heavy ML libraries.
Works on Python 3.14+.
"""
import ipaddress
import json
import logging
import re
import socket
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from rank_bm25 import BM25Okapi

from .config import settings
from . import tenants

logger = logging.getLogger(__name__)

# Cap on bytes pulled from a remote page during knowledge ingestion.
_URL_FETCH_MAX_BYTES = 5 * 1024 * 1024
_URL_FETCH_MAX_REDIRECTS = 5

# -- In-memory store per (tenant, persona) ------------------------------------
_stores: dict[tuple[str, str], dict] = {}


def _persist_dir() -> Path:
    base = tenants.tenant_dir(tenants.active_tenant_id())
    if tenants.active_tenant_id() == tenants.DEFAULT_TENANT_ID:
        return Path(settings.CHROMA_PERSIST_DIR)
    return base / "chromadb"


def _key(persona_id: str) -> tuple[str, str]:
    return tenants.active_tenant_id(), persona_id


def _tokenize(text: str) -> List[str]:
    return re.findall(r"\b\w+\b", text.lower())


def _save_to_disk(persona_id: str):
    persist_dir = _persist_dir()
    persist_dir.mkdir(parents=True, exist_ok=True)
    store = _stores.get(_key(persona_id), {})
    path = persist_dir / f"{persona_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"chunks": store.get("chunks", []), "titles": store.get("titles", [])}, f)


def _load_from_disk(persona_id: str):
    path = _persist_dir() / f"{persona_id}.json"
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        chunks = data.get("chunks", [])
        titles = data.get("titles", [])
        if chunks:
            tokenized = [_tokenize(c) for c in chunks]
            _stores[_key(persona_id)] = {
                "chunks": chunks,
                "titles": titles,
                "bm25": BM25Okapi(tokenized),
            }
    except Exception as e:
        logger.warning(f"Could not load knowledge for {persona_id}: {e}")


def _ensure_loaded(persona_id: str):
    if _key(persona_id) not in _stores:
        _load_from_disk(persona_id)


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

    store = _stores.setdefault(_key(persona_id), {"chunks": [], "titles": [], "bm25": None})
    store["chunks"].extend(chunks)
    store["titles"].extend([title or "Untitled"] * len(chunks))

    tokenized = [_tokenize(c) for c in store["chunks"]]
    store["bm25"] = BM25Okapi(tokenized)

    _save_to_disk(persona_id)
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

    tokens = _tokenize(query)
    scores = store["bm25"].get_scores(tokens)

    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    top = [(store["chunks"][i], store["titles"][i], s) for i, s in ranked[:top_k] if s > 0]

    if not top:
        return ""

    parts = []
    for chunk, title, score in top:
        prefix = f"[{title}] " if title and title != "Untitled" else ""
        parts.append(f"{prefix}{chunk}")

    return "\n\n".join(parts)[: settings.RAG_MAX_CONTEXT_CHARS]


async def delete_persona_knowledge(persona_id: str) -> bool:
    _stores.pop(_key(persona_id), None)
    path = _persist_dir() / f"{persona_id}.json"
    if path.exists():
        path.unlink()
    return True


async def get_knowledge_stats(persona_id: str) -> dict:
    _ensure_loaded(persona_id)
    store = _stores.get(_key(persona_id))
    count = len(store["chunks"]) if store else 0
    return {"persona_id": persona_id, "chunk_count": count, "status": "active" if count > 0 else "empty"}

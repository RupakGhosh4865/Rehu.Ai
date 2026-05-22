"""
SuperHuman AI Persona Platform -- Knowledge Base (RAG)
Pure Python BM25 implementation -- no GPU, no heavy ML libraries.
Works on Python 3.14+.
"""
import json
import logging
import re
from pathlib import Path
from typing import List, Optional

from rank_bm25 import BM25Okapi

from .config import settings

logger = logging.getLogger(__name__)

# -- In-memory store per persona ----------------------------------------------
# { persona_id: { "chunks": [...], "titles": [...], "bm25": BM25Okapi } }
_stores: dict[str, dict] = {}

PERSIST_DIR = Path(settings.CHROMA_PERSIST_DIR)


def _tokenize(text: str) -> List[str]:
    return re.findall(r"\b\w+\b", text.lower())


def _save_to_disk(persona_id: str):
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    store = _stores.get(persona_id, {})
    path = PERSIST_DIR / f"{persona_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"chunks": store.get("chunks", []), "titles": store.get("titles", [])}, f)


def _load_from_disk(persona_id: str):
    path = PERSIST_DIR / f"{persona_id}.json"
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        chunks = data.get("chunks", [])
        titles = data.get("titles", [])
        if chunks:
            tokenized = [_tokenize(c) for c in chunks]
            _stores[persona_id] = {
                "chunks": chunks,
                "titles": titles,
                "bm25": BM25Okapi(tokenized),
            }
    except Exception as e:
        logger.warning(f"Could not load knowledge for {persona_id}: {e}")


def _ensure_loaded(persona_id: str):
    if persona_id not in _stores:
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

    store = _stores.setdefault(persona_id, {"chunks": [], "titles": [], "bm25": None})
    store["chunks"].extend(chunks)
    store["titles"].extend([title or "Untitled"] * len(chunks))

    tokenized = [_tokenize(c) for c in store["chunks"]]
    store["bm25"] = BM25Okapi(tokenized)

    _save_to_disk(persona_id)
    logger.info(f"Added {len(chunks)} chunks for persona '{persona_id}'")
    return len(chunks)


async def add_knowledge_from_url(persona_id: str, url: str, title: Optional[str] = None) -> int:
    import httpx
    from bs4 import BeautifulSoup

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ", strip=True).split())
    return await add_knowledge(persona_id, text, title=title or url)


async def query_knowledge(persona_id: str, query: str, top_k: int = None) -> str:
    top_k = top_k or settings.RAG_TOP_K
    _ensure_loaded(persona_id)
    store = _stores.get(persona_id)
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
    _stores.pop(persona_id, None)
    path = PERSIST_DIR / f"{persona_id}.json"
    if path.exists():
        path.unlink()
    return True


async def get_knowledge_stats(persona_id: str) -> dict:
    _ensure_loaded(persona_id)
    store = _stores.get(persona_id)
    count = len(store["chunks"]) if store else 0
    return {"persona_id": persona_id, "chunk_count": count, "status": "active" if count > 0 else "empty"}

"""RAG tool for Nexus — Chroma-backed long-term memory.

Uses sentence-transformers (all-MiniLM-L6-v2) for embeddings and persists the
collection at ~/AI_Agent/chroma/.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Iterable

import chromadb
from chromadb.utils import embedding_functions
from langchain_core.tools import tool

COLLECTION_NAME = "nexus-memory"
EMBED_MODEL = "all-MiniLM-L6-v2"
PERSIST_DIR = Path.home() / "AI_Agent" / "chroma"

_embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name=EMBED_MODEL
)
_client: chromadb.api.ClientAPI | None = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(PERSIST_DIR))
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=_embed_fn,
        )
    return _collection


def add_documents(
    documents: Iterable[str],
    metadatas: list[dict] | None = None,
    ids: list[str] | None = None,
) -> list[str]:
    """Add one or more documents to the nexus-memory collection.
    Returns the ids assigned to each document."""
    col = _get_collection()
    docs = list(documents)
    if not docs:
        return []
    if ids is None:
        ids = [str(uuid.uuid4()) for _ in docs]
    if metadatas is None:
        metadatas = [{"source": "nexus", "ts": int(__import__("time").time())} for _ in docs]
    col.add(documents=docs, metadatas=metadatas, ids=ids)
    return ids


def query(text: str, k: int = 4) -> list[dict]:
    """Return the top-k most similar documents to `text`."""
    col = _get_collection()
    res = col.query(query_texts=[text], n_results=k)
    hits: list[dict] = []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    ids = (res.get("ids") or [[]])[0]
    for i, doc in enumerate(docs):
        hits.append(
            {
                "id": ids[i] if i < len(ids) else None,
                "document": doc,
                "metadata": metas[i] if i < len(metas) else {},
                "distance": dists[i] if i < len(dists) else None,
            }
        )
    return hits


@tool
def memory_search(query_text: str, k: int = 4) -> str:
    """Search Nexus's long-term memory for passages similar to query_text.
    Returns up to k hits as newline-delimited snippets."""
    hits = query(query_text, k=k)
    if not hits:
        return "(no results)"
    return "\n\n---\n".join(
        f"[dist={h['distance']:.3f}] {h['document']}" for h in hits
    )


@tool
def memory_add(text: str) -> str:
    """Store a text snippet in Nexus's long-term memory for future recall."""
    ids = add_documents([text])
    return f"stored id={ids[0]}"

"""RAG tool for Nexus — Chroma-backed long-term memory.

Uses Ollama's nomic-embed-text model for embeddings and persists the
collection at ~/AI_Agent/chroma/.
"""
from __future__ import annotations

import json
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Iterable

import chromadb
from langchain_core.tools import tool

COLLECTION_NAME = "nexus-memory"
EMBED_MODEL = "nomic-embed-text"
PERSIST_DIR = Path.home() / "AI_Agent" / "chroma"
OLLAMA_URL = "http://localhost:11434"


def _ollama_embed(texts: list[str]) -> list[list[float]]:
    """Get embeddings via Ollama's /api/embed endpoint."""
    try:
        payload = json.dumps({
            "model": EMBED_MODEL,
            "input": texts,
            "truncate": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA_URL + "/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["embeddings"]
    except Exception:
        # Fallback: return uniform vectors so Chroma still works (queries
        # will be meaningless but the collection stays bootstrapped).
        return [[0.0] * 768 for _ in texts]


class OllamaEmbeddingFunction:
    """Chroma embedding function wrapper that calls Ollama /api/embed."""
    _name = "ollama_nomic-embed-text"

    def __call__(self, input: list[str]) -> list[list[float]]:
        return _ollama_embed(list(input))

    def embed_query(self, input: str) -> list[list[float]]:
        """Single-query embedding — Chroma calls this for query_texts."""
        return _ollama_embed([input])

    def embed_queries(self, queries: list[str]) -> list[list[float]]:
        return self(queries)

    def embed_documents(self, documents: list[str]) -> list[list[float]]:
        return self(documents)

    def _ollama_embed_single(self, text: str) -> list[float]:
        return _ollama_embed([text])[0]

    def name(self) -> str:
        return self._name


_embed_fn = OllamaEmbeddingFunction()
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
            metadata={"hnsw:space": "cosine"},
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
        metadatas = [{"source": "nexus", "ts": int(time.time())} for _ in docs]
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


def _embed_strict(text: str) -> list[float]:
    """Embed one query, RAISING on failure instead of the zero-vector
    fallback. Used by recall() so a retrieval outage injects nothing rather
    than silently matching garbage (the all-zero vector matches everything)."""
    payload = json.dumps({"model": EMBED_MODEL, "input": [text],
                          "truncate": True}).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL + "/api/embed", data=payload,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        emb = json.loads(resp.read().decode("utf-8"))["embeddings"][0]
    if not emb or not any(emb):
        raise RuntimeError("embedding endpoint returned an empty/zero vector")
    return emb


def recall(text: str, k: int = 3, max_distance: float = 0.6) -> list[dict]:
    """Strict semantic recall for prompt injection. Returns
    [{document, distance}] for hits CLOSER than max_distance (cosine), or []
    on any embed/query failure. Unlike query(), this never returns the
    zero-vector fallback's meaningless neighbours, and the distance gate
    drops irrelevant matter (e.g. the git-commit noise in nexus-memory sits
    at distance ~1.0 and is filtered out)."""
    try:
        emb = _embed_strict(text)
        res = _get_collection().query(query_embeddings=[emb], n_results=k)
    except Exception:
        return []
    docs = (res.get("documents") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    out: list[dict] = []
    for i, doc in enumerate(docs):
        d = dists[i] if i < len(dists) else None
        if d is not None and d <= max_distance:
            out.append({"document": doc, "distance": d})
    return out


def seed_documents(path: str, tag: str, source: str = "manual") -> int:
    """Read a file, split into chunks, and add to the collection.
    Returns the number of chunks added.
    Args:
        path: filesystem path to the file
        tag: tag to attach to all chunks
        source: source metadata (defaults to the filename)
    """
    p = Path(path)
    if not p.exists():
        return 0
    text = p.read_text(encoding="utf-8", errors="replace")
    # Split into chunks of ~500 chars with overlap
    chunk_size = 500
    overlap = 100
    chunks = []
    metas = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        # Trim to word boundary
        if end < len(text):
            last_space = chunk.rfind(" ")
            if last_space > chunk_size * 0.5:
                chunk = chunk[:last_space]
                end = start + last_space
        if chunk.strip():
            chunks.append(chunk.strip())
            metas.append({"source": source, "tag": tag, "chunk_of": p.name})
        start = end - overlap
    ids = add_documents(chunks, metadatas=metas)
    return len(ids)


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


@tool
def memory_seed_file(path: str, tag: str, source: str = "manual") -> str:
    """Seed a file into the nexus-memory collection by splitting it into chunks.
    Returns the number of chunks added.
    Args:
        path: filesystem path to the file
        tag: tag to attach to all chunks
        source: source metadata (defaults to the filename)
    """
    count = seed_documents(path, tag, source)
    return f"seeded {count} chunks from {path}"


@tool
def memory_list(tag: str = "", limit: int = 20) -> str:
    """List documents in Nexus's long-term memory.

    Args:
        tag: Optional tag to filter by (checks metadata 'tag' or 'source' field)
        limit: Maximum number of results (default 20)

    Returns formatted list of document IDs, sources, and preview text."""
    col = _get_collection()
    try:
        result = col.get(limit=limit, include=["documents", "metadatas"])
    except Exception as e:
        return f"Error listing memory: {e}"

    ids = result.get("ids", [])
    docs = result.get("documents", [])
    metas = result.get("metadatas", [])

    if not ids:
        return "(memory is empty)"

    lines = []
    for i, doc_id in enumerate(ids):
        meta = metas[i] if i < len(metas) else {}
        doc = docs[i] if i < len(docs) else ""

        if tag:
            source = meta.get("source", "")
            doc_tag = meta.get("tag", "")
            if tag.lower() not in source.lower() and tag.lower() not in doc_tag.lower():
                continue

        source = meta.get("source", "unknown")
        preview = (doc[:80] + "...") if len(doc) > 80 else doc
        preview = preview.replace("\n", " ")
        lines.append(f"[{doc_id[:8]}...] ({source}) {preview}")

    if not lines:
        return f"(no documents matching tag '{tag}')"

    return f"Found {len(lines)} documents:\n" + "\n".join(lines)


@tool
def memory_delete(doc_id: str) -> str:
    """Delete a document from Nexus's long-term memory by ID.

    Args:
        doc_id: The document ID (can be partial, will match prefix)

    Returns confirmation or error message."""
    col = _get_collection()

    try:
        result = col.get(limit=100, include=["documents"])
        all_ids = result.get("ids", [])
    except Exception as e:
        return f"Error accessing memory: {e}"

    matches = [id for id in all_ids if id.startswith(doc_id)]

    if not matches:
        return f"No document found with ID starting with '{doc_id}'"

    if len(matches) > 1 and len(doc_id) < 36:
        return f"Multiple matches found ({len(matches)}). Provide more of the ID: {', '.join(m[:12] + '...' for m in matches[:5])}"

    try:
        col.delete(ids=matches)
        return f"Deleted {len(matches)} document(s): {', '.join(matches)}"
    except Exception as e:
        return f"Error deleting: {e}"


@tool
def memory_stats() -> str:
    """Get statistics about Nexus's long-term memory.

    Returns count, sources breakdown, and storage info."""
    col = _get_collection()

    try:
        result = col.get(include=["metadatas"])
        metas = result.get("metadatas", [])
        total = len(result.get("ids", []))
    except Exception as e:
        return f"Error getting stats: {e}"

    if total == 0:
        return "Memory is empty."

    sources = {}
    tags = {}
    for meta in metas:
        src = meta.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
        tag = meta.get("tag", "")
        if tag:
            tags[tag] = tags.get(tag, 0) + 1

    lines = [f"Total documents: {total}"]
    lines.append("\nBy source:")
    for src, count in sorted(sources.items(), key=lambda x: -x[1])[:10]:
        lines.append(f"  {src}: {count}")

    if tags:
        lines.append("\nBy tag:")
        for tag, count in sorted(tags.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"  {tag}: {count}")

    import os
    try:
        total_size = sum(
            os.path.getsize(os.path.join(dirpath, filename))
            for dirpath, _, filenames in os.walk(PERSIST_DIR)
            for filename in filenames
        )
        lines.append(f"\nStorage: {total_size / 1024 / 1024:.2f} MB")
    except Exception:
        pass

    return "\n".join(lines)

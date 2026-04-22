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


@tool
def memory_list(tag: str = "", limit: int = 20) -> str:
    """List documents in Nexus's long-term memory.

    Args:
        tag: Optional tag to filter by (checks metadata 'tag' or 'source' field)
        limit: Maximum number of results (default 20)

    Returns formatted list of document IDs, sources, and preview text."""
    col = _get_collection()
    # Get all documents (Chroma doesn't have a direct list-all, so we use a broad query)
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

        # Filter by tag if specified
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

    # Try to find the full ID if partial was given
    try:
        result = col.get(limit=100, include=["documents"])
        all_ids = result.get("ids", [])
    except Exception as e:
        return f"Error accessing memory: {e}"

    # Find matching IDs
    matches = [id for id in all_ids if id.startswith(doc_id)]

    if not matches:
        return f"No document found with ID starting with '{doc_id}'"

    if len(matches) > 1 and len(doc_id) < 36:
        return f"Multiple matches found ({len(matches)}). Provide more of the ID: {', '.join(m[:12] + '...' for m in matches[:5])}"

    # Delete the matching document(s)
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

    # Count by source
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

    # Storage size
    import os
    try:
        total_size = sum(
            os.path.getsize(os.path.join(dirpath, filename))
            for dirpath, _, filenames in os.walk(PERSIST_DIR)
            for filename in filenames
        )
        lines.append(f"\nStorage: {total_size / 1024 / 1024:.2f} MB")
    except:
        pass

    return "\n".join(lines)

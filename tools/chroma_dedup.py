"""Chroma deduplication and compaction utility.

Finds and removes near-duplicate chunks from the nexus-memory collection.
Run manually or via scheduled job to keep RAG quality high.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

# Import from rag_tool to share the collection
from tools.rag_tool import _get_collection, PERSIST_DIR

LOG_PATH = Path.home() / "AI_Agent" / "memory" / "dedup-log.md"


def _cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = sum(a * a for a in vec1) ** 0.5
    norm2 = sum(b * b for b in vec2) ** 0.5
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def find_duplicates(threshold: float = 0.95, limit: int = 500) -> list[tuple[str, str, float]]:
    """Find pairs of documents with similarity above threshold.

    Args:
        threshold: Cosine similarity threshold (0.95 = 95% similar)
        limit: Max documents to check (for performance)

    Returns list of (id1, id2, similarity) tuples."""
    col = _get_collection()

    try:
        result = col.get(limit=limit, include=["embeddings", "documents"])
    except Exception as e:
        print(f"Error getting documents: {e}")
        return []

    ids = result.get("ids", [])
    embeddings = result.get("embeddings", [])
    docs = result.get("documents", [])

    # `not embeddings` raises on a numpy array ("truth value ambiguous") —
    # newer Chroma returns embeddings as ndarrays. Check length instead.
    if embeddings is None or len(embeddings) < 2:
        return []

    duplicates = []
    checked = set()

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            pair_key = (ids[i], ids[j])
            if pair_key in checked:
                continue
            checked.add(pair_key)

            sim = _cosine_similarity(embeddings[i], embeddings[j])
            if sim >= threshold:
                duplicates.append((ids[i], ids[j], sim))

    return sorted(duplicates, key=lambda x: -x[2])


def remove_duplicates(
    threshold: float = 0.95,
    dry_run: bool = True,
    keep_newest: bool = True
) -> dict:
    """Remove near-duplicate documents from the collection.

    Args:
        threshold: Cosine similarity threshold (default 0.95)
        dry_run: If True, only report what would be deleted
        keep_newest: If True, keep the newer document (by timestamp)

    Returns dict with stats about the operation."""
    col = _get_collection()
    duplicates = find_duplicates(threshold=threshold)

    if not duplicates:
        return {"found": 0, "removed": 0, "dry_run": dry_run}

    # Get metadata to determine which to keep
    try:
        result = col.get(include=["metadatas"])
        id_to_meta = dict(zip(result.get("ids", []), result.get("metadatas", [])))
    except Exception as e:
        return {"error": str(e)}

    to_remove = set()
    for id1, id2, sim in duplicates:
        meta1 = id_to_meta.get(id1, {})
        meta2 = id_to_meta.get(id2, {})

        ts1 = meta1.get("ts", 0)
        ts2 = meta2.get("ts", 0)

        # Keep newer, remove older (or vice versa based on keep_newest)
        if keep_newest:
            remove_id = id1 if ts1 < ts2 else id2
        else:
            remove_id = id2 if ts1 < ts2 else id1

        to_remove.add(remove_id)

    stats = {
        "found": len(duplicates),
        "to_remove": len(to_remove),
        "dry_run": dry_run,
        "threshold": threshold,
    }

    if not dry_run and to_remove:
        try:
            col.delete(ids=list(to_remove))
            stats["removed"] = len(to_remove)
            _log_dedup(stats)
        except Exception as e:
            stats["error"] = str(e)
            stats["removed"] = 0
    else:
        stats["removed"] = 0

    return stats


def _log_dedup(stats: dict) -> None:
    """Log dedup operation to markdown file."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- {timestamp}: Removed {stats.get('removed', 0)} duplicates (threshold={stats.get('threshold', 0.95)})\n"
    with LOG_PATH.open("a") as f:
        f.write(entry)


@tool
def memory_dedup(threshold: float = 0.95, dry_run: bool = True) -> str:
    """Find and optionally remove near-duplicate documents from memory.

    Args:
        threshold: Similarity threshold (0.95 = 95% similar, default)
        dry_run: If True (default), only report what would be removed

    Use dry_run=False to actually remove duplicates."""
    stats = remove_duplicates(threshold=threshold, dry_run=dry_run)

    if "error" in stats:
        return f"Error: {stats['error']}"

    if stats["found"] == 0:
        return f"No duplicates found above {threshold:.0%} similarity."

    if dry_run:
        return (
            f"Found {stats['found']} duplicate pairs ({stats['to_remove']} unique docs to remove).\n"
            f"Run with dry_run=False to remove them."
        )
    else:
        return f"Removed {stats['removed']} duplicate documents."


@tool
def memory_compact() -> str:
    """Compact the Chroma database to reclaim space after deletions."""
    col = _get_collection()

    try:
        # Get before stats
        before = col.count()

        # Chroma auto-compacts on access, but we can force a persist
        if hasattr(col._client, "persist"):
            col._client.persist()

        after = col.count()

        # Check storage size
        import os
        total_size = sum(
            os.path.getsize(os.path.join(dirpath, filename))
            for dirpath, _, filenames in os.walk(PERSIST_DIR)
            for filename in filenames
        )

        return (
            f"Compaction complete.\n"
            f"Documents: {after}\n"
            f"Storage: {total_size / 1024 / 1024:.2f} MB"
        )
    except Exception as e:
        return f"Error during compaction: {e}"


# CLI interface
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python chroma_dedup.py [find|remove|compact]")
        print("  find   - Find duplicates (dry run)")
        print("  remove - Remove duplicates")
        print("  compact - Compact database")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "find":
        threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 0.95
        dupes = find_duplicates(threshold=threshold)
        print(f"Found {len(dupes)} duplicate pairs (threshold={threshold}):")
        for id1, id2, sim in dupes[:20]:
            print(f"  {id1[:8]}... <-> {id2[:8]}... ({sim:.3f})")

    elif cmd == "remove":
        threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 0.95
        stats = remove_duplicates(threshold=threshold, dry_run=False)
        print(json.dumps(stats, indent=2))

    elif cmd == "compact":
        print(memory_compact.invoke({}))

    else:
        print(f"Unknown command: {cmd}")

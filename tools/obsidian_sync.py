"""Obsidian sync (Phase 18.3).

If `~/Obsidian/` exists, walk it and seed every .md file into Chroma RAG
tagged `obsidian`. This is a one-shot tool — for live "watch" semantics
the Phase 11 file_watcher already covers any path under HOME by mtime
diff, and the Phase 16.5 scheduler can call this on a daily interval.

Idempotent: each doc keys on the relative path so re-syncing just
overwrites.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from langchain_core.tools import tool

ROOT = Path.home() / "Obsidian"
MAX_BYTES_PER_FILE = 100_000


@tool
def obsidian_sync(root: str = "") -> str:
    """Index every .md file under ~/Obsidian (or the given root) into RAG.

    Args:
        root: optional override; defaults to ~/Obsidian.
    """
    base = Path(root).expanduser() if root else ROOT
    if not base.exists():
        return f"obsidian root {base} not found — vault not present yet."
    from tools.rag_tool import add_documents

    indexed = 0
    skipped = 0
    for p in base.rglob("*.md"):
        if any(part.startswith(".") for part in p.parts[len(base.parts):]):
            skipped += 1
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        if not text.strip():
            continue
        if len(text) > MAX_BYTES_PER_FILE:
            text = text[:MAX_BYTES_PER_FILE]
        rel = str(p.relative_to(base))
        doc_id = hashlib.sha1(rel.encode()).hexdigest()[:16]
        try:
            add_documents(
                texts=[text],
                metadatas=[{"source": "obsidian", "path": rel, "doc_id": doc_id}],
                tag="obsidian",
            )
            indexed += 1
        except Exception:
            skipped += 1
    return f"obsidian sync: indexed {indexed} notes, skipped {skipped}, root {base}"


OBSIDIAN_TOOLS = [obsidian_sync]

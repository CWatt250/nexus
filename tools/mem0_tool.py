"""Mem0 tool for Nexus — local LLM-refined long-term memory using
Ollama (qwen3:4b) as the extractor and Chroma as the vector store.

Runs entirely on WattBott. No cloud calls."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import tool

MEM0_DIR = Path.home() / "AI_Agent" / "memory" / "mem0"
CHROMA_DIR = MEM0_DIR / "chroma"
HISTORY_DB = MEM0_DIR / "history.db"
OLLAMA_URL = "http://localhost:11434"
DEFAULT_USER = "nexus"

_memory: Any = None


def _config() -> dict:
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "llm": {
            "provider": "ollama",
            "config": {
                "model": "qwen3:4b",
                "temperature": 0.1,
                "max_tokens": 1024,
                "ollama_base_url": OLLAMA_URL,
            },
        },
        "embedder": {
            "provider": "huggingface",
            "config": {
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "embedding_dims": 384,
            },
        },
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": "nexus-mem0",
                "path": str(CHROMA_DIR),
            },
        },
        "history_db_path": str(HISTORY_DB),
    }


def _get_memory():
    global _memory
    if _memory is None:
        from mem0 import Memory
        _memory = Memory.from_config(_config())
    return _memory


@tool
def mem0_add(text: str) -> str:
    """Add a memory to Mem0. Mem0 uses the local LLM (qwen3:4b) to extract
    durable facts from the text and store them with embeddings in Chroma.
    Use this for facts, preferences, and decisions worth remembering across
    sessions. For raw passages, use memory_add (plain Chroma) instead."""
    try:
        mem = _get_memory()
        result = mem.add(text, user_id=DEFAULT_USER)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"
    results = result.get("results") if isinstance(result, dict) else result
    if not results:
        return "(nothing extracted)"
    lines = []
    for r in results:
        mid = r.get("id", "?") if isinstance(r, dict) else "?"
        mem_text = r.get("memory", "") if isinstance(r, dict) else str(r)
        event = r.get("event", "ADD") if isinstance(r, dict) else "ADD"
        lines.append(f"[{event}] {mid}: {mem_text}")
    return "\n".join(lines)


@tool
def mem0_search(query: str, k: int = 5) -> str:
    """Semantic search over Mem0. Returns up to k extracted memories
    matching the query, ranked by similarity."""
    try:
        mem = _get_memory()
        result = mem.search(query=query, user_id=DEFAULT_USER, limit=k)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"
    results = result.get("results") if isinstance(result, dict) else result
    if not results:
        return "(no results)"
    lines = []
    for r in results:
        if not isinstance(r, dict):
            lines.append(str(r))
            continue
        mem_text = r.get("memory", "")
        score = r.get("score")
        score_str = f" score={score:.3f}" if isinstance(score, (int, float)) else ""
        lines.append(f"-{score_str} {mem_text}")
    return "\n".join(lines)

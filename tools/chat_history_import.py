"""Claude/ChatGPT history import (Phase 18.4).

Two tools that parse the standard export JSON formats and seed each
conversation as one RAG doc tagged `claude_history` or `chatgpt_history`.

Claude export: a JSON array; each item has `name` + `chat_messages` with
`text` per message.
ChatGPT export: a JSON array; each item has `title` + `mapping` (a
node-graph of messages).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from langchain_core.tools import tool


def _walk_chatgpt_mapping(mapping: dict) -> Iterable[str]:
    for _, node in mapping.items():
        msg = (node or {}).get("message")
        if not msg:
            continue
        content = msg.get("content") or {}
        parts = content.get("parts") or []
        text = " ".join(str(p) for p in parts if isinstance(p, (str, int)))
        if text.strip():
            role = (msg.get("author") or {}).get("role") or "?"
            yield f"[{role}] {text.strip()}"


@tool
def claude_history_import(json_path: str) -> str:
    """Import a Claude conversation export JSON into RAG (tag='claude_history')."""
    p = Path(json_path).expanduser()
    if not p.exists():
        return f"file not found: {p}"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"parse failed: {type(exc).__name__}: {exc}"
    if not isinstance(data, list):
        return "expected a JSON array (Claude export shape)"
    from tools.rag_tool import add_documents
    indexed = 0
    for conv in data:
        title = conv.get("name") or conv.get("title") or "untitled"
        messages = conv.get("chat_messages") or conv.get("messages") or []
        body_lines = []
        for m in messages:
            text = (m.get("text") or m.get("content") or "").strip()
            sender = m.get("sender") or m.get("role") or "?"
            if text:
                body_lines.append(f"[{sender}] {text}")
        if not body_lines:
            continue
        body = "\n".join(body_lines)
        doc_id = hashlib.sha1((title + str(len(body))).encode()).hexdigest()[:16]
        try:
            add_documents(
                texts=[f"# {title}\n\n{body}"],
                metadatas=[{"source": "claude", "title": title, "doc_id": doc_id}],
                tag="claude_history",
            )
            indexed += 1
        except Exception:
            pass
    return f"claude history: indexed {indexed} conversations from {p}"


@tool
def chatgpt_history_import(json_path: str) -> str:
    """Import a ChatGPT conversation export JSON into RAG (tag='chatgpt_history')."""
    p = Path(json_path).expanduser()
    if not p.exists():
        return f"file not found: {p}"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"parse failed: {type(exc).__name__}: {exc}"
    if not isinstance(data, list):
        return "expected a JSON array (ChatGPT export shape)"
    from tools.rag_tool import add_documents
    indexed = 0
    for conv in data:
        title = conv.get("title") or "untitled"
        mapping = conv.get("mapping") or {}
        lines = list(_walk_chatgpt_mapping(mapping)) if isinstance(mapping, dict) else []
        if not lines:
            continue
        body = "\n".join(lines)
        doc_id = hashlib.sha1((title + str(len(body))).encode()).hexdigest()[:16]
        try:
            add_documents(
                texts=[f"# {title}\n\n{body}"],
                metadatas=[{"source": "chatgpt", "title": title, "doc_id": doc_id}],
                tag="chatgpt_history",
            )
            indexed += 1
        except Exception:
            pass
    return f"chatgpt history: indexed {indexed} conversations from {p}"


HISTORY_TOOLS = [claude_history_import, chatgpt_history_import]

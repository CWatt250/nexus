"""MarkItDown tool for Nexus — convert files or URLs to markdown and stash
the result in Chroma RAG memory for later recall."""
from __future__ import annotations

import os
from pathlib import Path

from langchain_core.tools import tool

from tools.rag_tool import add_documents

MAX_RETURN_CHARS = 20_000


def _markitdown():
    from markitdown import MarkItDown
    return MarkItDown()


def convert(source: str) -> tuple[str, dict]:
    """Convert a local path or URL to markdown. Returns (markdown, metadata)."""
    md = _markitdown()
    target = os.path.expanduser(source)
    is_url = target.startswith(("http://", "https://"))
    if not is_url:
        p = Path(target).resolve()
        if not p.exists():
            raise FileNotFoundError(f"no such file: {p}")
        target = str(p)
    result = md.convert(target)
    text = getattr(result, "text_content", None) or getattr(result, "markdown", "") or ""
    meta = {
        "source": target,
        "source_kind": "url" if is_url else "file",
        "tool": "markitdown",
    }
    title = getattr(result, "title", None)
    if title:
        meta["title"] = str(title)[:200]
    return text, meta


@tool
def markitdown_tool(source: str) -> str:
    """Convert a document (PDF, Word, Excel, PowerPoint, HTML, image, audio,
    etc.) or a URL into markdown using Microsoft MarkItDown, and also store
    the result in Chroma RAG memory tagged with the source path.

    Args:
        source: absolute path to a local file (~ expands) or an http(s) URL.

    Returns the converted markdown (truncated to 20k chars for chat)."""
    try:
        text, meta = convert(source)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"
    if not text.strip():
        return f"(markitdown produced no text for {source})"
    try:
        add_documents([text], metadatas=[meta])
    except Exception as exc:
        return (
            f"converted OK but RAG store failed ({type(exc).__name__}: {exc}):\n\n"
            + text[:MAX_RETURN_CHARS]
        )
    out = text
    if len(out) > MAX_RETURN_CHARS:
        out = out[:MAX_RETURN_CHARS] + f"\n\n[truncated — full document has {len(text)} chars; stored in RAG]"
    return f"SOURCE: {meta['source']}\n\n{out}"

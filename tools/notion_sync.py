"""Notion sync (Phase 18.2).

Reads `NOTION_API_KEY` and an optional `NOTION_DATABASE_ID` from
`~/AI_Agent/.env`. When present, `notion_sync(database_id)` pulls every
page in the database, flattens its rich-text blocks to markdown, and
seeds the resulting docs into Chroma RAG tagged `notion`.

If creds are missing the tool returns a clear setup hint instead of
raising. Uses the public Notion REST API directly via httpx — no
notion-client SDK dependency.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv(Path.home() / "AI_Agent" / ".env")
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
TIMEOUT = 30


def _key() -> str | None:
    k = os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN")
    return (k or "").strip() or None


def _missing() -> str:
    return (
        "Notion creds not found. Set NOTION_API_KEY in ~/AI_Agent/.env. "
        "Get a token at https://www.notion.so/my-integrations and share the "
        "target database with the integration."
    )


def _flatten_block_text(block: dict) -> str:
    rich = []
    for v in block.values():
        if isinstance(v, dict):
            rich.extend(v.get("rich_text", []) or [])
    return "".join((rt.get("plain_text") or "") for rt in rich)


@tool
def notion_sync(database_id: str = "", limit: int = 100) -> str:
    """Pull pages from a Notion database and seed them into RAG (tag='notion').

    Args:
        database_id: Notion database id (UUID or short id). Defaults to
                     the NOTION_DATABASE_ID env var if not given.
        limit: max pages to fetch.
    """
    key = _key()
    if not key:
        return _missing()
    database_id = database_id or os.getenv("NOTION_DATABASE_ID", "")
    if not database_id:
        return "database_id required (or set NOTION_DATABASE_ID in .env)"
    headers = {
        "Authorization": f"Bearer {key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    pages: list[dict] = []
    cursor: str | None = None
    fetched = 0
    try:
        while fetched < limit:
            payload = {"page_size": min(100, limit - fetched)}
            if cursor:
                payload["start_cursor"] = cursor
            with httpx.Client(timeout=TIMEOUT) as client:
                r = client.post(f"{NOTION_API}/databases/{database_id}/query",
                                json=payload, headers=headers)
            if r.status_code != 200:
                return f"Notion {r.status_code}: {r.text[:300]}"
            data = r.json()
            pages.extend(data.get("results", []))
            fetched = len(pages)
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
    except Exception as exc:
        return f"Notion fetch failed: {type(exc).__name__}: {exc}"

    # For each page, fetch its first 100 child blocks and flatten to markdown.
    from tools.rag_tool import add_documents
    indexed = 0
    for p in pages:
        page_id = p.get("id")
        title = ""
        for prop in (p.get("properties") or {}).values():
            if prop.get("type") in ("title",):
                title = "".join((rt.get("plain_text") or "")
                                for rt in (prop.get("title") or []))
                break
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                r = client.get(f"{NOTION_API}/blocks/{page_id}/children?page_size=100",
                               headers=headers)
            blocks = r.json().get("results", []) if r.status_code == 200 else []
        except Exception:
            blocks = []
        body = "\n".join(_flatten_block_text(b) for b in blocks).strip()
        if not (title or body):
            continue
        text = f"# {title}\n\n{body}".strip()
        try:
            add_documents(
                texts=[text],
                metadatas=[{"source": "notion", "page_id": page_id, "title": title}],
                tag="notion",
            )
            indexed += 1
        except Exception:
            pass
    return f"notion sync: pulled {len(pages)} pages, indexed {indexed}"


NOTION_TOOLS = [notion_sync]

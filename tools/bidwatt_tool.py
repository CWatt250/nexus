"""BidWatt read-only integration (Phase 16.4).

Three LangGraph tools that read from the BidWatt Supabase project:

  - bidwatt_list_bids(limit=20, status=?)
  - bidwatt_get_bid(bid_id)
  - bidwatt_search_bids(query, limit=10)

Strictly read-only — no INSERT / UPDATE / DELETE. Reads creds from
`~/AI_Agent/.env` (`BIDWATT_SUPABASE_URL`, `BIDWATT_SUPABASE_ANON_KEY`)
or the BidWatt repo's `.env.local` when present. If creds are missing,
each tool returns a clear setup message instead of crashing.

This calls Supabase's PostgREST API directly via httpx so the tool has
no extra Python dependency on the supabase-py SDK.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from langchain_core.tools import tool

ROOT = Path.home() / "AI_Agent"
load_dotenv(ROOT / ".env")

BIDBOARD_REPO = Path.home() / "Dev" / "cwatt-bidboard"
TIMEOUT = 10


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _creds() -> tuple[str | None, str | None]:
    """Return (url, anon_key). Prefer the Nexus .env, fall back to the
    BidWatt repo's .env.local."""
    url = os.getenv("BIDWATT_SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("BIDWATT_SUPABASE_ANON_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not (url and key):
        for fname in (".env.local", ".env"):
            extra = _read_env_file(BIDBOARD_REPO / fname)
            url = url or extra.get("NEXT_PUBLIC_SUPABASE_URL") or extra.get("BIDWATT_SUPABASE_URL")
            key = key or extra.get("NEXT_PUBLIC_SUPABASE_ANON_KEY") or extra.get("BIDWATT_SUPABASE_ANON_KEY")
            if url and key:
                break
    return url or None, key or None


def _missing_message() -> str:
    return (
        "BidWatt creds not found. Set BIDWATT_SUPABASE_URL and "
        "BIDWATT_SUPABASE_ANON_KEY in ~/AI_Agent/.env (or copy them from "
        "~/Dev/cwatt-bidboard/.env.local)."
    )


def _request(path: str, params: dict | None = None) -> str:
    url_base, key = _creds()
    if not (url_base and key):
        return _missing_message()
    url = f"{url_base.rstrip('/')}/rest/v1/{path.lstrip('/')}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.get(url, headers=headers, params=params or {})
    except Exception as exc:
        return f"BidWatt request failed: {type(exc).__name__}: {exc}"
    if resp.status_code != 200:
        return f"BidWatt {resp.status_code}: {resp.text[:300]}"
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return f"BidWatt returned non-JSON: {resp.text[:300]}"
    return json.dumps(data, ensure_ascii=False, indent=2)[:4000]


@tool
def bidwatt_list_bids(limit: int = 20, status: str = "") -> str:
    """List recent BidWatt bids (read-only).

    Args:
        limit: max rows to return (capped server-side at 100).
        status: optional status filter, e.g. 'open', 'won', 'lost'.
    """
    params: dict = {
        "select": "id,project_name,client,due_date,status,total_value,created_at",
        "order": "created_at.desc",
        "limit": str(min(max(int(limit or 1), 1), 100)),
    }
    if status:
        params["status"] = f"eq.{status}"
    return _request("bids", params)


@tool
def bidwatt_get_bid(bid_id: str) -> str:
    """Fetch one BidWatt bid by id (read-only)."""
    if not bid_id:
        return "bid_id required"
    return _request("bids", {"id": f"eq.{bid_id}", "select": "*", "limit": "1"})


@tool
def bidwatt_search_bids(query: str, limit: int = 10) -> str:
    """Search BidWatt bids by free-text against project_name and client (read-only)."""
    if not query:
        return "query required"
    # PostgREST `or=(project_name.ilike.*foo*,client.ilike.*foo*)`
    pattern = f"*{query}*"
    or_filter = f"(project_name.ilike.{pattern},client.ilike.{pattern})"
    return _request("bids", {
        "or": or_filter,
        "select": "id,project_name,client,due_date,status,total_value",
        "order": "created_at.desc",
        "limit": str(min(max(int(limit or 1), 1), 50)),
    })


BIDWATT_TOOLS = [bidwatt_list_bids, bidwatt_get_bid, bidwatt_search_bids]

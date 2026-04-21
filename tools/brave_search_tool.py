"""Brave Search tools for Nexus.

Uses the Brave Search API directly over httpx. The API key is read from
`BRAVE_SEARCH_API_KEY` in the environment or `~/AI_Agent/.env`.

Exposes `brave_search(query, count)` and `brave_search_news(query)`."""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from langchain_core.tools import tool

ENV_FILE = Path.home() / "AI_Agent" / ".env"
WEB_URL = "https://api.search.brave.com/res/v1/web/search"
NEWS_URL = "https://api.search.brave.com/res/v1/news/search"
TIMEOUT = 15


def _load_env_file() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_FILE.exists():
        return out
    try:
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            out[k.strip()] = v
    except OSError:
        pass
    return out


def _api_key() -> str | None:
    k = os.environ.get("BRAVE_SEARCH_API_KEY")
    if k:
        return k
    return _load_env_file().get("BRAVE_SEARCH_API_KEY") or None


def _missing_key_message() -> str:
    return "Add BRAVE_SEARCH_API_KEY to ~/AI_Agent/.env to enable web search"


def _request(url: str, params: dict) -> dict | str:
    key = _api_key()
    if not key:
        return _missing_key_message()
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": key,
    }
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.get(url, headers=headers, params=params)
        if r.status_code == 401:
            return "ERROR: Brave API rejected the key (401). Check BRAVE_SEARCH_API_KEY."
        if r.status_code == 429:
            return "ERROR: Brave API rate-limited (429). Back off and retry."
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


def _format_web(data: dict) -> str:
    items = ((data.get("web") or {}).get("results")) or []
    if not items:
        return "(no results)"
    lines = []
    for r in items:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("description") or r.get("snippet") or "").strip()
        lines.append(f"- {title}\n  {url}\n  {snippet}")
    return "\n".join(lines)


def _format_news(data: dict) -> str:
    items = ((data.get("results") or []) if "results" in data else
             ((data.get("news") or {}).get("results")) or [])
    if not items:
        return "(no news results)"
    lines = []
    for r in items:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("description") or "").strip()
        age = (r.get("age") or "").strip()
        prefix = f"[{age}] " if age else ""
        lines.append(f"- {prefix}{title}\n  {url}\n  {snippet}")
    return "\n".join(lines)


@tool
def brave_search(query: str, count: int = 5) -> str:
    """Search the web via the Brave Search API.

    Args:
        query: natural-language search query.
        count: how many results to return (default 5, max 20).
    Returns one result per entry as 'title\\n  url\\n  snippet'. If no API
    key is configured, returns a clear instruction message."""
    count = max(1, min(int(count), 20))
    data = _request(WEB_URL, {"q": query, "count": count})
    if isinstance(data, str):
        return data
    return _format_web(data)


@tool
def brave_search_news(query: str, count: int = 5) -> str:
    """Search news articles specifically via Brave Search News API.

    Args:
        query: natural-language news query.
        count: how many results to return (default 5, max 20).
    Returns one result per entry prefixed with the article age. If no API
    key is configured, returns a clear instruction message."""
    count = max(1, min(int(count), 20))
    data = _request(NEWS_URL, {"q": query, "count": count})
    if isinstance(data, str):
        return data
    return _format_news(data)

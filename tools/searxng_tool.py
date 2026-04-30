"""SearXNG — self-hosted search backend (R5).

Replaces Brave-only web search with a free, unlimited, locally-hosted
SearXNG container running on http://127.0.0.1:8888. The container is
managed via `searxng/docker-compose.yml` + `nexus-searxng.service`.

Two tools live here:

  - `searxng_search(query, count)` — generic web search, returns a list
    of results. Use this directly when you want unfiltered breadth, or
    let `web_search()` (in `tools/search_router.py`) pick the best
    available backend.
  - `searxng_health()` — fast probe that returns "ok" or a short reason
    string. Useful for the dashboard / runbook.

Network-tolerant: a stopped container or 5xx response returns a clear
error string — never raises. The agent can recover by retrying with
the upstream `web_search()` chain (Tavily → Brave → SearXNG).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from langchain_core.tools import tool

log = logging.getLogger("nexus.searxng")

SEARXNG_URL = "http://127.0.0.1:8888"
TIMEOUT = 10.0
MAX_COUNT = 25


def _request(query: str, count: int, *, news: bool = False) -> dict | str:
    """Hit the SearXNG JSON API. Returns the parsed JSON dict on success
    or a plain error string on failure (caller decides what to do)."""
    if not query or not query.strip():
        return "ERROR: empty query"
    params: dict[str, Any] = {
        "q": query.strip(),
        "format": "json",
        "language": "en",
        "safesearch": 0,
    }
    if news:
        params["categories"] = "news"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            r = client.get(f"{SEARXNG_URL}/search", params=params)
        if r.status_code == 403:
            return (
                "ERROR: SearXNG returned 403 — JSON format probably not "
                "enabled. Check ~/AI_Agent/searxng/config/settings.yml "
                "has `formats: [html, json]` then `docker compose restart`."
            )
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        return (
            "ERROR: SearXNG not reachable at http://127.0.0.1:8888. "
            "Start the container with: "
            "`cd ~/AI_Agent/searxng && docker compose up -d`."
        )
    except httpx.HTTPError as exc:
        return f"ERROR: SearXNG HTTP error: {type(exc).__name__}: {exc}"
    except ValueError as exc:  # JSONDecodeError subclasses ValueError
        return f"ERROR: SearXNG returned non-JSON: {exc}"


def _format_results(data: dict, count: int) -> str:
    items = data.get("results") or []
    if not items:
        infoboxes = data.get("infoboxes") or []
        if infoboxes:
            ib = infoboxes[0]
            title = ib.get("infobox") or ib.get("title") or ""
            content = ib.get("content") or ""
            return f"INFOBOX: {title}\n{content}".strip()
        return "(no results)"
    lines = []
    for r in items[:count]:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("content") or "").strip()
        engine = r.get("engine") or ""
        prefix = f"[{engine}] " if engine else ""
        lines.append(f"- {prefix}{title}\n  {url}\n  {snippet}")
    return "\n".join(lines)


@tool
def searxng_search(query: str, count: int = 5) -> str:
    """Search the web via the local SearXNG container (free, unlimited,
    no API key required). Aggregates Google + Bing + DuckDuckGo +
    Wikipedia + GitHub + Stack Overflow + Reddit + YouTube + news.

    Args:
        query: the natural-language query.
        count: how many results to return (default 5, max 25).

    Returns one entry per result formatted as:
        - [engine] title
          url
          snippet

    On container outage or HTTP error returns a short ERROR string —
    never raises. The agent should fall back to `web_search()`'s next
    backend in that case.
    """
    count = max(1, min(int(count), MAX_COUNT))
    data = _request(query, count)
    if isinstance(data, str):
        return data
    return _format_results(data, count)


@tool
def searxng_search_news(query: str, count: int = 5) -> str:
    """Search recent news via the local SearXNG container, restricted to
    the news category (Google News + Bing News + DuckDuckGo News +
    Yahoo News). Same return shape as `searxng_search`. No API key.
    """
    count = max(1, min(int(count), MAX_COUNT))
    data = _request(query, count, news=True)
    if isinstance(data, str):
        return data
    return _format_results(data, count)


@tool
def searxng_health() -> str:
    """Health probe for the local SearXNG container. Returns 'ok' if
    the JSON search endpoint responds inside the timeout, or a short
    reason string otherwise. Cheap — runs a 'ping' query with count=1.
    """
    try:
        with httpx.Client(timeout=3.0) as client:
            r = client.get(
                f"{SEARXNG_URL}/search",
                params={"q": "ping", "format": "json"},
            )
        if r.status_code != 200:
            return f"degraded: HTTP {r.status_code}"
        body = r.json()
        results = body.get("results") or body.get("infoboxes") or []
        if not isinstance(results, list):
            return "degraded: unexpected JSON shape"
        return "ok"
    except httpx.ConnectError:
        return "down: container not reachable on 127.0.0.1:8888"
    except Exception as exc:
        return f"down: {type(exc).__name__}: {exc}"


SEARXNG_TOOLS = [searxng_search, searxng_search_news, searxng_health]

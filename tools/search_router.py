"""web_search() — picks the best available web-search backend.

Priority chain:

  1. Tavily          — if `TAVILY_API_KEY` is set in secrets.yaml / env.
                       LLM-tuned snippets, paid tier with free credits.
  2. Brave Search    — if `BRAVE_SEARCH_API_KEY` is set.
                       Fast, paid after free tier.
  3. SearXNG (local) — always-on fallback, free, unlimited, runs on
                       http://127.0.0.1:8888 via Docker.

The function is called `web_search` so the agent has one stable tool
name regardless of which backend answered. Backend names are surfaced
in the response prefix so the user can see who served the query.

If a higher-priority backend errors (rate-limit, network, etc.), we
fall through to the next one rather than returning the error — the
fallback chain is the whole point.
"""
from __future__ import annotations

import logging
import os
from typing import Callable

from langchain_core.tools import tool

from core import secrets

log = logging.getLogger("nexus.web_search")

# Used by the smoke tests to deterministically force one backend.
_FORCE_BACKEND_ENV = "NEXUS_WEB_SEARCH_FORCE"


def _has_tavily() -> bool:
    return bool(secrets.get("TAVILY_API_KEY") or os.environ.get("TAVILY_API_KEY"))


def _has_brave() -> bool:
    return bool(
        secrets.get("BRAVE_SEARCH_API_KEY")
        or os.environ.get("BRAVE_SEARCH_API_KEY")
    )


def _is_error(result: str) -> bool:
    """Standard error-shape from any of the search tools is a string
    starting with 'ERROR' or 'Add ' (the brave 'no key' nudge)."""
    if not result:
        return True
    head = result.strip()[:8].upper()
    return head.startswith("ERROR") or head.startswith("ADD ") or head.startswith("(NO ")


def _call_tavily(query: str, count: int) -> str:
    """Tavily isn't built yet — leave a stub so adding the integration
    later is one small change. For now this returns a sentinel that
    `web_search` treats as an error and skips."""
    return "ERROR: Tavily backend not yet wired (TAVILY_API_KEY present but no client)"


def _call_brave(query: str, count: int) -> str:
    from tools.brave_search_tool import brave_search  # noqa: PLC0415
    return brave_search.invoke({"query": query, "count": count})


def _call_searxng(query: str, count: int) -> str:
    from tools.searxng_tool import searxng_search  # noqa: PLC0415
    return searxng_search.invoke({"query": query, "count": count})


def _backend_chain() -> list[tuple[str, Callable[[str, int], str]]]:
    """Build the ordered (label, fn) list of backends to try.

    SearXNG is always in the chain so the agent never needs an API key
    to search the web.
    """
    chain: list[tuple[str, Callable[[str, int], str]]] = []
    if _has_tavily():
        chain.append(("tavily", _call_tavily))
    if _has_brave():
        chain.append(("brave", _call_brave))
    chain.append(("searxng", _call_searxng))
    return chain


@tool
def web_search(query: str, count: int = 5) -> str:
    """Search the web. Picks the best backend automatically:

      1. Tavily (if TAVILY_API_KEY configured)
      2. Brave Search (if BRAVE_SEARCH_API_KEY configured)
      3. SearXNG localhost (free, always-on)

    Falls through to the next backend on any error so the chain is
    self-healing — a Brave 429 doesn't ruin the turn.

    Use this for general web search. For news specifically, use
    `searxng_search_news` (or `brave_search_news` if you want the paid
    variant).
    """
    forced = os.environ.get(_FORCE_BACKEND_ENV, "").strip().lower()
    chain = _backend_chain()
    if forced:
        chain = [c for c in chain if c[0] == forced] or chain

    last_error = ""
    for label, fn in chain:
        try:
            out = fn(query, count)
        except Exception as exc:
            last_error = f"{label}: {type(exc).__name__}: {exc}"
            log.warning("web_search %s raised: %s", label, exc)
            continue
        if _is_error(out):
            last_error = f"{label}: {out.splitlines()[0][:160]}"
            log.info("web_search %s soft-failed; trying next: %s",
                     label, last_error)
            continue
        # Success — annotate which backend served and return.
        return f"[search:{label}]\n{out}"
    return (
        f"ERROR: every web_search backend failed. "
        f"Last: {last_error or '(no backends configured)'}"
    )


WEB_SEARCH_TOOLS = [web_search]

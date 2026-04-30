"""Fast-tool whitelist for the QUERY_TOOL routing path.

The conversation handler's `lite_agent()` picks one tool from this
registry, calls it, then formats the result. This is the
single-tool-call fast path — anything that needs iteration, sub-agents,
or browser rendering belongs in TASK, not here.

Eligibility for this whitelist:
- Single API/HTTP call (no orchestration).
- Typical wall-clock < 3 s.
- Simple, named-arg schema the picker LLM can fill from one JSON line.
- No safety-sensitive side effects (no `terminal`, no destructive ops).

Each entry:
  - `tool`: the LangChain tool object (so we keep tool.invoke semantics).
  - `description`: one-line hint for the picker prompt. Keep it short —
    the picker reads ALL descriptions on every routing turn.
  - `args`: human-readable argument doc for the picker. The picker is
    instructed to emit a JSON object whose keys match.
"""
from __future__ import annotations

from typing import Any

from tools.searxng_tool import (
    searxng_search,
    searxng_search_news,
    searxng_health,
)
from tools.search_router import web_search
from tools.github_tool import (
    github_auth_status,
    github_list_my_repos,
    github_list_repos,
    github_list_issues,
    github_get_file,
)
from tools.rag_tool import memory_search, memory_stats


_REGISTRY: dict[str, dict[str, Any]] = {
    # Web — always SearXNG-first via the router; web_search auto-falls
    # through to direct searxng_search if it's the only backend available.
    "web_search": {
        "tool": web_search,
        "description": "Search the web for current info (weather, news, prices, "
                       "general facts). Routes to SearXNG locally — fast, free.",
        "args": {"query": "string (required)", "count": "int (optional, default 5)"},
    },
    "searxng_search": {
        "tool": searxng_search,
        "description": "Direct SearXNG search (same backend web_search uses).",
        "args": {"query": "string (required)", "count": "int (optional, default 5)"},
    },
    "searxng_search_news": {
        "tool": searxng_search_news,
        "description": "News-only search via SearXNG. Use for current events.",
        "args": {"query": "string (required)", "count": "int (optional, default 5)"},
    },
    "searxng_health": {
        "tool": searxng_health,
        "description": "Health probe for the local SearXNG container.",
        "args": {},
    },

    # GitHub — every entry is a single PyGithub call, all <2s.
    "github_auth_status": {
        "tool": github_auth_status,
        "description": "Report GitHub auth state: who's logged in, scopes, rate limit.",
        "args": {},
    },
    "github_list_my_repos": {
        "tool": github_list_my_repos,
        "description": "List ALL repos the user can access (public + private).",
        "args": {"visibility": "string (optional: 'all'|'public'|'private', default 'all')",
                 "limit": "int (optional, default 30)"},
    },
    "github_list_repos": {
        "tool": github_list_repos,
        "description": "List the user's own repositories.",
        "args": {"visibility": "string (optional)", "limit": "int (optional, default 30)"},
    },
    "github_list_issues": {
        "tool": github_list_issues,
        "description": "List open/closed issues on a specific repo.",
        "args": {"repo": "string 'owner/name' (required)",
                 "state": "string (optional: 'open'|'closed'|'all')",
                 "limit": "int (optional, default 20)"},
    },
    "github_get_file": {
        "tool": github_get_file,
        "description": "Read the contents of a file from a GitHub repo.",
        "args": {"repo": "string 'owner/name' (required)",
                 "path": "string (required)",
                 "ref": "string (optional, branch/tag/sha)"},
    },

    # Memory — local Chroma queries, instant.
    "memory_search": {
        "tool": memory_search,
        "description": "Search Nexus's long-term memory (Chroma RAG).",
        "args": {"query_text": "string (required)", "k": "int (optional, default 4)"},
    },
    "memory_stats": {
        "tool": memory_stats,
        "description": "Get statistics about the long-term memory store.",
        "args": {},
    },
}


def get_registry() -> dict[str, dict[str, Any]]:
    """Return the lite-agent tool registry. Function (not module-level
    constant) so tests can monkey-patch it without import-order issues.
    """
    return _REGISTRY


def picker_prompt_block() -> str:
    """Format the registry as a tight tools-available block for the
    picker LLM. Keep this under 800 chars total — it goes into every
    QUERY_TOOL routing call."""
    lines = ["Available tools:"]
    for name, meta in _REGISTRY.items():
        lines.append(f"- {name}({_args_inline(meta['args'])}): {meta['description']}")
    return "\n".join(lines)


def _args_inline(args: dict[str, str]) -> str:
    if not args:
        return ""
    return ", ".join(args.keys())


# Names exported for tier-tagging in TOOLS.md.
FAST_TOOL_NAMES = frozenset(_REGISTRY.keys())

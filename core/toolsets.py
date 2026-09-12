"""Phase 1 — route-scoped toolsets.

Ornith-1.5 is hybrid-attention: llama.cpp re-prefills the WHOLE prompt on
every agent step, and all 114 tool schemas cost ~15K tokens of that
prompt. Binding only the tools a route can plausibly need cuts each step
from ~49K tokens to well under 8K.

Three named sets, selected by tool *name* so this module never imports
the tool modules themselves:

  lite   ~15 tools — read-only lookups (web, wiki, weather, files, shell,
         RAG, screenshot) + telegram_notify.
  heavy  ~45 tools — lite + editing, git/github, codebase intel, tests,
         diff review, builder, browser, image gen, goals, memory, and the
         computer-use surface.
  full   everything in nexus.TOOLS (CLI / explicit opt-in only).

MCP tools discovered at runtime (nexus.extend_tools_with_mcp) are passed in
via `extra_names` so heavy/full keep seeing them.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Sequence

log = logging.getLogger("nexus.toolsets")

DEFAULT_TOOLSET = "heavy"

LITE: list[str] = [
    "web_search",
    "searxng_search_news",
    "quick_lookup",
    "wiki_query",
    "get_weather",
    "file_read_tool",
    "glob_tool",
    "grep_tool",
    "terminal",
    "sandbox_exec",
    "memory_search",
    "telegram_notify",
    "screenshot",
    "describe_image",
    "desktop_screenshot",
    "system_status",
]

# ~44 tools / ~6K schema tokens. Fat, rarely-needed schemas (wiki_update,
# html_mockup, markitdown, github_list_*, dispatch_to_claude_code — cloud
# tiers are explicit-slash-only anyway) stay in `full`.
HEAVY: list[str] = [
    *LITE,
    # editing
    "file_write_tool",
    "file_edit_tool",
    # web
    "browser_tool",
    "browser_render",
    # memory / knowledge
    "memory_add",
    "wiki_ingest",
    # git / github
    "git_status",
    "git_diff",
    "git_log",
    "git_commit",
    "github_create_pr",
    "github_get_file",
    "github_commit_file",
    # codebase intel + tests + review
    "index_codebase",
    "search_codebase",
    "run_tests",
    "get_diff",
    "review_diff",
    # builders
    "build_thing",
    "solve_task",
    "generate_image",
    # headless desktop (:99) — Phase 3
    "desktop_click",
    "desktop_type",
    "desktop_open_url",
    "desktop_find",
    "desktop_task",
    # task queue / goals
    "goal_add",
    # computer use + vision
    "mouse_move",
    "mouse_click",
    "keyboard_type",
    "keyboard_press",
    "find_on_screen",
    "open_app",
    "ask_about_image",
]

_SETS: dict[str, list[str] | None] = {
    "lite": LITE,
    "heavy": HEAVY,
    "full": None,  # None == everything
}

_warned: set[tuple[str, str]] = set()


def resolve_name(toolset: str | None) -> str:
    """Normalise a toolset name. None → $NEXUS_TOOLSET or 'heavy'. Unknown
    names warn once and fall back to the default instead of crashing."""
    name = (toolset or os.environ.get("NEXUS_TOOLSET") or DEFAULT_TOOLSET).strip().lower()
    if name not in _SETS:
        if ("set", name) not in _warned:
            _warned.add(("set", name))
            log.warning("unknown toolset %r — falling back to %r", name, DEFAULT_TOOLSET)
        name = DEFAULT_TOOLSET
    return name


def select(tools: Sequence, toolset: str | None = None,
           extra_names: Iterable[str] = ()) -> list:
    """Return the subset of `tools` (LangChain tool objects with `.name`)
    that belongs to `toolset`, preserving the original order. Names listed
    in the set but absent from `tools` log a warning once and are skipped.
    `extra_names` (e.g. MCP tools) are always included for heavy/full."""
    name = resolve_name(toolset)
    wanted = _SETS[name]
    if wanted is None:
        return list(tools)
    keep = set(wanted)
    if name != "lite":
        keep.update(extra_names)
    by_name = {getattr(t, "name", None): t for t in tools}
    for n in keep:
        if n not in by_name and ("tool", n) not in _warned:
            _warned.add(("tool", n))
            log.warning("toolset %r lists %r but no such tool is registered", name, n)
    return [t for t in tools if getattr(t, "name", None) in keep]


def names(toolset: str | None = None) -> list[str] | None:
    """The configured name list for a set (None for 'full')."""
    return _SETS[resolve_name(toolset)]

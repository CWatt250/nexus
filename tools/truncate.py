"""Tool-result truncation helper (Phase 13.7).

Big tool outputs (browser dumps, long terminal logs, raw repo greps) burn
context. This helper trims a string to roughly `max_tokens` tokens; if the
content is longer it asks qwen3:4b for a tight summary instead. The wrapper
`wrap_tools(tools, max_tokens)` retrofits an existing list of LangChain
`BaseTool` objects in place by replacing their sync/async callables.

Excluded by name from auto-summary (already small or structured): memory_*,
mem0_*, router_*, glob_tool, telegram_*, sparky_*. Edit `_SKIP_NAMES` to
tune.
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Iterable

import ollama

OLLAMA_URL = "http://localhost:11434"
SUMMARY_MODEL = "qwen3:4b"
DEFAULT_MAX_TOKENS = 500
CHARS_PER_TOKEN = 4  # rough; close enough for budget decisions

log = logging.getLogger("nexus.truncate")

_SKIP_NAMES = {
    "memory_search", "memory_add", "memory_list", "memory_delete", "memory_stats",
    "memory_dedup", "memory_compact",
    "mem0_add", "mem0_search",
    "router_telemetry", "router_stats",
    "glob_tool",
    "telegram_notify", "telegram_send_file",
    "post_state",
}


def _approx_tokens(s: str) -> int:
    return max(1, len(s) // CHARS_PER_TOKEN)


def _summarize(text: str, max_tokens: int) -> str:
    """Ask qwen3:4b for a `max_tokens`-budget summary. Falls back to a hard
    truncation if the model errors out so we never lose the call entirely."""
    prompt = (
        f"Summarize the tool output below in at most {max_tokens} tokens. "
        f"Preserve concrete file paths, error messages, line numbers, "
        f"command exit codes, and URLs verbatim. No preamble.\n\n"
        f"---\n{text}\n---\nSummary:"
    )
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=SUMMARY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            think=False,
            options={
                "temperature": 0.0,
                "num_predict": max_tokens + 50,
                "num_ctx": 8192,
            },
            keep_alive=-1,
        )
    except Exception as exc:
        log.warning("truncate summary failed (%s); falling back to hard cut", exc)
        return text[: max_tokens * CHARS_PER_TOKEN] + "\n…[hard-truncated]"
    content = ""
    if isinstance(resp, dict):
        content = (resp.get("message", {}) or {}).get("content", "") or ""
    else:
        msg = getattr(resp, "message", None)
        content = getattr(msg, "content", "") or ""
    return content.strip() or text[: max_tokens * CHARS_PER_TOKEN]


def truncate_tool_result(output, max_tokens: int = DEFAULT_MAX_TOKENS):
    """Trim a tool result string to ~max_tokens. Returns non-strings unchanged.

    Strategy: if length fits the budget, pass through. Otherwise ask qwen3:4b
    to summarize while preserving paths, errors, line numbers, commands.
    """
    if not isinstance(output, str):
        return output
    if not output:
        return output
    if _approx_tokens(output) <= max_tokens:
        return output
    summary = _summarize(output, max_tokens)
    return f"[truncated from ~{_approx_tokens(output)}t to ~{_approx_tokens(summary)}t via qwen3:4b]\n{summary}"


def wrap_tool(tool, max_tokens: int = DEFAULT_MAX_TOKENS):
    """Apply `truncate_tool_result` to a tool's sync and async callables.

    Mutates the tool in place and returns it for chaining. Skipped if the
    tool's name is in `_SKIP_NAMES` so already-bounded outputs don't pay
    for an unnecessary LLM round trip."""
    name = getattr(tool, "name", "")
    if name in _SKIP_NAMES:
        return tool

    func = getattr(tool, "func", None)
    if func is not None:
        original = func

        @wraps(original)
        def _sync(*args, **kwargs):
            return truncate_tool_result(original(*args, **kwargs), max_tokens)

        tool.func = _sync

    coro = getattr(tool, "coroutine", None)
    if coro is not None:
        original_async = coro

        @wraps(original_async)
        async def _async(*args, **kwargs):
            result = await original_async(*args, **kwargs)
            return truncate_tool_result(result, max_tokens)

        tool.coroutine = _async

    return tool


def wrap_tools(tools: Iterable, max_tokens: int = DEFAULT_MAX_TOKENS) -> list:
    """Wrap every tool in the iterable with the truncation helper. Returns
    the same list (mutated in place) so callers can chain."""
    out = list(tools)
    for t in out:
        wrap_tool(t, max_tokens=max_tokens)
    return out

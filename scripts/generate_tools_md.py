#!/usr/bin/env python3
"""Generate ~/AI_Agent/TOOLS.md from the live nexus.TOOLS registry.

Introspects every registered LangChain tool, groups by source module,
maps modules onto human-friendly categories, and writes a markdown
inventory. Used both at write-time (committed initial file) and by the
nexus-tools-refresh.timer (daily 04:00).

Run: ~/AI_Agent/venv/bin/python3 scripts/generate_tools_md.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import nexus  # noqa: E402

OUTPUT = ROOT / "TOOLS.md"

# Module → human-friendly category. Anything unmapped falls into "Other".
MODULE_CATEGORY: dict[str, str] = {
    "tools.terminal_tool":      "System & Shell",
    "tools.file_tool":          "File System",
    "tools.search_tool":        "File System",
    "tools.brave_search_tool":  "Web & Search",
    "tools.browser_tool":       "Web & Search",
    "tools.markitdown_tool":    "Web & Search",
    "tools.youtube_tool":       "Web & Search",
    "tools.github_tool":        "GitHub",
    "tools.rag_tool":           "Memory & RAG",
    "tools.mem0_tool":          "Memory & RAG",
    "tools.chroma_dedup":       "Memory & RAG",
    "tools.codebase_tool":      "Code & Dev",
    "tools.test_runner_tool":   "Code & Dev",
    "tools.diff_tool":          "Code & Dev",
    "tools.coding_agent":       "Code & Dev",
    "tools.tts_tool":           "Audio & Speech",
    "tools.whisper_tool":       "Audio & Speech",
    "tools.audio_gen_tool":     "Audio & Speech",
    "tools.bark_tool":          "Audio & Speech",
    "tools.image_gen_tool":     "Image & Game",
    "tools.opengame_tool":      "Image & Game",
    "tools.godot_tool":         "Image & Game",
    "tools.game_pipeline":      "Image & Game",
    "tools.computer_use_tool":  "Computer Use (mouse/keyboard/screen)",
    "tools.telegram_tool":      "Notifications",
    "tools.vercel_tool":        "Deployment",
    "tools.bidwatt_tool":       "BidWatt (read-only)",
    "tools.notion_sync":        "Knowledge Sync",
    "tools.obsidian_sync":      "Knowledge Sync",
    "tools.chat_history_import": "Knowledge Sync",
    "tools.glm_tool":           "External Models (escalation)",
    "tools.parallel_tools":     "Meta & Telemetry",
    "tools.router_telemetry":   "Meta & Telemetry",
    "tools.model_watcher":      "Meta & Telemetry",
    "tools.capabilities_tool":  "Meta & Telemetry",
}

# Per-category preamble — appears immediately under the heading. Use
# this for cross-cutting notes (auth requirements, dependencies) the
# per-tool docstrings can't capture.
CATEGORY_PREAMBLE: dict[str, str] = {
    "GitHub": (
        "Auth: token resolved from `~/AI_Agent/config/secrets.yaml` "
        "(`GITHUB_PAT`, fine-grained — preferred), then env vars, then "
        "`~/AI_Agent/.env` (`GITHUB_TOKEN`, classic). "
        "When no token is configured, falls back to anonymous PyGithub — "
        "**public repos only, ~60 req/h rate limit**. Run "
        "`github_auth_status()` to see who you're logged in as, what "
        "scopes the token has, and the current rate-limit budget."
    ),
}

# Latency tier per tool — drives the FAST/MEDIUM/SLOW tag in the inventory.
# FAST    : single API/HTTP call, <3 s typical, eligible for the QUERY_TOOL
#           lite_agent path (see tools/lite_agent_tools.py).
# MEDIUM  : 3–10 s typical (Playwright render, heavy doc conversion, OCR).
# SLOW    : >10 s typical OR multi-step / cloud / sub-agent (full TASK only).
# Anything not listed defaults to MEDIUM.
TOOL_TIER: dict[str, str] = {
    # FAST — eligible for QUERY_TOOL lite_agent
    "web_search": "FAST",
    "searxng_search": "FAST",
    "searxng_search_news": "FAST",
    "searxng_health": "FAST",
    "brave_search": "FAST",
    "brave_search_news": "FAST",
    "github_auth_status": "FAST",
    "github_list_repos": "FAST",
    "github_list_my_repos": "FAST",
    "github_list_issues": "FAST",
    "github_get_file": "FAST",
    "github_create_issue": "FAST",
    "github_create_pr": "FAST",
    "github_create_repo": "FAST",
    "github_commit_file": "FAST",
    "memory_search": "FAST",
    "memory_add": "FAST",
    "memory_list": "FAST",
    "memory_delete": "FAST",
    "memory_stats": "FAST",
    "mem0_add": "FAST",
    "mem0_search": "FAST",
    "file_read_tool": "FAST",
    "file_write_tool": "FAST",
    "file_edit_tool": "FAST",
    "glob_tool": "FAST",
    "grep_tool": "FAST",
    "router_telemetry": "FAST",
    "router_stats": "FAST",
    "get_current_time": "FAST",
    "get_capabilities": "FAST",
    "list_capabilities": "FAST",

    # SLOW — heavy work, full TASK only
    "browser_render": "MEDIUM",
    "browser_tool": "MEDIUM",
    "markitdown_tool": "MEDIUM",
    "youtube_transcript": "MEDIUM",
    "youtube_summary": "SLOW",
    "memory_dedup": "MEDIUM",
    "memory_compact": "MEDIUM",
    "whisper_record": "MEDIUM",
    "whisper_transcribe": "MEDIUM",
    "tts_speak": "MEDIUM",
    "tts_save": "MEDIUM",
    "terminal": "MEDIUM",  # depends entirely on the command
    "run_tests": "SLOW",
    "run_specific_test": "SLOW",
    "watch_tests": "SLOW",
    "review_diff": "SLOW",
    "approve_diff": "MEDIUM",
    "get_diff": "FAST",
    "index_codebase": "SLOW",
    "search_codebase": "FAST",
    "get_file_context": "FAST",
    "list_repo_structure": "FAST",
    "solve_task": "SLOW",
    "solve_coding_task": "SLOW",
    "audio_gen_speech": "SLOW",
    "audio_gen_music": "SLOW",
    "bark_voice": "SLOW",
    "image_generate": "SLOW",
    "opengame_create": "SLOW",
    "godot_run_export": "SLOW",
    "godot_create_project": "SLOW",
    "godot_run_headless": "SLOW",
    "vercel_deploy": "SLOW",
    "vercel_list_deployments": "FAST",
    "game_create": "SLOW",
    "glm_consult": "SLOW",
    "telegram_notify": "FAST",
    "telegram_send_file": "MEDIUM",
}


# Stable display order for categories.
CATEGORY_ORDER = [
    "Web & Search",
    "GitHub",
    "File System",
    "System & Shell",
    "Code & Dev",
    "Memory & RAG",
    "Computer Use (mouse/keyboard/screen)",
    "Audio & Speech",
    "Image & Game",
    "Knowledge Sync",
    "Deployment",
    "Notifications",
    "BidWatt (read-only)",
    "External Models (escalation)",
    "Meta & Telemetry",
    "Other",
]


def _signature(tool) -> str:
    """Build a 'name(arg1, arg2)' string from the tool's args_schema."""
    schema = getattr(tool, "args_schema", None)
    args: list[str] = []
    if schema is not None:
        try:
            fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", None)
            if fields:
                args = list(fields.keys())
        except Exception:
            pass
    return f"`{tool.name}({', '.join(args)})`"


def _description(tool) -> str:
    """First non-empty line of the tool's description, trimmed."""
    desc = getattr(tool, "description", "") or ""
    for line in desc.splitlines():
        line = line.strip()
        if line:
            return line[:160]
    return ""


def _tier(tool) -> str:
    """Return FAST / MEDIUM / SLOW for `tool`. Defaults to MEDIUM if
    the tool isn't in TOOL_TIER — not a hard error so newly-added tools
    don't break the generator."""
    return TOOL_TIER.get(tool.name, "MEDIUM")


def _category_for(tool) -> str:
    fn = getattr(tool, "func", None) or getattr(tool, "coroutine", None)
    mod = getattr(fn, "__module__", "") if fn else ""
    return MODULE_CATEGORY.get(mod, "Other")


def render() -> str:
    """Return the TOOLS.md body as a string."""
    by_cat: dict[str, list] = {}
    for t in nexus.TOOLS:
        by_cat.setdefault(_category_for(t), []).append(t)

    total = sum(len(v) for v in by_cat.values())
    out: list[str] = []
    out.append("# Nexus Tool Inventory")
    out.append("")
    out.append(f"_Last generated: {datetime.now().astimezone().isoformat(timespec='seconds')}_")
    out.append(f"_Total: {total} tools across {len(by_cat)} categories_")
    out.append("")
    out.append(
        "This file is auto-generated by `scripts/generate_tools_md.py`. "
        "Don't hand-edit — run the generator instead. Refreshed nightly by "
        "`nexus-tools-refresh.timer`."
    )
    out.append("")
    out.append(
        "**Latency tiers** — `[FAST]` = single API call, <3 s typical, "
        "eligible for the QUERY_TOOL `lite_agent` path; `[MEDIUM]` = 3–10 s "
        "typical (browser rendering, doc conversion); `[SLOW]` = >10 s or "
        "multi-step / cloud / sub-agent (full TASK only)."
    )
    out.append("")

    for cat in CATEGORY_ORDER:
        tools = by_cat.get(cat)
        if not tools:
            continue
        out.append(f"## {cat}")
        out.append("")
        preamble = CATEGORY_PREAMBLE.get(cat)
        if preamble:
            out.append(f"_{preamble}_")
            out.append("")
        for t in sorted(tools, key=lambda x: x.name):
            out.append(f"- `[{_tier(t)}]` {_signature(t)} — {_description(t)}")
        out.append("")

    # Surface any uncategorized tools so a stale module mapping is obvious.
    leftover = sorted({c for c in by_cat if c not in CATEGORY_ORDER})
    if leftover:
        out.append("> ⚠️  Uncategorized buckets present — extend `MODULE_CATEGORY`: "
                   + ", ".join(leftover))
        out.append("")

    return "\n".join(out)


def main() -> int:
    body = render()
    OUTPUT.write_text(body, encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(body)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

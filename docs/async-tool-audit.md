# Async Tool Audit (Phase 13.6)

_Date: 2026-04-27. Sample: nexus-core run-log.jsonl + grep of @tool decorators._

## Findings — top 10 tools by observable usage

Run-log only records `terminal`, `router`, `reflection`, and a few one-offs because most tools log to their own files. The "top 10" list below is the union of (run-log frequency) and (tools that are I/O heavy enough to matter for event-loop responsiveness).

| Rank | Tool | I/O type | Sync today? | Native async path | Notes |
|------|------|----------|-------------|-------------------|-------|
| 1 | `terminal` (sandbox.run_guarded) | subprocess | yes | **added** `safety.sandbox.run_guarded_async` (asyncio.create_subprocess_shell) | hottest tool by far (72/108 run-log entries) |
| 2 | `brave_search` / `brave_search_news` | http | yes (httpx.Client) | not yet — already uses httpx, swap to AsyncClient when an async tool wraps it | TIMEOUT=10s per request |
| 3 | `browser_tool` | playwright | yes | playwright has async API but cost of refactor > benefit; runs in thread pool | 1 call ≈ 5s, sparse |
| 4 | `memory_search` / `memory_add` | chromadb | yes | chromadb has no native async; ToolNode threads it | <50ms typical |
| 5 | `file_read_tool` / `file_write_tool` / `file_edit_tool` | disk | yes | aiofiles available; not converted (latency negligible) | one-shot ms-scale |
| 6 | `glob_tool` / `grep_tool` | disk | yes | as above | small dirs only |
| 7 | `github_*` | http (PyGithub) | yes | PyGithub is sync-only; threaded by ToolNode | rare |
| 8 | `youtube_transcript` / `youtube_summary` | http | **was** `requests`, now **httpx** | sync httpx.Client; can swap to AsyncClient later | converted in 13.6 |
| 9 | `generate_image` | http | **was** `requests`, now **httpx** | sync httpx.Client; can swap to AsyncClient later | converted in 13.6 |
| 10 | `markitdown_tool` | mixed | yes | runs sub-converters; cost is in the converter, not Python | rare |

## Decision

LangGraph's `ToolNode` already wraps sync callables with `asyncio.to_thread`, so sync tools don't actually block the event loop in async code paths — they just consume thread-pool slots. For Phase 13.6 we focused on the highest-leverage changes:

1. **`safety.sandbox.run_guarded_async`** — async sibling of the existing `run_guarded`, using `asyncio.create_subprocess_shell` and `asyncio.wait_for`. Same return shape. Available for async-aware code paths (Phase 15 task worker will use this).
2. **`requests` → `httpx`** in `youtube_tool.py` and `image_gen_tool.py`. `httpx` exposes both sync (`Client`) and async (`AsyncClient`) APIs from the same package, so any later promotion to native async is a one-line swap.
3. **No change** for the rest. They are either already async-compatible (`brave_search` uses `httpx`), already short-running (filesystem helpers, RAG), or have no async upstream (`PyGithub`, `chromadb`, sync libraries we don't own). Converting them would be churn without measurable wins.

## What is NOT done

- `aiofiles` for `file_tool` — disk reads on this machine are µs-scale; the threading overhead would dominate.
- `httpx.AsyncClient` for `brave_search` — would require turning the tool into an `async def`, which is fine, but not a Phase 13 priority. Defer to Phase 15 when the conversation handler / task worker split lands.
- `PyGithub` migration — no async fork worth adopting; sticking with thread offload.

## How to extend

When you add a new tool, default to `httpx` for HTTP and `asyncio.create_subprocess_shell` for shell-out. If the tool may run >1s and is on a hot path, write it as `async def` from the start.

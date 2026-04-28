# Nexus Build Changelog

## 2026-04-27 — Phase 14 (Reliability Scaffolding) starting

### 14.3 Task retrospective generator — DONE
- New `memory/retros.py` reads `task_metrics.jsonl` + `tool_metrics.jsonl` for a given `task_id`, asks qwen3:4b for 1-3 bullet lessons, and writes `memory/retros/retro_<task_id>.md` with goal / outcome / tool calls / wall time / tokens / lessons.
- "Interesting" filter: skips fast/no-tool-call/<5s/successful turns so trivial greetings don't bury the pile.
- `generate_retro_async(task_id)` fires on a daemon thread after `record_agent_turn` in both CLI and API paths.
- Verified on the smoke-test record `test-task-001`: file written under `memory/retros/`, lessons section populated.

### 14.2 Task metrics logging — DONE
- New `memory/metrics.py` writes two append-only JSONL streams:
  - `memory/task_metrics.jsonl` — one record per agent turn (task_id, wall_seconds, route, model, tokens_in/out, tool_calls, success, error, previews).
  - `memory/tool_metrics.jsonl` — one record per tool call (task_id, tool, latency_ms, tokens_in/out, success, error).
- `wrap_tools_with_metrics(TOOLS)` retrofits every registered tool. Idempotent (won't double-wrap). Threads attribution via thread-local `task_context`.
- Wired into both transports:
  - CLI (`nexus.interactive_loop`) wraps the streaming block with `task_context(uuid)`, counts ToolMessages from final state, records the turn even on error.
  - API (`nexus_api.chat_completions`) does the same for both streaming and non-streaming paths.
- All writes are best-effort — log failures never break the agent. Verified end-to-end: `tool_metrics.jsonl` and `task_metrics.jsonl` both got entries from the smoke test.

### 14.1 Dry-run mode for destructive tools — DONE
- New `safety/destructive.py` with `is_destructive(cmd) -> (bool, reason)`, `needs_approval`, `strip_approval`, `dry_run_summary`. Patterns cover: git force-push / reset --hard / clean -fdx / branch -D / rebase --root / filter-*, SQL DROP/TRUNCATE/DELETE-without-WHERE, rm -r, mv to /dev/null, redirects to /dev/sd*, docker prune --all, kubectl delete, supabase db reset, npm publish, vercel remove, etc.
- `safety.sandbox.run_guarded` and `run_guarded_async` both now default `dry_run=True`. When the command matches a destructive pattern and lacks an `APPROVED:` prefix, they return a dry-run summary instead of executing. `APPROVED:` prefix is stripped before the command reaches the shell.
- `tools/github_tool.github_commit_file` and `tools/vercel_tool.vercel_deploy` gained an `approve=False` arg that returns a dry-run preview by default. Model must explicitly set `approve=True` to push or deploy.
- Existing hard guardrails (rm -rf, mkfs, etc.) still block at the earlier layer — destructive.py is the *softer* tier above it.
- Smoke-tested: `git reset --hard HEAD~1` now returns `DRY-RUN: not executed`, `APPROVED: echo` runs, plain `echo` runs, `rm -rf /tmp/test` still hard-blocked. `github_commit_file` and `vercel_deploy` show preview without `approve=True`.

## 2026-04-27 — Phase 13 (Speed Layer) starting

### 13.9 Phase 13 verification — PASS
- New `scripts/bench_phase13.py` measures TTF over 10 prompts in two passes:
  - **cold**: model evicted (`keep_alive=0`) before each call → simulates pre-Phase-13 baseline.
  - **warm**: router pinned via prewarm + `KEEP_ALIVE=-1`.
- Result (`PHASE_13_VERIFY.md`):
  - mean TTF: **531.5 ms cold → 91.4 ms warm** = **82.8% reduction**
  - median TTF: **527.7 ms cold → 80.1 ms warm** = **84.8% reduction**
- Exit criterion (≥50% TTF reduction): **PASS**.
- Live `nexus-api.service` still runs the pre-Phase-13 binary; restart line added to `SUDO_COMMANDS_R3.sh` for Colton to pick up the new code.

### 13.8 Instant acknowledgment pattern — DONE
- New helpers in `tools/sparky_state.py`:
  - `looks_long_running(message, route)`: heuristic — heavy/code/design route, long message, or imperative verbs (build/implement/refactor/...).
  - `post_bubble(text)`: fire-and-forget POST to `:11437/message` on a daemon thread.
  - `instant_ack(message, route)`: picks one of five pre-baked acks ("On it.", "Got it, starting now.", ...) and pushes it to the Sparky bubble. Returns `None` when no ack is warranted.
- Wired into `nexus_api.chat_completions` (before agent.astream begins) and `nexus.interactive_loop` (after the router prints).
- Latency measured at 1ms — well under the 200ms budget. No LLM call.

### 13.7 Tool result truncation helper — DONE
- New `tools/truncate.py` with `truncate_tool_result(output, max_tokens=500)` and `wrap_tool / wrap_tools` retrofitters. Outputs ≤500t pass through; longer outputs are summarised by qwen3:4b with a prompt that preserves paths, error messages, line numbers, and exit codes verbatim.
- `nexus.TOOLS` is run through `wrap_tools(TOOLS, max_tokens=500)` after assembly. Skip list excludes already-bounded tools (memory_*, mem0_*, router_*, glob_tool, telegram_*).
- Pre-existing bug exposed and fixed in `tools/terminal_tool.py` — `run_guarded` was referenced but never imported. Added `from safety.sandbox import run_guarded`.
- Verified: short terminal output passes through; `yes | head -n 1000` (~3500t) collapses to ~540t with the `[truncated from ~3505t to ~538t via qwen3:4b]` header.

### 13.6 Async tool audit (top 10) — DONE
- New `docs/async-tool-audit.md` with the full ranking + decisions. Run-log shows `terminal` is the hottest tool by far (72/108 entries).
- New `safety.sandbox.run_guarded_async` — async sibling of `run_guarded` using `asyncio.create_subprocess_shell` + `asyncio.wait_for`. Same return shape. Phase 15 worker will use this.
- `tools/youtube_tool.py` and `tools/image_gen_tool.py`: migrated `requests` → `httpx` so they expose both sync and async paths from the same package.
- Skipped (with reasons in the audit): aiofiles for file_tool, AsyncClient for brave_search, PyGithub migration. ToolNode already offloads sync tools to a thread pool, so the rest is churn without wins.

### 13.5 Parallel tool execution — DONE
- New `tools/parallel_tools.py` exposes three composite tools that run paired lookups in a `ThreadPoolExecutor` for guaranteed parallelism:
  - `quick_lookup(query)` → `brave_search` + `memory_search`
  - `repo_inspect(file_path, repo_path)` → `get_file_context` + `git log`
  - `screen_clip()` → xclip + scrot screenshot
- Registered in `nexus.TOOLS` (now 78 total). Tool hint nudges the model to batch independent calls in one assistant turn (LangGraph ToolNode already runs batched tool_calls in parallel).
- Smoke-tested: `quick_lookup` 0.08s, `repo_inspect` 0.02s.

### 13.4 fast_mode flag — DONE
- New `nexus.FAST_MODE_INSTRUCTION` + `is_fast_route(route)` + `fast_mode_messages(user, route, override)`.
- Router's existing `fast` route triggers fast mode automatically. Caller can also force it with `override=True/False`.
- API path (`nexus_api.chat_completions`) and CLI loop (`nexus.interactive_loop`) both now build their input messages with `fast_mode_messages`. CLI prints `[router: fast → qwen3:4b FAST]` when fast mode is active.
- Strip-think is already handled downstream by `ThinkStripper`, so fast-mode replies stay clean even if the model leaks `<think>` blocks.

### 13.3 Streaming everywhere — DONE
- API path (`nexus_api._stream_agent`) was already streaming via `agent.astream(stream_mode='messages')`. Verified.
- CLI path (`nexus.interactive_loop`): converted from blocking `agent.invoke` to `agent.stream(stream_mode='messages')`. Tokens print as they arrive, ThinkStripper drops `<think>` blocks on the fly. Smoke test passed.
- Sparky overlay already has a typewriter render in `sparky/overlay/index.html` (lines 513-532) — no change needed.
- Telegram streaming intentionally deferred to Phase 16.1 per Rule 10 (Telegram disabled until Phase 15 verified).
- Router is intentionally non-streaming — it returns a 64-token JSON object; streaming would just add latency.

### 13.2 Prompt caching via static prefix — DONE
- `nexus.load_static_prefix()` returns SOUL.md + STYLE.md + tool hint + NEXUS.md, cached at module level so it hashes byte-stable every call (verified: 11787c, sha256 stable across calls).
- `nexus.load_dynamic_suffix()` returns the volatile tail: lessons + project ctx.
- `load_system_prompt()` composes `[STATIC][DYNAMIC]` and logs `[prompt] static=Xc/~Yt dynamic=Xc/~Yt` to stderr at startup.
- Deviation from spec: CLAUDE.md is the autonomous-build playbook for Claude Code, not Nexus's identity. Including it would rewrite Nexus's persona on every turn and bloat the cache. Used STYLE.md instead — the user-facing style guide is the analogue.

### 13.1 KEEP_ALIVE=-1 + prewarm service — DONE
- New `tools/prewarm.py`: pins router (`qwen3:4b`) with `keep_alive=-1`, warms heavy (`qwen3.6`) with 30m. Tested: router 0.7s, heavy 2.55s.
- New `/tmp/nexus-prewarm.service` (oneshot, After=ollama+nexus-api). Sudo install lines added to `SUDO_COMMANDS_R3.sh`.
- `router.py:classify` now passes `keep_alive=-1` so per-call requests don't override the pin.
- Verified `GET /api/ps` shows qwen3:4b expires 2318 (effectively never).



## 2026-04-21 — Phase 2 Complete (Session 2)

### Completed
- **RAG introspection tools**: `memory_list`, `memory_delete`, `memory_stats`
- **Chroma dedup utility**: `memory_dedup`, `memory_compact` in new tools/chroma_dedup.py
- **Mem0 reflection sink**: High-quality lessons (quality >= 4) now stored in Mem0
- **Router telemetry dashboard**: `router_telemetry`, `router_stats` in new tools/router_telemetry.py

### Remaining (needs user)
- Install nomic-embed-text: `ollama pull nomic-embed-text`

### Tool Count
- **64 tools** (was 57)

### Files Modified
- tools/rag_tool.py
- tools/chroma_dedup.py (new)
- tools/router_telemetry.py (new)
- reflection.py
- nexus.py

---

## 2026-04-21 — Session Start

### Status Assessment
**Completed:**
- Phase 4.1: Whisper STT (tools/whisper_tool.py)
- Phase 4.2: Kokoro TTS (tools/tts_tool.py)
- Phase 4.3: Voice Loop (voice_loop.py)
- Phase 5.1: Brave Search Tool (tools/brave_search_tool.py)
- Phase 5.3: Nexus Chronicle (tools/chronicle.py)
- Phase 6.2: Context Compression (tools/context_compressor.py)
- Phase 6.3: Pattern Analyzer (memory/patterns.py)

**In Progress:**
- Phase 10: Game Development Studio — STARTING NOW

**Just Completed:**
- Phase 5.2: YouTube Transcript Tool
- Phase 6.1: Telegram Bot Integration
- Phase 7: Computer Use & Media (all 4 tasks)
- Phase 8: Sparky Avatar System (all tasks)
- Phase 9: Multi-Agent Swarms:
  - agents/base_agent.py
  - agents/orchestrator.py
  - agents/coder_agent.py
  - agents/researcher_agent.py
  - agents/builder_agent.py
  - agents/designer_agent.py
  - /agents endpoint in nexus_api.py
- Nexus now has 46 tools + 4 sub-agents!

**Pending:**
- Phase 7: Computer Use & Media (all tasks)
- Phase 8: Sparky Avatar System (all tasks)
- Phase 9: Multi-Agent Swarms (all tasks)
- Phase 10: Game Development Studio (all tasks)
- Final Tasks (F1-F6)

---


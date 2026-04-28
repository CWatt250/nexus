# Nexus Build Changelog

## 2026-04-27 — Phase 13 (Speed Layer) starting

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


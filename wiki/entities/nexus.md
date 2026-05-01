---
name: Nexus
description: Local-first AI agent stack running on WattBott. LangGraph + Ollama + qwen3.6 with a 75+ tool belt.
type: entity
last_updated: 2026-05-01
sources: []
tags: [project, agent, langgraph, ollama, self]
---

# Nexus

The agent stack itself. Lives at `~/AI_Agent/` on [WattBott](colton.md).

## Architecture
- **Core**: LangGraph + LangChain over local Ollama. Heavy model is `qwen3.6:35b-a3b`, router/quick-chat is `qwen3:4b`.
- **Entry points**:
  - `nexus.py` — CLI agent (sync path).
  - `nexus_api.py` — OpenAI-compatible HTTP API on port 11435 (async path, used by Open WebUI and the dashboard).
  - `nexus_design.py` — Design Studio on port 11436.
  - `mcp/server.py` — MCP server exposing the same tool belt to other agents.
- **Persistence**: `memory/checkpoints.db` (LangGraph SqliteSaver in WAL mode, shared between sync + async via `aiosqlite`), `chroma/` (long-term RAG), `memory/tasks.db` (Phase 15 task queue).
- **Identity prompt**: `SOUL.md` + `STYLE.md` + `TOOLS.md` + `NEXUS.md` + `LESSONS.md` + `wiki/index.md` are concatenated into the static prefix that Ollama caches per turn.

## Services (systemd)
- `nexus-api` — FastAPI on :11435
- `nexus-design` — Design Studio on :11436
- `nexus-task-worker` — Phase 15 background task runner
- `nexus-cc-dispatcher` — Phase 22 Claude Code dispatch watcher
- `nexus-cc-reporter` — surfaces dispatch results to Telegram + dashboard
- `nexus-telegram` — two-way Telegram listener
- `nexus-prewarm`, `nexus-chronicle`, `nexus-wakeword`, `nexus-perf-guardian`, others — see `SERVICES.md`

## Phases
- See [decisions/](../decisions/) for the running phase log.
- Currently in Phase 25 — Knowledge Garden (this wiki).

## Tool belt
- **System**: terminal, file_*, glob_tool, grep_tool, codebase_tool, test_runner, diff_tool
- **Web**: brave_search, web_fetch, browser_tool, browser_render, markitdown, searxng
- **Memory**: rag_tool (memory_*), mem0, **wiki_query / wiki_ingest / wiki_update / wiki_create** (Phase 25)
- **Coding**: coding_agent.solve_coding_task, scaffold_tool, github_tool
- **Voice**: whisper_record/transcribe, tts_speak/save (Kokoro)
- **Computer use**: screenshot, mouse, keyboard, find_on_screen
- **Dispatch**: cc_dispatch_tool (Phase 22 — escalate to Claude Code)
- **Escalation**: glm_consult (Z.ai GLM-5.1, $50/mo cap)

## Related
- [Dispatch system](../concepts/dispatch-system.md) — Phase 22
- [Intent routing](../concepts/intent-routing.md) — CHAT/QUERY/TASK/STATUS classifier
- [Scaffolding recipes](../concepts/scaffolding-recipes.md) — Phase 23.1
- [LLM wiki pattern](../concepts/llm-wiki-pattern.md) — the pattern this garden implements

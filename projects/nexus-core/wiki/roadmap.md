# Nexus Core — Roadmap

_Last updated: 2026-04-21 (later in the day)_

## Phase 0 — Foundation (DONE, earlier in the project)
- LangGraph ReAct agent wired to local Ollama.
- Chroma-backed RAG memory (`tools/rag_tool.py`).
- Tool belt: terminal, file r/w/edit, glob, grep, browser (Playwright).
- OpenAI-compatible FastAPI at `:11435` for Open WebUI.
- Design studio service (`nexus_design.py`) at its own port.
- Session/checkpointing via LangGraph SqliteSaver.
- Router (`router.py`) multi-model dispatch over `models.json`.
- Auto-reflection pipeline (`reflection.py`) with run-log + lessons/improvements.
- Auto-commit chain (`git_sync.py`) after every turn.
- Systemd services: `nexus-agent`, `nexus-api`, `nexus-design`.

## Phase 1 — 2026-04-20 — SHIPPED
- **Karpathy coding principles** appended to workspace CLAUDE.md and `~/Dev/cwatt-bidboard/CLAUDE.md`.
- **Thinking-token leak fix**: `strip_thinking()` + streaming `ThinkStripper` in `nexus.py`; applied to CLI, API non-streaming + SSE stream, and pre-reflection.
- **Reflection wired into `nexus_api.py`**: `_spawn_reflection()` runs after every `/v1/chat/completions` turn in a background thread, chained to `git_sync.auto_commit()`.
- **MarkItDown tool** (`tools/markitdown_tool.py`): local file / URL → markdown → Chroma RAG.
- **Mem0 tool** (`tools/mem0_tool.py`): local `mem0_add` / `mem0_search` with Ollama (`qwen3:4b`) + Chroma + HF MiniLM embeddings.
- **File watcher** (`tools/file_watcher.py`): auto-ingest Downloads + Documents PDF/Word/Excel/PPT. Unit staged.
- **Clipboard watcher** (`tools/clipboard_watcher.py`): xclip poll → `clipboard-log.md` + RAG. Unit staged.

## Phase 2 — Next up (locked, short horizon)
- **Install nomic-embed-text in Ollama** and re-test Mem0 with a pure-Ollama embedder (drop HF sentence-transformers).
- **Chroma compaction / dedup job** — remove near-duplicate chunks (esp. clipboard noise).
- **RAG introspection command** — `memory list [tag]` / `memory delete <id>` for curation.
- **Mem0 as reflection sink** — pipe `reflection.reflect()` lessons into `mem0_add`.
- **Router telemetry dashboard** — route mix / quality histogram / time-saved totals from `run-log.jsonl`.

## Phase 3 — MCP Server Support — 2026-04-21 — SHIPPED
- **MCP Python SDK** (`mcp==1.8.1`) installed in the venv. `anthropic-mcp` doesn't exist on PyPI and was skipped.
- **`mcp/server.py`** — stdio MCP server exposing all 19 native Nexus tools (terminal, file r/w/edit, glob, grep, browser, memory_search/add, markitdown, mem0_add/search, the 7 GitHub tools) to any MCP client. Run with `python3 ~/AI_Agent/mcp/server.py`.
- **`mcp/client.py`** — loads every enabled entry in `servers.json`, spawns each server in a persistent asyncio-loop thread, wraps each discovered MCP tool as a LangChain `StructuredTool` (name prefixed `<server>__<tool>`), appends them to `TOOLS` before the agent is built. Name collision with the pip SDK solved by keeping `~/AI_Agent/mcp/` as a plain directory (no `__init__.py`).
- **`mcp/servers.json`** — config format `{servers: [{name, command, env, enabled, skip_if_missing_env}]}`.
- **`markitdown-mcp`** installed and configured as the first active MCP server. Loads `markitdown__convert_to_markdown`.
- **`@modelcontextprotocol/server-github`** configured via `npx -y` (no global install needed); auto-skipped until `GITHUB_TOKEN` is set in the server's env block.
- Wired via `nexus.extend_tools_with_mcp()`, called from both `nexus.main()` and `nexus_api.py` startup before `build_agent`.

## Phase 4 — Safety Layer — 2026-04-21 — SHIPPED
- `safety/guardrails.py` — blacklist (`rm -rf`, `mkfs`, `dd if=`, fork bomb, `/etc/passwd`/`shadow`/`sudoers`, `/boot`, `chmod -R 777 /`, `chown -R`, `mv /*`), `check_command`, token-usage log, sliding-window rate limiter, 60-second max-exec.
- `safety/sandbox.py` — single terminal execution path. `terminal_tool.py` now routes through it.
- `safety/circuit_breaker.py` — per-tool loop detection (>10 calls / 60s), 8 GB RAM watch w/ auto-restart, 30-min Ollama resident watch.
- `safety/watchdog.py` + `nexus-watchdog.service` — 30s polling of nexus-agent/api/design, open-webui, open-terminal, ollama; restart + `notify-send` + watchdog log.
- `SOUL.md` safety section — ask before system-file edits, deletes, external network calls.

## Phase 5 — GitHub + Git Activity — 2026-04-21 — SHIPPED
- **PyGithub** installed. `tools/github_tool.py` exposes 7 tools: `github_create_repo`, `github_list_repos`, `github_create_issue`, `github_list_issues`, `github_create_pr`, `github_get_file`, `github_commit_file`. Auth reads `GITHUB_TOKEN` from env or `~/AI_Agent/.env`.
- **`~/AI_Agent/.env.example`** — GITHUB_TOKEN, Z_AI_API_KEY, BRAVE_SEARCH_API_KEY, TAILSCALE_API_KEY placeholders.
- **GitHub MCP server** configured in `servers.json` (auto-skipped until token set). Global npm install requires sudo (`sudo npm install -g @modelcontextprotocol/server-github`); `npx -y` works without it.
- **`tools/git_watcher.py`** + **`nexus-git-watcher.service`** — every 60s, walks `~/Dev` and `~/AI_Agent` up to depth 3, detects new commits, writes JSONL to `memory/git-activity.log`, stores commit summary in Chroma RAG tagged `git_activity`.

## Phase 6 — Voice, Web Search, Chronicle, Compression, Soul upgrade — 2026-04-21 — SHIPPED
- **Voice stack**:
  - `tools/whisper_tool.py` — faster-whisper `base`, `record_and_transcribe` (silence-stops, 30s cap) + `transcribe_file`. Model cached under `models/whisper/`. LangGraph tools: `whisper_record`, `whisper_transcribe`.
  - `tools/tts_tool.py` — Kokoro-82M (`kokoro-onnx`), `speak` + `save_audio`, default voice `af_heart`. Model + voice bundle auto-downloaded into `models/kokoro/`. LangGraph tools: `tts_speak`, `tts_save`.
  - `voice_loop.py` — press-Enter-to-record interactive voice assistant; whisper → agent → Kokoro. Run with `python3 ~/AI_Agent/voice_loop.py`.
  - `sounddevice` requires `libportaudio2` (added to `/tmp/nexus-chronicle-apt.sh`).
- **Brave Search** — `tools/brave_search_tool.py` (`brave_search`, `brave_search_news`). Reads `BRAVE_SEARCH_API_KEY` from `.env`; when missing returns "Add BRAVE_SEARCH_API_KEY to ~/AI_Agent/.env to enable web search".
- **Chronicle** — `tools/chronicle.py` + `nexus-chronicle.service`. Every 5 min: scrot → tesseract OCR → qwen3:4b summary → `memory/chronicle/YYYY-MM-DD.md` + RAG `tag=chronicle`. Skips on lock / missing `DISPLAY` / OCR < 50 chars.
- **Context compression** — `tools/context_compressor.py`. Every 10 CLI turns, qwen3:4b produces a ~500-token summary; LangGraph checkpoint is rewritten via `RemoveMessage` + injected `SystemMessage`. Logs to `memory/compression-log.md`. Wired into `interactive_loop`.
- **Pattern analyzer v2** — `memory/patterns.py` now tracks peak hour + quality trend (first half vs second half of window), top GitHub repos, hot read/write files, and git-activity commits per repo. Emits both `memory/patterns.md` (full) and `memory/weekly-digest.md` (condensed). `nexus-patterns.timer` runs it every Monday at 06:00 local.
- **SOUL.md** — rewritten: identity + autonomy push, "never say I can't", WattBott / Irex Argus / BidWatt context, full tool belt, when-to-use cheatsheet, safety rules carried forward.

## Phase 7 — Next up (locked)
- **GLM-5.1 code-tier route** — add Z.AI / GLM-5.1 as an alternate for the `code` route; key via `Z_AI_API_KEY`.
- **Tailscale integration** — remote-device visibility via `TAILSCALE_API_KEY`.
- **Design Studio v2** — keep visual design on its own model, wire output artifacts into Chroma RAG automatically.
- **BidWatt integration** — dedicated tools for scraping/bidding pipeline; route `bid/*` keywords through a specialist model.
- **Proactive ingestion expansion** — email-drop watcher once Gmail MCP auth is set up; mirror the file-watcher pattern.
- **Open WebUI tool bridge** — surface the full LangGraph tool catalog as function-calling options.
- **Hardware-aware model routing** — use ROCm GPU availability/utilization as a router signal.
- **Nomic-embed-text in Ollama** and drop HF sentence-transformers in Mem0.

## Phase 8 — Stretch (unlocked, explored only)
- **Fine-tuning loop** — use reflection-tagged exchanges (quality=5/1) to build SFT/DPO pairs for a local qwen3 variant.
- **Multi-agent planning** — design↔code↔critic loop for larger tasks.
- **Voice in/out** — whisper.cpp + piper wired into the API.

## Services (current)
| Service | Status | Purpose |
| --- | --- | --- |
| `nexus-agent` | running (restart pending for Phase 3+5+6) | CLI/daemon LangGraph agent |
| `nexus-api` | running (restart pending for Phase 3+5+6) | OpenAI-compatible API for Open WebUI |
| `nexus-design` | running | Design Studio |
| `nexus-file-watcher` | pending install | Auto-ingest Downloads/Documents |
| `nexus-clipboard-watcher` | pending install | Auto-ingest clipboard |
| `nexus-watchdog` | pending install | Service monitor + RAM/Ollama watchdog |
| `nexus-git-watcher` | pending install | Commit watcher → git-activity.log + RAG |
| `nexus-chronicle` | pending install | Screen → OCR → qwen3:4b summary → RAG |
| `nexus-patterns.timer` | pending install | Weekly digest Mon 06:00 |

## Runtime data paths
- Chroma RAG: `~/AI_Agent/chroma/`
- Mem0 store: `~/AI_Agent/memory/mem0/`
- Clipboard log: `~/AI_Agent/memory/clipboard-log.md`
- File-watcher state: `~/AI_Agent/memory/file_watcher_seen.txt`
- Git-watcher state: `~/AI_Agent/memory/git_watcher_seen.json`
- Git-activity log: `~/AI_Agent/memory/git-activity.log`
- Watchdog log: `~/AI_Agent/memory/watchdog.log`
- Blocked-command log: `~/AI_Agent/memory/blocked-commands.log`
- Token-usage log: `~/AI_Agent/memory/token-usage.log`
- Chronicle pages: `~/AI_Agent/memory/chronicle/YYYY-MM-DD.md`
- Compression log: `~/AI_Agent/memory/compression-log.md`
- Compression state: `~/AI_Agent/memory/compression-state.json`
- Weekly digest: `~/AI_Agent/memory/weekly-digest.md`
- Patterns report: `~/AI_Agent/memory/patterns.md`
- Whisper model cache: `~/AI_Agent/models/whisper/`
- Kokoro model cache: `~/AI_Agent/models/kokoro/`
- Reflection run log: `~/AI_Agent/projects/nexus-core/run-log.jsonl`
- MCP server config: `~/AI_Agent/mcp/servers.json`
- Secrets: `~/AI_Agent/.env` (template in `.env.example`)

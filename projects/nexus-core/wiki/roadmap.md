# Nexus Core — Roadmap

_Last updated: 2026-04-20_

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

## Phase 1 — Today (2026-04-20) — SHIPPED
- **Karpathy coding principles** appended to workspace CLAUDE.md and `~/Dev/cwatt-bidboard/CLAUDE.md`.
- **Thinking-token leak fix**: `strip_thinking()` + streaming `ThinkStripper` in `nexus.py`; applied in CLI, non-streaming API reply, API SSE stream, and pre-reflection.
- **Reflection wired into `nexus_api.py`**: `_spawn_reflection()` now runs after every `/v1/chat/completions` turn (both streamed and non-streamed) in a background thread, chained to `git_sync.auto_commit()` just like the CLI.
- **MarkItDown** installed into `~/AI_Agent/venv`; new `tools/markitdown_tool.py` LangGraph tool: accepts local file or URL, converts to markdown, stashes in Chroma RAG with source metadata. Registered in `nexus.py` TOOLS.
- **Mem0** installed locally (no cloud); new `tools/mem0_tool.py` exposes `mem0_add` / `mem0_search` tools. Uses Ollama (`qwen3:4b`) as extractor LLM and Chroma (separate `nexus-mem0` collection under `memory/mem0/chroma`) as vector store with HF sentence-transformers embeddings. Registered in `nexus.py` TOOLS.
- **File watcher daemon** (`tools/file_watcher.py`): polls `~/Downloads` and `~/Documents` for new PDF/Word/Excel/PPT, waits for the write to settle, runs MarkItDown, adds to Chroma RAG. Service unit `nexus-file-watcher.service` (staged in `/tmp`).
- **Clipboard watcher daemon** (`tools/clipboard_watcher.py`): polls xclip every 5s, captures text ≥20 chars, appends to `memory/clipboard-log.md`, stashes in Chroma RAG tagged `clipboard`. Service unit `nexus-clipboard-watcher.service` (staged in `/tmp`).

## Phase 2 — Next up (locked, short horizon)
- **Install nomic-embed-text in Ollama** and re-test Mem0 with a local embedder instead of HF sentence-transformers (so Mem0 depends only on Ollama).
- **Chroma compaction / dedup job**: scheduled task that removes near-duplicate chunks (esp. clipboard noise) and keeps the collection tight.
- **RAG introspection command** in the CLI: `memory list [tag]` / `memory delete <id>` for curation.
- **Mem0 as reflection sink**: pipe `reflection.reflect()` lessons into `mem0_add` so long-term facts don't only live in `lessons.md`.
- **Router telemetry dashboard** pulling from `run-log.jsonl` — route mix, quality histogram, time-saved totals.

## Phase 3 — Planning session commitments (locked)
- **Design Studio v2**: keep visual design on its own model, wire output artifacts into Chroma RAG automatically.
- **BidWatt integration**: dedicated tools that talk to the BidWatt scraping/bidding pipeline; route `bid/*` keywords through a specialist model.
- **Proactive ingestion expansion**: add an email-drop watcher once Gmail MCP auth is set up; mirror the file-watcher pattern.
- **Open WebUI tool bridge**: surface the LangGraph tool catalog to Open WebUI users as function-calling options.
- **Hardware-aware model routing**: use ROCm GPU availability/utilization as a router signal — fall back to smaller models under load.

## Phase 4 — Stretch (unlocked, explored only)
- **Fine-tuning loop**: use reflection-tagged exchanges (quality=5 and quality=1) to build SFT/DPO pairs for a local qwen3 variant.
- **Multi-agent planning**: orchestrate a design↔code↔critic loop for larger tasks instead of single-model completions.
- **Voice in/out**: whisper.cpp + piper wired into the same API so Nexus can respond via audio.

## Services (current)
| Service | Status | Purpose |
| --- | --- | --- |
| `nexus-agent` | running | CLI/daemon LangGraph agent |
| `nexus-api` | running | OpenAI-compatible API (Open WebUI) |
| `nexus-design` | running | Design Studio |
| `nexus-file-watcher` | **pending install** | Auto-ingest Downloads/Documents |
| `nexus-clipboard-watcher` | **pending install** | Auto-ingest clipboard |

## Runtime data paths
- Chroma RAG: `~/AI_Agent/chroma/`
- Mem0 store: `~/AI_Agent/memory/mem0/`
- Clipboard log: `~/AI_Agent/memory/clipboard-log.md`
- File-watcher state: `~/AI_Agent/memory/file_watcher_seen.txt`
- Reflection run log: `~/AI_Agent/projects/nexus-core/run-log.jsonl`

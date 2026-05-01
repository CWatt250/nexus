# tasks

Open work for nexus-core. Newest-first within each section. Keep items short; link deeper context into `decisions.md` or `scratchpad.md`.

## now
- [x] **Tool execution layer** — wired `tools/terminal_tool.py` into the LangGraph loop; uses `tools/run_log.py` centralized helper; sandbox guardrails + 30s timeout enforced.
- [x] **Chroma RAG setup** — `chromadb` installed in venv; `rag_tool.py` switched to Ollama `nomic-embed-text` for embeddings (no sentence-transformers needed); `nexus-memory` collection bootstrapped with 259 chunks from SOUL.md, STYLE.md, CLAUDE.md, NEXUS.md, and all wiki docs.
- [x] **Run log system** — `tools/run_log.py` created as centralized append helper; `terminal_tool.py` migrated to use it; all other tools can import and use `log_run()`.

## next
- [x] **Multi-model routing** — `tools/model_router.py` created with heuristic + Ollama classification. Routes: fast, mid, heavy, code, design. Model resolution via `models.json` with fallback defaults.
- [x] **Reflection pipeline** — `tools/session_reflection.py` created. `run_reflection(n)` reads last N runs from run-log, asks qwen3:4b for insights, writes lessons to wiki and deduplicates. `auto_reflect_threshold(n)` enables auto-trigger.
- [x] **Phone access via Open WebUI** — `wiki/phone-access.md` created with 3 options (bind-all, API key auth, SSH tunnel), firewall config, Open WebUI setup steps, and security notes.

## later
- [ ] Checkpointed conversation state via `langgraph-checkpoint-sqlite` so sessions survive restarts.
- [ ] Browser/automation tool using playwright (already installed); scope to read-only research first.
- [ ] Tool permission model — allow/deny list per tool, confirmation for destructive commands.
- [ ] Health + status endpoint for the systemd service.

## done
- [x] Systemd service `nexus-agent` auto-starting on boot.
- [x] Basic LangGraph agent talking to local Ollama (`qwen3.6`).
- [x] Workspace scaffold: SOUL.md, STYLE.md, CLAUDE.md, new-project.sh.

# tasks

Open work for nexus-core. Newest-first within each section. Keep items short; link deeper context into `decisions.md` or `scratchpad.md`.

## now
- [x] **Tool execution layer** — wired `tools/terminal_tool.py` into the LangGraph loop; uses `tools/run_log.py` centralized helper; sandbox guardrails + 30s timeout enforced.
- [x] **Chroma RAG setup** — `chromadb` installed in venv; `rag_tool.py` switched to Ollama `nomic-embed-text` for embeddings (no sentence-transformers needed); `nexus-memory` collection bootstrapped with 259 chunks from SOUL.md, STYLE.md, CLAUDE.md, NEXUS.md, and all wiki docs.
- [x] **Run log system** — `tools/run_log.py` created as centralized append helper; `terminal_tool.py` migrated to use it; all other tools can import and use `log_run()`.

## next
- [ ] **Multi-model routing** — cheap/fast model for trivial turns (qwen3:14b or similar), heavy model (qwen3.6) for reasoning/tool use. Decide routing heuristic: message length, tool-intent detection, or a tiny classifier pass.
- [ ] **Reflection pipeline** — after each session (or N turns), summarize what happened, pull lessons into `wiki/lessons-learned.md`, and write embeddings into `nexus-memory` so future sessions recall them.
- [ ] **Phone access via Open WebUI** — expose nexus through Open WebUI so it's reachable from the phone on the home network. Confirm auth story before opening beyond localhost.

## later
- [ ] Checkpointed conversation state via `langgraph-checkpoint-sqlite` so sessions survive restarts.
- [ ] Browser/automation tool using playwright (already installed); scope to read-only research first.
- [ ] Tool permission model — allow/deny list per tool, confirmation for destructive commands.
- [ ] Health + status endpoint for the systemd service.

## done
- [x] Systemd service `nexus-agent` auto-starting on boot.
- [x] Basic LangGraph agent talking to local Ollama (`qwen3.6`).
- [x] Workspace scaffold: SOUL.md, STYLE.md, CLAUDE.md, new-project.sh.

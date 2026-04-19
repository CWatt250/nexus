# tasks

Open work for nexus-core. Newest-first within each section. Keep items short; link deeper context into `decisions.md` or `scratchpad.md`.

## now
- [ ] **Tool execution layer** — wire `tools/terminal_tool.py` into the LangGraph loop so the agent can actually run shell commands; enforce the 30s timeout and run-log append.
- [ ] **Chroma RAG setup** — stand up persistent Chroma at `~/AI_Agent/chroma/`, bootstrap the `nexus-memory` collection via `tools/rag_tool.py`, seed it with SOUL.md + STYLE.md + wiki docs.
- [ ] **Run log system** — every task completion appends a JSONL record (`ts`, `task`, `result`, `notes`) to the project's `run-log.jsonl`. Centralize the append helper so tools and the agent both use it.

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

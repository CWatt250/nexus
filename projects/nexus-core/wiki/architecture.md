# Architecture

## Overview

Nexus Core is Colton's personal AI agent running on WattBott (Ubuntu 24.04, AMD Ryzen AI Max+ 395, 128 GB RAM, Radeon 8060S). It runs as a set of systemd services under `~/AI_Agent/` and communicates via a local REST API (port 11435) and WebSocket API (port 11436).

## Services

| Service | Port | Purpose |
|---------|------|---------|
| nexus-api | 11435 | REST + OpenAI-compatible API server |
| nexus-agent | — | LangGraph agent loop (consumes API responses, routes to tools) |
| nexus-telegram | — | Telegram bot listener |
| nexus-task-worker | — | Background task executor |
| nexus-dashboard | 11438 | Web UI (next.js) |
| nexus-cc-dispatcher | — | Claude Code dispatch daemon |
| nexus-cc-reporter | — | Claude Code status reporter |

## Components

### Core

- **`nexus.py`** — Main entry point. Parses args, initializes agent graph, runs loop.
- **`agent/`** — LangGraph agent definition, tool registration, state management.
- **`api/`** — FastAPI server with OpenAI-compatible chat endpoint (`/v1/chat/completions`).
- **`models.json`** — Model registry (local ollama models + optional cloud providers).

### Tools

- **`tools/`** — All tool implementations (terminal, file I/O, browser, web search, memory, GitHub, voice, etc.)
- **`tools/run_log.py`** — Centralized run logging helper.
- **`tools/terminal_tool.py`** — Shell command execution with sandbox guardrails + 30s timeout.

### Memory

- **Chroma RAG** — `chromadb` collection `nexus-memory` for long-term storage (~259 chunks from docs).
- **Mem0** — LLM-refined durable facts via `qwen3:4b`.
- **`memory/`** — Raw memory files, RAG chunks, lesson patterns.

### Routing

- **`agent/router.py`** — Intent detection + model routing. Routes trivial queries to fast model, complex reasoning to heavy model.

### Scaffolding

- **`recipes/`** — Project starter recipes (Next.js, Python, etc.).
- **`scaffold_project.py`** — Orchestrates project creation.

### Deployment

- **`vercel_deploy.py`** — Vercel deployment orchestrator.
- **`systemd/`** — Service unit files.

## Data Flow

```
User Input → API Server → Agent Graph → Tool Execution → Response
     ↓                                    ↓
  Telegram                            Run Log
                                    Chroma Memory
```

## Key Files

| Path | Purpose |
|------|---------|
| `~/AI_Agent/nexus.py` | Main entry point |
| `~/AI_Agent/.env` | Secrets (GITHUB_TOKEN, API keys) |
| `~/AI_Agent/config/secrets.yaml` | Alternative secret store |
| `~/AI_Agent/chroma/` | Chroma RAG database |
| `~/AI_Agent/safety/` | Guardrails layer |
| `~/AI_Agent/mcp/servers.json` | MCP server config |
| `~/AI_Agent/projects/nexus-core/wiki/` | Project wiki |
| `~/AI_Agent/projects/nexus-core/run-log.jsonl` | Session run log |

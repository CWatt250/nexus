# Nexus

Personal autonomous AI agent stack. LangGraph + Ollama + 100+ tools running on AMD Ryzen AI Max+ 395 (WattBott), accessible via Telegram and an iOS Liquid Glass dashboard.

> Not a product. Not for distribution. See LICENSE.

## What it does

- **Multi-model routing** across local Ollama models — `qwen3:4b` for fast chat, `qwen3.6:35b-a3b` for heavier work, `qwen2.5-coder` for code, `qwen2.5vl` for vision (when ROCm cooperates).
- **Phone-to-Claude-Code dispatch** — `dispatch:` prefix on Telegram queues a prompt for a background `claude --dangerously-skip-permissions` session. Risky prompts hold for approval, time budgets enforce kill, monthly cost cap.
- **Knowledge Garden** — Karpathy-style wiki at `~/AI_Agent/wiki/` (entities, concepts, decisions). Entity questions ("what is X") query the wiki *before* any LLM call so answers stay grounded.
- **iOS Liquid Glass dashboard** at `http://localhost:11438` — single-file React PWA, installable, real-time WebSocket updates, four tabs (Home / Dispatch / Memory / Settings).
- **Project scaffolding** — six recipes spin up Next.js + Supabase apps end to end.
- **Self-healing** — `nexus_restart_services` tool restarts its own systemd units (sudoers-scoped to `nexus-*`).

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Telegram bot   │  Dashboard (PWA)  │  Open WebUI  │  CLI/voice │
└────────┬─────────────────┬───────────────┬─────────────┬────────┘
         │                 │               │             │
         └──── /chat ──────┴── /v1/chat ───┴── /chat ────┘
                              │
              ┌───────────────┴───────────────┐
              │  conversation_handler.        │
              │  route_message                │  ← intent classifier,
              │  (entity → wiki,              │    dispatch shortcut,
              │   queue → task_worker,        │    safety patterns
              │   dispatch → cc_inbox/)       │
              └───────────────┬───────────────┘
                              │
        ┌─────────────┬───────┴───────┬──────────────┐
        │             │               │              │
   quick_chat    lite_agent      task_worker    cc_dispatcher
   (qwen3:4b,    (qwen3.6 +      (heavy agent,  (claude CLI
    1-3s)        one tool, 8s)    timeouts)      subprocess)
                                                      │
                                              ┌───────┴───────┐
                                              │ cc_results/   │
                                              │ cc_archive/   │
                                              │ cc_logs/      │
                                              └───────┬───────┘
                                                      │
                                              cc_result_reporter
                                              → Telegram + dashboard

memory:
  ChromaDB (RAG) │ Mem0 (durable facts) │ wiki/ (curated truth)
  task_metrics   │ tool_metrics         │ retros (per-task)
```

## Phases shipped

- ✅ Phase 1–11 — Foundation: Ollama integration, tool belt, MCP, autonomous coding loop
- ✅ Phase 13 — Speed layer (82.8% mean TTF reduction)
- ✅ Phase 14 — Reliability scaffolding (metrics, retros, regression tests, GLM escalation, checkpoints)
- ✅ Phase 15 — AsyncSqliteSaver migration + concurrent task/handler split
- ✅ Phase 16 — Two-way Telegram + scheduler + perf guardian + research agent
- ✅ Phase 17 — Live event bus + websocket + minimal dashboard
- ✅ Phase 17.5 — iOS Liquid Glass dashboard (single-file React PWA)
- ✅ Phase 18 — Planner agent + auto model watcher
- ✅ Phase 19 — Sparky proactive: EOD summary, click-to-chat, multi-agent bubbles
- ✅ Phase 22 — Phone-to-Claude-Code dispatch + service restart authority
- ✅ Phase 23.1 — Project scaffolding agent (six recipes)
- ✅ Phase 25 — Knowledge Garden (Karpathy-style wiki)
- ✅ May 1 polish pass — 12 production-test bugs (wiki bypass, think leak, multi-step compliance, dispatch fragment, casual routing, slang glossary, …)

See `STATE.md` for per-phase exit criteria and verification.

## Roadmap

1. 🥇 Phase 21 — Content Production Stack (HyperFrames + Higgsfield + TTS for BidWatt promo PoC)
2. 🥈 ReserveStack — HOA reserve study compliance SaaS for Washington state
3. 🥉 Phase 24 — Shoppable Video Marketplace
4. Phase 26 — SubWatt v2 backend migration to Supabase
5. Phase 16.7 — Fix qwen2.5vl ROCm OOM (unblock vision)
6. Phase 16.10 — MCPs (Filesystem, Obsidian, Excel)
7. Phase 22.x — Self-modification with safety
8. Phase 20 — Sparky v2 visual avatar

## Tech stack

- **Python 3.12**, LangGraph, FastAPI, httpx, Pydantic
- **Ollama** with qwen3:4b, qwen3.6:35b-a3b, qwen2.5-coder, qwen2.5vl, nomic-embed-text
- **Telegram Bot API** (`python-telegram-bot`)
- **React 18 + Tailwind + Babel-standalone** (single-file dashboard PWA, no Node build)
- **ROCm 6.4+** for AMD Radeon 8060S
- **systemd** for service management (~25 nexus-* units)
- **ChromaDB + Mem0** for memory layers
- **SQLite** (WAL) for task queue, scheduler, checkpoints
- **Anthropic Claude CLI** for dispatch backbone

## Repository structure

```
~/AI_Agent/
├── nexus.py              # main agent: tools[] registration, prompt cache,
│                          # sync + async LangGraph builds
├── nexus_api.py          # FastAPI: OpenAI-compatible /v1/chat, dashboard
│                          # /api/*, websocket /ws/events, CORS
├── nexus_design.py       # Nexus Design Studio (port 11436)
├── router.py             # model_for(route) + classify_and_model()
├── reflection.py         # post-turn reflection writer
├── git_sync.py           # auto-commit helper (content paths only)
│
├── core/                 # shared kernels
│   ├── cc_dispatch.py    # Phase 22 dispatch state + risky-pattern matcher
│   ├── event_bus.py      # in-process pub/sub + JSONL persistence
│   ├── json_safe.py      # bytes/Path/dataclass-tolerant json.dumps wrapper
│   ├── scheduler.py      # cron / once / interval triggers
│   ├── secrets.py        # secrets.yaml + .env loader, redact() for logs
│   └── task_queue.py     # SQLite WAL queue (Phase 15.2)
│
├── tools/                # 100+ LangGraph tools — see TOOLS.md
│   ├── cc_dispatch_tool.py     # dispatch_to_claude_code
│   ├── restart_services_tool.py # nexus_restart_services
│   ├── wiki_tool.py            # wiki_query / ingest / update / create
│   ├── coding_agent.py         # solve_coding_task
│   ├── scaffold_tool.py        # project recipes
│   ├── …                       # github / brave / searxng / mem0 / rag /
│                                #   computer_use / image_gen / opengame /
│                                #   vercel / godot / audio_gen / bark / …
│
├── workers/              # background daemons
│   ├── conversation_handler.py # qwen3:4b router (intent → quick_chat /
│   │                           #   lite_agent / task / dispatch / status)
│   ├── task_worker.py          # heavy task executor with retros
│   ├── cc_dispatcher.py        # claude CLI subprocess + budget enforce
│   ├── cc_result_reporter.py   # Telegram fan-out for finished dispatches
│   ├── perf_guardian_loop.py   # RAM/VRAM pressure + LRU model unload
│   ├── scheduler_loop.py       # tick engine for core.scheduler
│   ├── task_notifier.py        # heartbeat + done/failed/timeout notify
│   └── wakeword_listener.py    # "hey nexus" voice trigger
│
├── safety/               # guardrails, sandbox, circuit breaker, watchdog
├── memory/               # runtime state — partially gitignored (see below)
├── agents/               # Phase 9 sub-agent shells
├── recipes/              # Phase 23.1 scaffold templates
├── wiki/                 # Phase 25 Knowledge Garden
│   ├── entities/         # colton, nexus, bidwatt, subwatt, argus
│   ├── concepts/         # llm-wiki-pattern, dispatch-system, intent-routing
│   ├── decisions/        # ADRs (one per major architectural change)
│   ├── SCHEMA.md, log.md, index.md
│
├── dashboard/            # legacy Phase 17 minimal dashboard
├── dashboard_v2/         # Phase 17.5 React Liquid Glass PWA
├── docs/                 # dispatch.md, dashboard_v2.md, telegram-setup.md, …
│
├── CLAUDE.md             # build playbook for Claude Code on this repo
├── SOUL.md               # identity, tone, length cadence, slang glossary
├── STYLE.md              # communication rules
├── STATE.md              # phase status tracker
├── TOOLS.md              # auto-generated tool inventory
├── SERVICES.md           # systemd unit catalog
├── DEPENDENCIES.md       # external services + APIs
├── EXTERNAL_INTEGRATIONS.md
└── SUDO_DISPATCH.sh, SUDO_COMMANDS_R3.sh   # one-shot install scripts
```

## Setup (for restoration on a fresh machine)

```bash
# 1. Clone
git clone git@github.com:CWatt250/nexus.git ~/AI_Agent
cd ~/AI_Agent

# 2. Fill secrets
cp config/secrets.yaml.example config/secrets.yaml
$EDITOR config/secrets.yaml          # add real GITHUB_PAT, TELEGRAM_*, etc.
cp .env.example .env
$EDITOR .env

# 3. Python venv + deps
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Pull Ollama models (see models.json for full list)
ollama pull qwen3:4b
ollama pull qwen3.6:35b-a3b-instruct-2507-q4_K_M
ollama pull qwen2.5-coder:32b
ollama pull nomic-embed-text

# 5. Install systemd units (idempotent)
sudo bash SUDO_DISPATCH.sh
sudo bash SUDO_COMMANDS_R3.sh

# 6. Verify
sudo systemctl status nexus-api nexus-agent nexus-task-worker nexus-telegram \
                      nexus-cc-dispatcher nexus-cc-reporter nexus-dashboard
curl -s localhost:11435/healthz
curl -s localhost:11438/healthz
```

## Key files

| File | What it is |
|------|-----------|
| `CLAUDE.md` | Claude Code working context for this repo |
| `SOUL.md` | Identity, tone, length cadence, slang glossary |
| `STYLE.md` | Communication style rules |
| `STATE.md` | Per-phase status + exit criteria |
| `TOOLS.md` | Auto-generated tool inventory (~100 tools, 18 categories) |
| `SERVICES.md` | systemd unit catalog |
| `wiki/` | Knowledge Garden (entities, concepts, decisions) |
| `recipes/` | Project scaffolding templates |

## What's NOT in this repo (intentionally)

- `config/secrets.yaml` and `.env` — real credentials live local-only
- `cc_inbox/`, `cc_logs/`, `cc_results/`, `cc_archive/`, `cc_metrics/` — Phase 22 dispatch runtime state
- `wiki/sources/` — raw inputs that may contain personal data; the curated `wiki/entities|concepts|decisions/` ARE tracked
- `projects/*/run-log.jsonl` — per-task telemetry; redacted at write time, but kept local-only as defense-in-depth
- `memory/retros/`, `memory/eod/`, `memory/*.jsonl`, `memory/*.db` — runtime state
- `venv/`, `__pycache__/`, `chroma/` — derivable
- `output/`, `content/`, `research/` — generated work products

See `.gitignore` for the full list.

## License

All Rights Reserved — see `LICENSE`. Personal property of Colton Watt. No reuse without written permission.

## Author

**Colton Watt** — Project Estimator at Irex Argus, building Nexus as personal AI infrastructure on WattBott (AMD Ryzen AI Max+ 395, 128 GB, Ubuntu 24.04, ROCm 6.4).

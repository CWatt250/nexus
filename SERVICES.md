# Nexus Services

Inventory of every systemd unit Nexus relies on. All units run under
`cwatt250` and call into `~/AI_Agent/venv/bin/python3` (or `node` for
SearXNG). Restart with `sudo systemctl restart <name>.service` — the
`nexus_restart_services` tool wraps this via the NOPASSWD sudoers entry
in `SUDO_DISPATCH.sh`.

## Core agent

| Service | Purpose | Port |
|---------|---------|------|
| `nexus-agent` | Daemon mode of `nexus.py`. Hosts the LangGraph agent for the CLI/voice paths. | — |
| `nexus-api` | OpenAI-compatible API + WebSocket bus + dashboard backend. | 11435 |
| `nexus-task-worker` | Pulls heavy tasks from `tasks.db`, runs them through the heavy agent. | — |
| `nexus-telegram` | Telegram bot listener. Routes `dispatch:`, `queue status`, `restart …`, plus normal chat. | — |
| `nexus-prewarm` | One-shot warm-up of `qwen3:4b` + `qwen3.6` on boot. | — |

## Phase 22 — Claude Code dispatch

| Service | Purpose |
|---------|---------|
| `nexus-cc-dispatcher` | Watches `cc_inbox/`, runs `claude --dangerously-skip-permissions --print` one at a time, enforces time budget, writes `cc_results/<id>.json`. |
| `nexus-cc-reporter`   | Watches `cc_results/`, fans outcome to Telegram + dashboard event bus. Dedupes via `.reported`. |

## Dashboard / observability

| Service | Purpose | Port |
|---------|---------|------|
| `nexus-dashboard` | Serves `dashboard_v2/` (iOS Liquid Glass PWA), legacy at `/legacy`. | 11438 |
| `nexus-design`    | Nexus Design Studio. | 11436 |
| `nexus-watchdog`  | Health probe + auto-restart of unhealthy services. | — |
| `nexus-perf-guardian` | Watches RAM/VRAM/CPU; drops idle Ollama models, alerts on dogpile. | — |

## Background helpers

| Service | Purpose |
|---------|---------|
| `nexus-chronicle` | Periodic screenshot + OCR + qwen3:4b summary into RAG. |
| `nexus-clipboard-watcher` | Detects clipboard changes, optionally indexes into RAG. |
| `nexus-eod-summary` (timer) | Daily 5pm end-of-day summary. |
| `nexus-file-watcher` | Watches `~/Downloads`, indexes new files. |
| `nexus-git-watcher` | Re-indexes any repo under `~/Dev` on commit. |
| `nexus-lessons` (timer) | Weekly Monday 8am LESSONS.md aggregation. |
| `nexus-model-watcher` (timer) | Weekly Ollama-vs-public model comparison. |
| `nexus-patterns` (timer) | Weekly pattern digest. |
| `nexus-scheduler` | Timer fire engine for the SQLite scheduler (Phase 16.5). |
| `nexus-searxng` | Local search engine container. |
| `nexus-sparky-brain` | Sparky desktop avatar state bridge. |
| `nexus-test` (timer) | Nightly 3am full test suite. |
| `nexus-wakeword` | "Hey Nexus / Hey Sparky" wake-word listener. |

## Activation

Many of these units ship to `/tmp/<name>.service` from the build agent;
the matching block in `~/AI_Agent/SUDO_COMMANDS_R3.sh` (or
`~/AI_Agent/SUDO_DISPATCH.sh` for Phase 22) installs them under
`/etc/systemd/system/`.

## Quick health

```
sudo systemctl status nexus-*  # human-readable
curl -s localhost:11435/healthz  # API
curl -s localhost:11438/healthz  # dashboard
```

The dashboard's Settings tab renders the live status of every
`nexus-*` unit and offers a per-row restart.

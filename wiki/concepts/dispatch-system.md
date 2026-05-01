---
name: CC Dispatch System
description: Phase 22 — Nexus escalates heavy coding/research tasks to a Claude Code subprocess with budget, approval, and result reporting.
type: concept
last_updated: 2026-05-01
sources: []
tags: [phase-22, dispatch, claude-code, escalation]
---

# CC Dispatch System (Phase 22)

When Nexus hits a task that's too heavy for the local LLM (or that needs Claude's particular strength — long-horizon coding, careful refactors, deep research), it escalates by writing a prompt to `cc_inbox/` and letting a watcher daemon spawn a Claude Code subprocess.

## State machine

```
pending_approval ── go ──> queued ──> running ──> {done, failed, timeout}
       │
       └── cancel ──> cancelled
```

## Filesystem layout

| Path | Purpose |
|---|---|
| `cc_inbox/<id>.md` | Queued prompt. Picked up FIFO by `nexus-cc-dispatcher`. |
| `cc_inbox/.pending/<id>.md` | Risky prompt awaiting Telegram approval. |
| `cc_archive/<id>.md` | Completed/cancelled prompt (post-mortem). |
| `cc_logs/<id>.log` | stdout/stderr from the Claude subprocess. |
| `cc_results/<id>.json` | Structured outcome — status, commits, summary, cost estimate. |
| `cc_metrics/dispatches.jsonl` | Append-only audit + cost log. |

## Components

- **`core/cc_dispatch.py`** — single source of truth. State I/O, risky-prompt detection, cost estimation, dataclasses (`DispatchMeta`, `DispatchResult`).
- **`tools/cc_dispatch_tool.py`** — LangGraph tools so Nexus can dispatch / check status from inside an agent turn.
- **`workers/cc_dispatcher.py`** — long-running watcher. `_run_one()` spawns Claude, polls log activity, enforces budget + inactivity kill, writes the result.
- **`workers/cc_result_reporter.py`** — watches `cc_results/`, formats new results to Telegram + dashboard event bus.
- **`tools/telegram_listener.py`** — handles `dispatch:`, `force dispatch:`, `go cc_xxx`, `cancel cc_xxx`, `queue status`, `restart`, `retry`, `extend` prefixes.

## Risky-prompt detection

`core/cc_dispatch.RISKY_PATTERNS` is a regex list — `drop database`, `rm -rf`, `--no-verify`, `force-push`, `production`, etc. A match parks the prompt in `.pending/` and DMs Colton for approval. Safer to false-positive than miss.

## Budget

- Per-dispatch: `time_budget_minutes` in the `DispatchMeta` (default 120, max 480).
- Monthly: soft cap on estimated cost. `force dispatch:` overrides.
- Inactivity kill: if the log file is idle for 5+ minutes, the dispatch is killed.

## Wiki integration (Phase 25)

Every completed dispatch result is auto-ingested into `wiki/sources/<dispatch_id>.json` so the knowledge garden has a permanent record of what was attempted and what came out. The wiki extractor then updates relevant entity / decision pages.

## Related
- [Knowledge garden / LLM wiki pattern](llm-wiki-pattern.md)
- Decision: [2026-04-30 — Phase 22 dispatch](../decisions/2026-04-30_phase-22-dispatch.md)

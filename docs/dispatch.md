# Phase 22 — Claude Code Dispatch

Phone-first delegation. Nexus hands prompts to a background Claude Code
session running on the same box; one job at a time, the rest queue,
risky prompts wait for Telegram approval, and Telegram gets the
outcome with offered follow-ups (`restart`, `retry`, `extend`).

## Quick start

After Colton runs `~/AI_Agent/SUDO_DISPATCH.sh` once (installs the
sudoers entry + the two systemd units), dispatch from anywhere:

**From Telegram**
```
dispatch: add a /version endpoint to nexus_api.py and write a test for it
```
Reply `queue status` any time to see what's running, queued, or held.

**From the dashboard** (port 11438) — open the Dispatch tab, paste the
prompt, hit Dispatch.

**From Nexus chat / any agent path**
```
dispatch_to_claude_code(prompt="…", time_budget_minutes=120, label="…")
```

## State machine

```
                  risky?
   ┌─────────────────┴────────────────┐
   │                                  │
no risky pattern              risky pattern
   │                                  │
   ▼                                  ▼
cc_inbox/<id>.md            cc_inbox/.pending/<id>.md
   │                                  │
   │                              go cc_xxx (Telegram or dashboard)
   │                                  │
   │                                  ▼
   │                          (rejoins inbox)
   ▼
[dispatcher daemon picks oldest mtime]
   │
   ▼
spawn `claude --dangerously-skip-permissions --print` with prompt on stdin
log → cc_logs/<id>.log
   │
   ├─ exit 0           → done
   ├─ exit !=0         → failed
   ├─ 80% time budget  → Telegram heads-up
   ├─ 100% time budget → SIGTERM, 10s, SIGKILL → timeout
   └─ log idle 5+ min  → SIGTERM (assumed stuck) → failed
   │
   ▼
write cc_results/<id>.json
move cc_inbox/<id>.md → cc_archive/<id>.md
log to cc_metrics/dispatches.jsonl

(reporter daemon then tails cc_results/, formats per-status Telegram message)
```

## Filesystem layout

| Path | Purpose |
|------|---------|
| `cc_inbox/<id>.md`              | Queued prompt — picked up FIFO by the dispatcher. |
| `cc_inbox/.pending/<id>.md`     | Risky prompt awaiting Telegram approval. |
| `cc_inbox/.lock`                | JSON: `{dispatch_id, started_at_epoch}` for the job currently running. |
| `cc_archive/<id>.md`            | Original prompt + meta header, post-mortem. |
| `cc_logs/<id>.log`              | Captured stdout/stderr from the claude subprocess. |
| `cc_results/<id>.json`          | Structured outcome (status, duration, commits, summary, cost estimate). |
| `cc_results/.reported`          | One-per-line dispatch_ids the reporter has already notified — dedup across restarts. |
| `cc_metrics/dispatches.jsonl`   | Append-only audit log. One line per dispatch with cost + commit count. |

## Risky pattern matcher

In `core/cc_dispatch.py:RISKY_PATTERNS`. Conservative — false positives
cost a Telegram tap, false negatives cost data. Current set:

- `drop database|table|schema`
- `delete from`
- `rm -rf`
- `git push --force` / `force-push`
- `production` / `PROD` (case-sensitive on PROD)
- `skip tests` / `bypass auth|security|tests`
- `main branch directly`
- `--no-verify`
- `sudo`
- `delete … (all|every|everything)`

Any match holds the prompt in `.pending/`. Telegram receives:

```
🚨 Risky prompt held for approval (matched: `drop database`).
dispatch_id: cc_abcdef12 — label: …
Reply `go cc_abcdef12` to dispatch or `cancel cc_abcdef12` to abort.
```

## Time budgets

- Default 120 min, range 5–480 min, set per-dispatch via
  `time_budget_minutes` arg.
- 80% mark → Telegram heads-up: `⏱️ cc_xxx — label at 80% of budget (Nm left).`
- 100% mark → SIGTERM the entire process group, wait 10s, SIGKILL if
  still alive. Status recorded as `timeout`, partial commits captured.
- Log inactivity > 5 min → SIGTERM, status recorded as `failed` with
  `error_tail = "killed: log inactivity (5+ min)"`.

## Cost tracking

Estimated, not billed — the CLI doesn't expose token usage. Default
model is rough:
- input ≈ 8000 tokens/min × duration
- output ≈ 1200 tokens/min × duration
- Sonnet 4.6 pricing: $3/M input + $15/M output

Per-dispatch entry written to `cc_metrics/dispatches.jsonl`.
`core.cc_dispatch.month_spend_usd()` sums the current month. The
monthly cap (`CLAUDE_CODE_MONTHLY_BUDGET` from `config/secrets.yaml`,
default $50) drives:

- 50% — `warn50`, dashboard shows amber
- 80% — `warn80`, dashboard amber + tool annotates "budget X% used"
- 100% — `over`, both tool path and dashboard refuse new dispatches
  unless the user passes `force=True` (`force dispatch:` on Telegram,
  Force button on dashboard).

## Telegram commands

| Command | Effect |
|---------|--------|
| `dispatch: <prompt>` | Queue a new dispatch (default 120m budget). |
| `force dispatch: <prompt>` | Bypass monthly budget cap. |
| `go cc_xxx` | Release a pending-approval prompt. |
| `cancel cc_xxx` | Drop a pending or queued dispatch. |
| `queue status` (or `queue`) | Snapshot: running, queued, pending, budget. |
| `restart cc_xxx` | Restart the default nexus-* set after a dispatch. |
| `restart nexus-foo` | Restart a specific service. |
| `retry cc_xxx` | Re-dispatch the original archived prompt. |
| `extend cc_xxx <minutes>` | Re-dispatch with a bigger time budget. |

These shortcuts run BEFORE the LLM router so they're deterministic and
never block on Ollama.

## Services

| Service | Role |
|---------|------|
| `nexus-cc-dispatcher.service` | Watches `cc_inbox/`, runs Claude Code one at a time. |
| `nexus-cc-reporter.service`   | Watches `cc_results/`, fans outcome to Telegram + dashboard. |

Both ship as `Restart=always` units. Stop with `sudo systemctl stop` —
a graceful stop lets the current dispatch finish its bookkeeping.

## Verification

- `tests/test_dispatch.py` — 15 unit + integration tests covering meta
  round-trip, risky detection, approval flow, cancel pre-flight, FIFO
  ordering, lock file, cost scaling, monthly spend filtering, tool
  budget gate, restart tool refusal of non-nexus-* names.
- Run with: `cd ~/AI_Agent && venv/bin/python3 -m pytest tests/test_dispatch.py -q`

## End-to-end smoke test

Once `SUDO_DISPATCH.sh` has been run and services are up:

1. Telegram: `dispatch: add a /version endpoint that returns {"version": "1.0"}`
2. Expect: `🚀 Dispatched. id cc_xxxxxxxx — … (budget 120m).`
3. `queue status` → see it as Running.
4. ~5–10 min later: `✅ cc_xxx — … done in N.Nm. K commit(s): …`
5. Reply `restart nexus-api` to bounce the API process.
6. `curl http://localhost:11435/version` → 200 OK.

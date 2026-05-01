---
title: Nexus GitHub Backup
date: 2026-05-01
status: accepted
tags: [infra, git, security]
---

# Context

Nexus codebase had grown to 35+ phases (Phase 22 dispatch, Phase 17.5 dashboard, Phase 25 wiki, May-1 polish pass) without remote backup. Risk of total loss from disk failure, accidental `rm -rf`, or corrupted state. Local `~/nexus-backup-*.tar.gz` snapshots exist but are also on the same disk. Time to put a remote off-host.

# Decision

Created private GitHub repo `CWatt250/nexus` as canonical backup.

## Repo policy

- **Visibility**: private from creation. License `LICENSE`: All Rights Reserved.
- **Branch**: `main` is canonical. Auto-commit via `git_sync.py` continues; remote push is manual.
- **`.gitignore`** excludes secrets (`config/secrets.yaml`, `.env`), Phase 22 dispatch runtime (`cc_inbox/`, `cc_logs/`, `cc_results/`, `cc_archive/`, `cc_metrics/`), `wiki/sources/` (raw inputs may contain personal data), `projects/*/run-log.jsonl` (per-task telemetry), `memory/{retros,eod,*.jsonl,*.db}`, `venv/`, and the usual `__pycache__/` family.
- **Tracked content**: every `.py` under `core/`, `tools/`, `workers/`, `safety/`, `agents/`; all top-level docs (`CLAUDE.md`, `SOUL.md`, `STATE.md`, `TOOLS.md`, `SERVICES.md`, `README.md`, etc.); curated wiki (`wiki/entities/`, `wiki/concepts/`, `wiki/decisions/`, `wiki/SCHEMA.md`, `wiki/index.md`, `wiki/log.md`); recipes; dashboard source; systemd unit templates.

## Pre-flight finding: leaked secrets in tracked history

Pre-flight audit (`git grep` on tracked content + history) surfaced four live secret values committed to `projects/nexus-core/run-log.jsonl` on 2026-04-30 14:45 — Nexus's terminal tool ran `cat .env` and `cat secrets.yaml` (Nexus inspecting its own config), and `tools/run_log.py` captured the raw stdout. The JSONL is in the auto-commit content allowlist.

Tokens leaked in history: `GITHUB_TOKEN` (ghp_…), `GITHUB_PAT` (github_pat_…), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## Remediation (Path B — scrub history, do not rotate)

Chose `git filter-repo --replace-text` over rotation:

1. Backed up `.git/` to `/tmp/nexus-git-backup-pre-scrub` as escape lever.
2. Built `/tmp/nexus-redact.txt` mapping each leaked value → `REDACTED_<KEY>` marker, longer patterns first so the bot token replaces before standalone `chat_id`.
3. Ran `git-filter-repo --replace-text … --force` (installed via `pip install git-filter-repo`). 173 commits preserved, all hashes rewritten.
4. Verified: `git log -p --all | grep <each token>` returned 0 hits per token.
5. Test fixture in `tests/test_secrets_parser.py` got cosmetically rewritten on both sides of its assertion, still passes.

## Defense-in-depth (so this doesn't recur)

- **`.gitignore`**: `projects/*/run-log.jsonl` is now ignored. Run logs continue locally; nothing gets pushed.
- **`tools/run_log.py`**: every string field (task, notes, command, stdout, stderr, reason, extras) passes through `core.secrets.redact()` before disk. Known token values get masked to `<REDACTED>` at write time.
- Future audit: re-run `git grep -E 'ghp_|github_pat_|<chat_id>'` before any remote-add operation.

# Consequences

- Local hardware failure no longer means total loss. Clone-and-restore on a fresh machine takes ~10 min plus model pulls.
- Cannot accidentally make public — repo is private from creation.
- Wiki `sources/` excluded; will rebuild from `research/` + scratchpad on restore.
- All dispatch logs/results excluded; only code + curated docs are tracked.
- History was rewritten with `--force`. Old commit hashes no longer reachable from `main`. The pre-scrub backup at `/tmp/nexus-git-backup-pre-scrub` can be deleted once the GitHub push is verified clean.

# Follow-ups (deferred)

These were noted during the backup audit but NOT fixed in this dispatch:

- Add a pre-commit hook (or extend the auto-commit helper) that rejects diffs matching common secret patterns. Belt-and-suspenders on top of `run_log.py` redaction.
- Move `tools/run_log.py` into `core/` since it's lower-level than the rest of `tools/`. Cosmetic.
- `LESSONS.md` and `CHANGELOG.md` may also benefit from a redaction pass — they're tracked but auto-generated; review next dispatch.

# See also
- [[concepts/dispatch-system]]
- [[entities/nexus]]
- `core/secrets.py` — `redact()` implementation
- `tools/run_log.py` — wired into the redactor
- `.gitignore` — full exclusion list

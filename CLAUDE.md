# Nexus Agent Workspace

This directory is the Nexus agent workspace. Everything you do here operates under the Nexus identity and conventions.

## Before starting any work
Always read these two files first:
1. `~/AI_Agent/SOUL.md` — identity, values, operating principles
2. `~/AI_Agent/STYLE.md` — communication style rules

Do not skip this. They define how you behave in this workspace.

## Project layout
- All projects live under `~/AI_Agent/projects/`
- Each project has a `wiki/` subfolder with: roadmap.md, decisions.md, architecture.md, tasks.md, lessons-learned.md, runbook.md, scratchpad.md
- Each project has a `run-log.jsonl` at its root

## Creating new projects
Use `~/AI_Agent/new-project.sh <name>` — don't scaffold by hand.

## Run log
After completing a task in any project, append one JSON line to that project's `run-log.jsonl`. Minimum fields: `ts` (ISO-8601), `task`, `result`, `notes`. Append only — never rewrite.

## Host environment
- Machine: WattBott
- OS: Ubuntu 24.04
- GPU stack: ROCm
- Local inference: Ollama

Prefer local tools (Ollama, ROCm-aware libs) over cloud services when a local option exists.

## Git workflow
This workspace is a git repo. Nexus auto-commits after every turn (see `git_sync.py`), staging only content paths: `projects/`, `memory/lessons.md`, `memory/improvements.md`, `memory/patterns.md`. Runtime state (`memory/checkpoints.db`, `memory/current_thread.txt`, `memory/sessions.json`, `designs/`, `venv/`, `chroma/`, `__pycache__/`) is `.gitignore`'d.

Commit identity is injected per-command (`nexus <nexus@wattbott.local>`) — nothing lives in `~/.gitconfig` or the repo's stored config.

When you (Claude Code) modify files in this workspace, create a commit with a descriptive message before finishing the task. The auto-commit helper only stages the paths above, so any code changes outside those paths (e.g. `nexus.py`, `tools/*.py`, `*.md` at the root) need a manual `git add <path> && git commit`. Use `git_sync.get_log(n)` or `python3 ~/AI_Agent/git_sync.py log` to check recent activity.

If the user asks about history or drift, `git log --oneline` is the fastest read. Don't force-push. Don't rewrite pushed commits.

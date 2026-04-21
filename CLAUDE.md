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

---

# Karpathy Coding Principles
_source: https://raw.githubusercontent.com/forrestchang/andrej-karpathy-skills/main/CLAUDE.md_

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---
name: Phase 22 — Stand up CC dispatch
description: Decided to escalate heavy/long-horizon tasks to Claude Code subprocesses with budget + approval + result reporting, instead of grinding them through local LLM.
type: decision
last_updated: 2026-05-01
sources: []
tags: [phase-22, dispatch, claude-code, escalation]
---

# 2026-04-30 — Phase 22: Stand up CC dispatch

## Decision
Build a system that lets Nexus shell out to Claude Code for tasks that exceed local-model practicality (long-horizon coding, careful refactors, deep research), with a structured contract:
- Risky prompts park in `cc_inbox/.pending/` and require Telegram approval.
- Each dispatch has a `time_budget_minutes` (default 120, hard max 480) and gets killed on timeout or 5-min log inactivity.
- Results land as JSON in `cc_results/<id>.json` and get reported via Telegram + dashboard event bus.
- A monthly cost cap (estimated, since the CLI doesn't expose real billing) with `force dispatch:` override.

## Why
- Local qwen3.6 is excellent for chat, classification, fast tools, and short coding tasks. It runs out of horizon for multi-hour refactors.
- Routing those through chat blocks the conversation, defeating the whole point of the conversation handler being snappy.
- Out-of-band escalation keeps the bot responsive AND lets Claude do the heavy lifting where it excels.

## How to apply
- When asked to do something heavy: prefer `cc_dispatch_tool` over running it inline.
- When the prompt mentions destructive ops, production, or `--no-verify`, expect the risky-pattern detector to park it for approval — don't try to bypass.
- Always cite the dispatch ID when reporting back to Colton so he can `cancel`, `extend`, or `retry`.

## Related
- Concept: [Dispatch system](../concepts/dispatch-system.md)
- Successor: Phase 25 auto-ingests dispatch results into the wiki.

---
name: coding-router
type: entity
updated: 2026-05-03T18:07:39.474630+00:00
---

# Coding Router (Phase 28)

Tracks dispatches routed through the tier-aware Claude Code dispatcher. Source: `cc_metrics/dispatches.jsonl`. Last update auto-rewritten by `workers/cc_result_reporter`.

## Cumulative

- Total dispatches: **5**
- Successful: **5** (100%)
- Total estimated cost: **$0.0067**

## By tier

| tier | count | done | est. cost | success |
|------|-------|------|-----------|---------|
| flash | 4 | 4 | $0.0057 | 100% |
| pro | 1 | 1 | $0.0010 | 100% |

## Slash commands

- `/code <prompt>` — DeepSeek V4-Flash (cheap default, ~$0.005)
- `/pro <prompt>`  — DeepSeek V4-Pro (~$0.05)
- `/real <prompt>` — Anthropic Sonnet 4.6 (~$0.20)
- `/local <prompt>` — qwen3-coder:30b local (free)
- `/quick <prompt>` — qwen3:4b quick chat (free)

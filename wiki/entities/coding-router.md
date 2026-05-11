---
name: coding-router
type: entity
updated: 2026-05-07T18:08:29.357098+00:00
---

# Coding Router (Phase 28 + 29)

Tracks dispatches routed through the tier-aware Claude Code dispatcher. Source: `cc_metrics/dispatches.jsonl`. Last update auto-rewritten by `workers/cc_result_reporter`.

Phase 29 made `/max` the default for complex builds — Colton already pays for the Max subscription, so the API-key path became a rare fallback.

## Cumulative

- Total dispatches: **28**
- Successful: **22** (79%)
- Total estimated cost (API-billed only): **$0.0334**

## By tier

| tier | count | done | est. cost | success |
|------|-------|------|-----------|---------|
| max | 11 | 10 | $0.0000 | 91% |
| flash | 11 | 10 | $0.0249 | 91% |
| pro | 1 | 1 | $0.0010 | 100% |
| api | 5 | 1 | $0.0075 | 20% |

## Slash commands (Phase 29 ladder)

- `/max <prompt>`   — Claude Sonnet 4.6 via Max plan ($0 marginal) **default**
- `/code <prompt>`  — DeepSeek V4-Flash (~$0.005)
- `/pro <prompt>`   — DeepSeek V4-Pro (~$0.05)
- `/api <prompt>`   — Sonnet 4.6 via API key (~$0.10–1.00)
- `/local <prompt>` — qwen3-coder:30b local ($0)
- `/quick <prompt>` — qwen3:4b chat ($0)
- `/real <prompt>`  — *deprecated alias for /api*

## Routing without a slash

- Casual chat → `/quick`
- `make a quick/simple/tiny X` → `/local`
- `build me X` / `create X` / `make me X` / `code X` → `/max`

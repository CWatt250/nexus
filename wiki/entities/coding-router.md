---
name: coding-router
type: entity
updated: 2026-05-04T02:13:50.857670+00:00
---

# Coding Router (Phase 28 + 29)

Tracks dispatches routed through the tier-aware Claude Code dispatcher. Source: `cc_metrics/dispatches.jsonl`. Last update auto-rewritten by `workers/cc_result_reporter`.

Phase 29 made `/max` the default for complex builds — Colton already pays for the Max subscription, so the API-key path became a rare fallback.

## Cumulative

- Total dispatches: **8**
- Successful: **7** (88%)
- Total estimated cost (API-billed only): **$0.0142**

## By tier

| tier | count | done | est. cost | success |
|------|-------|------|-----------|---------|
| max | 1 | 1 | $0.0000 | 100% |
| flash | 4 | 4 | $0.0057 | 100% |
| pro | 1 | 1 | $0.0010 | 100% |
| api | 2 | 1 | $0.0075 | 50% |

## Slash commands (Phase 29 ladder)

- `/max <prompt>`   — Claude Sonnet 4.6 via Max plan ($0 marginal — uses subscription) **default for complex builds**
- `/code <prompt>`  — DeepSeek V4-Flash (~$0.005 — saves Max quota on small builds)
- `/pro <prompt>`   — DeepSeek V4-Pro (~$0.05 — DeepSeek mid-tier)
- `/api <prompt>`   — Sonnet 4.6 via API key (~$0.10–1.00 — fallback if Max limits hit)
- `/local <prompt>` — qwen3-coder:30b local ($0 — offline)
- `/quick <prompt>` — qwen3:4b chat ($0 — chat, not code)
- `/real <prompt>`  — *deprecated alias for /api; logged to   `cc_logs/_deprecation.log` whenever used*

## Routing without a slash

- Casual chat → `/quick`
- `make a quick/simple/tiny X` → `/local`
- `build me X` / `create X` / `make me X` / `code X` →   `/max` (Phase 29 default; was `/code` in Phase 28)

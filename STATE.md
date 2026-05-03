# Nexus Build State

## Current Phase
COMPLETE

## Current Task
NONE

## Last Completed Task
Phase 28 — Smart Coding Router + Slash Commands (10/10 gates, $0.012 cook cost)

## Phase Status
- Phase 12: SKIPPED
- Phase 13: COMPLETE (2026-04-27, 82.8% mean TTF reduction)
- Phase 14: COMPLETE (2026-04-27, 21/21 tests, metrics + retros generating)
- Phase 15: COMPLETE (2026-04-28, 5/5 handler <10s, long task done concurrent)
- Phase 16: COMPLETE (2026-04-28, scheduler+guardian+handler gates PASS)
- Phase 17: COMPLETE (2026-04-28, dashboard html+bus+publish_remote gates PASS)
- Phase 17.5: COMPLETE (2026-04-30, iOS Liquid Glass dashboard at port 11438, PWA-installable)
- Phase 18: COMPLETE (2026-04-28, planner clarify+plan, model watcher candidates)
- Phase 19: COMPLETE (2026-04-28, all 6 sub-tasks shipped + verified)
- Phase 22: COMPLETE (2026-04-30, dispatch tool + watcher daemon + reporter + Telegram routing + 15/15 tests)
- Phase 27: COMPLETE (2026-05-02, scope-guarded local builder + build intent → qwen3.6)
- Phase 28: COMPLETE (2026-05-03, tier-aware Claude Code router + 5 slash commands, 10/10 gates)

## Phase 28 — Smart Coding Router + Slash Commands (2026-05-03)
Folded tier (flash/pro/real/local) into existing dispatch infrastructure
per Colton directive (no new tools/claude_code_dispatch.py file).

Code:
- `core/cc_dispatch.py` — DispatchMeta gains `tier`, DispatchResult gains
  `tier`/`model_used`/`artifact_paths`/`needs_review`/`review_notes`.
  TIER_PRICING + TIER_MODELS dicts; `estimate_cost(duration, tier)`,
  `day_spend_usd()`, `get_cost_limits()`.
- `workers/cc_dispatcher.py` — `_spawn_claude(tier=)` sources the right
  ~/.claude-* env file before exec; `_run_local_qwen()` streams Ollama
  qwen3-coder:30b for tier=local; pre-flight cost ceiling refusal;
  `_detect_artifact_paths` + visual-verify call after run.
- `workers/cc_result_reporter.py` — formats tier/cost/needs_review,
  auto-attaches artifact files via Telegram sendDocument, writes one
  line to wiki/log.md + rewrites wiki/entities/coding-router.md.
- `workers/conversation_handler.py` — SLASH_COMMANDS dict +
  parse_slash_command + _route_slash_command + _enqueue_tiered_dispatch
  (injects target-path hint for auto-attach), _slash_local_build,
  SIMPLE_BUILD_RE / COMPLEX_BUILD_RE smart routing.
- `tools/visual_verify.py` — Playwright headless screenshot + qwen2.5vl
  CLEAN/BROKEN verdict with description-level override.
- `tools/local_builder.py` — model param so /local can pick qwen3-coder.
- `tools/telegram_listener.py` — CommandHandler for /code /pro /real
  /local /quick /help; _build_in_background auto-attaches HTML +
  posts visual verify screenshot.
- `config/cost_limits.yaml` — per-dispatch $0.50 + per-day $5.00 ceilings.
- `~/.claude-deepseek-flash`, `~/.claude-deepseek-pro`,
  `~/.claude-anthropic` — chmod 600 env files sourced by dispatcher.
- `~/.bashrc` — claude-cheap / claude-pro / claude-real bash functions.

Test gates (10/10 PASS):
- Gate 1 DeepSeek API curl → 200 OK
- Gate 2 bash aliases all resolve as functions
- Gate 3 /code analog clock → 145s, $0.0035, vision CLEAN, HTML+screenshot
  attached to Telegram
- Gate 4 /local fibonacci → qwen3-coder:30b, 34s, $0
- Gate 5 /quick "what is 2+2" → "4" in 0.4s
- Gate 6 smart routing "build me a working analog clock..." auto →
  tier=flash dispatch (cc_7aa82914)
- Gate 7 reporter sendDocument 200 (HTML + screenshot) — Phase 27
  auto-attach bug fixed
- Gate 8 visual verify flags blank page → needs_review=True
- Gate 9 5 cloud dispatches logged with tier/model/cost (4 flash + 1 pro
  on deepseek-v4-pro), $0.0067 cumulative
- Gate 10 wiki/entities/coding-router.md auto-rewritten with cumulative
  stats, wiki_query "coding router" returns the entity

Cook cost: ~$0.012 across all gates. Test artifacts in ~/AI_Agent/games/
(analog-clock variants) + ~/AI_Agent/cc_artifacts/ (screenshots).

Limitations:
- ANTHROPIC_API_KEY not in secrets.yaml → /real tier currently routes
  but claude subprocess will fail-with-empty-token. Add the key to
  secrets.yaml to enable.
- Visual verify CLEAN/BROKEN verdict is qwen2.5vl-driven; can be
  inconsistent on edge cases (garbage characters render as "shapes
  and symbols"). Description-level override catches blank pages
  reliably.

## Phase 22 — Phone-to-Claude-Code Dispatch (2026-04-30)
Code:
- `core/cc_dispatch.py` — paths, state machine, risky-pattern detector,
  monthly cost tracking
- `tools/cc_dispatch_tool.py` — LangGraph tool dispatch_to_claude_code
- `tools/restart_services_tool.py` — nexus_restart_services (sudoers-scoped)
- `workers/cc_dispatcher.py` — daemon, one-at-a-time claude subprocess
  with budget enforcement
- `workers/cc_result_reporter.py` — Telegram + dashboard fan-out
- `tools/telegram_listener.py` — dispatch:/go/cancel/queue/restart/retry/extend shortcuts
- New API endpoints in `nexus_api.py`: /api/dispatches, /api/dispatch,
  /api/dispatch/approve, /api/dispatch/cancel, /api/dispatch/{id}/log,
  /api/services, /api/restart, /api/memory/retros, /api/memory/retro/{id}
Deploy:
- `/tmp/nexus-cc-dispatcher.service`, `/tmp/nexus-cc-reporter.service`
- `SUDO_DISPATCH.sh` — sudoers entry + service install + restart
Tests: 15/15 pass (`tests/test_dispatch.py`)
Docs: `docs/dispatch.md`, `docs/dashboard_v2.md`, `SERVICES.md`,
`TOOLS.md` (now 100 tools / 18 categories)

## Failures Log
(empty)

## Skip Log
(empty)

## See also
- `MEGA_RUN_COMPLETE.md` — full run summary
- `SUDO_COMMANDS_R3.sh` — Colton-side activation steps

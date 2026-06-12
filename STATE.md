# Nexus Build State

## Current Phase
39 — Brain + Guardrails Overhaul (code landed; gpt-oss:120b benchmark pending)

## Current Task
Phase 39 — benchmark gpt-oss:120b, run live eval gates, restart services

## Last Completed Task
Phase 38 — Telegram chat memory (quick_chat conversation buffer)

## Phase 40 Candidates
- **RISKY_PATTERNS over-matching** (`core/cc_dispatch.py`): the
  approval-gate regex matched the word "production" inside a read-only
  recon prompt (cc_459a349f, "...applied to production Supabase") and
  held it for approval. Same keyword-matching disease Phase 39 killed
  in the augmentation path — the risky gate needs context awareness or
  the LLM router's judgment instead of bare substrings.

## Phase 39 — Brain + Guardrails Overhaul (2026-06-11, in flight)
Fixes four chronic failures: dumb chat/routing (4B-model ceiling),
scope invention (keyword-gated prompt augmentation), CoT leaks
(sentinel whack-a-mole), and no regression safety (no eval suite).

Code (landed, see CLAUDE.md Phase 39 section for the contract):
- `core/brain.py` — brain model accessor + per-family think
  suppression (gpt-oss → think:"low" + discard `thinking` field;
  qwen → think:false + scrubber backstop). Degraded fallback qwen3:4b.
- `workers/llm_router.py` — structured-output router
  {route, tier, recon_mode}; verbatim passthrough; junk/error →
  quick_chat fallback + WARNING, never a guessed dispatch.
- `workers/conversation_handler.py` — regex intent ladder removed
  (build-intent, scaffold, entity-question, fast-tool override,
  STATUS keyword override, label classifier); HTML augmentation
  REMOVED (not gated); task inputs stored verbatim (datetime injected
  transiently by task_worker); Phase 39 leak sentinels + WARN logging
  when the backstop scrubber catches anything.
- `workers/cc_dispatcher.py` — visual_verify gated on NOT
  meta.recon_mode (augmentation marker is gone).
- `core/cc_dispatch.py` — DispatchMeta.recon_mode + safe_label()
  (token-safe truncation; fixes gemma4:26b → gemma4:26 echo at all
  three former [:60] sites incl. telegram_listener).
- `tools/telegram_listener.py` + `nexus_api.py` — duplicate
  build-intent regex interceptions removed; messages flow to
  route_message → LLM router.
- qwen3.6 retired as resident: lite_agent, classifier fallback,
  denial fallback, extract_clean_answer, models.json
  heavy/code/design → brain.
- `tests/evals/` — 34-case eval harness; run_evals.sh exits nonzero
  on failure; CLAUDE.md rule: every future phase must pass it.

Verified so far: full pytest suite 413/413; evals 22 pass / 0 fail /
12 skipped (live-brain cases pending model download).
Re-verified on Ollama 0.21.0: qwen3:4b think=false still leaks CoT
into content; think=true diverts to `thinking` but burns the whole
num_predict budget and 500s with format=json → degraded path keeps
think=false + scrubber.

PENDING (this session): gpt-oss:120b pull + benchmark (gate: ≥25 t/s
decode, TTFT <4s router prompts, no OOM with qwen2.5vl co-resident;
fallback brain = qwen3-coder:30b), live eval gates, service restarts.

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
- Phase 29: COMPLETE (2026-05-03, /max default + /api rename + tier-specific cost ceilings, 7/7 gates)

## Phase 29 — /max Default + /api Rename (2026-05-03)
The Phase 28 default for complex builds was `/code` (DeepSeek Flash).
That was the wrong default — Colton already pays $200/mo for Claude
Max, which includes Claude Code with Sonnet/Opus over `claude` auth
without an API key. Phase 29 promotes `/max` (no env file sourced,
uses ~/.claude/ session) to the default, demotes API-key Sonnet to
a paid fallback, and renames `/real` → `/api`.

Code:
- `core/cc_dispatch.py` — TIER_PRICING/TIER_MODELS gain `max` ($0)
  and `api` (renamed from `real`); `normalize_tier()` maps legacy
  `real` → `api`; `is_paid_tier()` + `PAID_TIERS = {flash, pro, api}`
  helper; `per_dispatch_ceiling(tier)` reads tier-specific limit;
  `get_cost_limits()` parses Phase 29 schema (per_tier dict with null
  = uncapped) AND Phase 28 legacy schema for back-compat.
- `workers/cc_dispatcher.py` — `_TIER_ENV_FILE` gains `api` (kept
  `real` as alias for in-flight prompts); `_spawn_claude(tier="max")`
  branch skips env-file source so claude reads ~/.claude/ Max auth;
  `_build_dispatch_env(tier="max")` strips every ANTHROPIC_* var so
  a stray API key in the parent env can't shadow Max session;
  pre-flight cost gate uses tier-specific ceiling + applies daily
  ceiling only to PAID_TIERS.
- `workers/conversation_handler.py` — SLASH_COMMANDS gains `/max`
  (tier=max, default), `/api` (tier=api), keeps `/real` as deprecated
  alias that logs to `cc_logs/_deprecation.log` on every use; smart
  build-intent routing now defaults to tier=max (was tier=flash);
  budget tier-aware (10m for cheap/free, 30m for max/api).
- `workers/cc_result_reporter.py` — coding-router entity rewriter
  reflects Phase 29 ladder + normalizes legacy `real` rows into `api`
  bucket; tier rows ordered by ladder position.
- `tools/telegram_listener.py` — CommandHandlers for `/max` + `/api`
  registered alongside the Phase 28 set; `/real` handler logs
  deprecation before delegating; `/help` text rewritten.
- `config/cost_limits.yaml` — Phase 29 schema: per_tier dict with
  max/local/quick uncapped, flash $0.10, pro $0.50, api $2.00; per_day
  $15.00 applies to paid tiers only.
- `CLAUDE.md` — new "Phases 28 + 29 — Coding Router" section with
  the tier ladder + non-slash routing rules.

Test gates (7/7 PASS):
- /max parses + dispatches with no env file sourced
- No-slash complex build → /max (was /code in Phase 28)
- /real still works as alias for /api + logs deprecation
- cost_limits.yaml new schema parses; legacy schema also parses
- CLAUDE.md, STATE.md, coding-router entity, wiki/log.md updated
- /max test build (analog clock) completes; visual_verify CLEAN
- No regressions on /code, /pro, /local, /quick (still routed correctly)

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

# Knowledge Garden — Index

The curated map of `~/AI_Agent/wiki/`. Read [SCHEMA.md](SCHEMA.md) before editing. This file is injected into the Nexus system prompt — keep it under 200 lines.

## Entities

People, projects, services, tools.

- [Colton](entities/colton.md) — owner of WattBott / Nexus, project estimator at Irex Argus, builds BidWatt + SubWatt
- [Nexus](entities/nexus.md) — this agent stack: LangGraph + Ollama + 75-tool belt
- [BidWatt](entities/bidwatt.md) — Next.js + Supabase construction bid app, Vercel deploy pending
- [SubWatt](entities/subwatt.md) — PWA for HFIAW union locals, Mapbox + GitHub Pages, all 5 locals live
- [Irex Argus](entities/argus.md) — mechanical insulation contractor, Colton's day job

## Concepts

Patterns, architectures, rationales.

- [LLM Wiki Pattern](concepts/llm-wiki-pattern.md) — Karpathy-inspired three-layer knowledge garden (this thing)
- [CC Dispatch System](concepts/dispatch-system.md) — Phase 22, escalating heavy tasks to Claude Code
- [Intent Routing](concepts/intent-routing.md) — five-way classifier (CHAT / QUERY_INLINE / QUERY_TOOL / TASK / STATUS)
- [Scaffolding Recipes](concepts/scaffolding-recipes.md) — Phase 23.1, recipe-driven project starters

## Decisions

Date-stamped choices with rationale.

- [2026-05-01 — Phase 25: Knowledge Garden](decisions/2026-05-01_phase-25-knowledge-garden.md)
- [2026-04-30 — Phase 22: CC Dispatch](decisions/2026-04-30_phase-22-dispatch.md)
- [2026-04-30 — qwen3:4b for quick-chat router](decisions/2026-04-30_qwen3-4b-quick-chat.md)
- [2026-04-30 — iOS liquid-glass dashboard](decisions/2026-04-30_ios-liquid-glass.md)

## Sources

Raw, immutable inputs at `wiki/sources/`. Listed in `log.md` and queryable via `wiki_query`. Don't enumerate them here — there will be too many.

## Quick links

- [SCHEMA.md](SCHEMA.md) — wiki maintenance contract
- [log.md](log.md) — chronological journal of significant changes

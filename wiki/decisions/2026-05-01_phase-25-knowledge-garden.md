---
name: Phase 25 — Knowledge Garden
description: Built a structured, LLM-maintained wiki at ~/AI_Agent/wiki/ to stop burying findings in chat history.
type: decision
last_updated: 2026-05-01
sources:
  - 2026-05-01_llm-wiki-pattern-research.md
tags: [phase-25, wiki, knowledge, karpathy]
---

# 2026-05-01 — Phase 25: Knowledge Garden

## Decision
Implement a three-layer knowledge garden under `~/AI_Agent/wiki/` following the Karpathy LLM-wiki pattern:
- **Layer 1**: `sources/` — append-only raw inputs (articles, dispatch results, audits, research).
- **Layer 2**: `entities/`, `concepts/`, `decisions/` — curated, LLM-maintained synthesis.
- **Layer 3**: `SCHEMA.md` + `index.md` — the rules + navigation.

Plus: four LangGraph tools (`wiki_query`, `wiki_ingest`, `wiki_update`, `wiki_create`), a background extractor (`workers/wiki_extractor.py`), Telegram prefix commands (`wiki <q>`, `ingest <url>`), and a dashboard surface in the Memory tab.

## Why
Yesterday's audit (2026-04-30) produced specific BidWatt + Nexus findings worth keeping. They got buried in chat history within hours. Same thing happens to dispatch results, research artifacts, and decisions every time. Generic chat-history RAG is too noisy — the answer surfaces in a sea of unrelated turns.

A structured, schema-driven wiki gives durable, queryable, cross-linked reference material. Schema-driven means the rules live in one human-editable file, so the LLM curator doesn't drift.

## How to apply
- **When something worth remembering happens, ingest it.** New audit, new research, new dispatch result → `wiki_ingest`. The extractor will fan it out.
- **When you make a real decision, write a `decisions/YYYY-MM-DD_*.md`.** Don't bury it in an entity page.
- **Cross-link aggressively.** Every entity → related projects, decisions. Every decision → entities/concepts it touches.
- **Cite the wiki when you answer from it.** Relative path, like `wiki/entities/bidwatt.md`.
- **Read SCHEMA.md before editing the wiki.** It's the contract.

## Related
- Concept: [LLM Wiki Pattern](../concepts/llm-wiki-pattern.md)
- Schema: [SCHEMA.md](../SCHEMA.md)
- Source: [llm_wiki_pattern.md research](../sources/2026-05-01_llm-wiki-pattern-research.md)

---
name: LLM Wiki Pattern
description: Karpathy-inspired pattern — three-layer knowledge garden (raw sources → curated wiki → schema) maintained automatically by an LLM agent.
type: concept
last_updated: 2026-05-01
sources:
  - 2026-05-01_llm-wiki-pattern-research.md
tags: [phase-25, knowledge, wiki, karpathy, pattern]
---

# LLM Wiki Pattern

The pattern this knowledge garden implements. Inspired by Andrej Karpathy's gist on LLM-maintained personal wikis, adapted to Nexus's own substrate.

## The problem it solves

Findings get buried in chat history. Audits run, dispatches finish, decisions get made — and then they evaporate the next time the conversation rolls. RAG over chat logs is too noisy. A structured, curated wiki is the missing layer.

We hit this exact failure on 2026-04-30 (yesterday): an audit produced specific bidwatt + nexus findings that were valuable enough to save but ended up scattered across run-logs and never got turned into reference material. Phase 25 is the fix.

## The three layers

| Layer | Mutability | What it holds |
|---|---|---|
| **1 — Sources** (`wiki/sources/`) | Append-only, immutable | Raw inputs: articles, PDFs, transcripts, screenshots, audit results, dispatch result JSON, research artifacts. |
| **2 — The wiki** (`wiki/entities/`, `wiki/concepts/`, `wiki/decisions/`) | LLM-maintained | Curated synthesis: one .md per noun, pattern, or decision. Cross-linked. |
| **3 — Schema** (`wiki/SCHEMA.md`, `wiki/index.md`) | Human-edited | Tells the LLM HOW to maintain layers 1 and 2. Equivalent of CLAUDE.md but for the wiki. |

Without layer 3, the LLM drifts. Without layer 1, the wiki has no provenance. Without layer 2, you've just got a folder of dead files.

## Maintenance loop

```
new source lands in wiki/sources/
    │
    ▼
workers/wiki_extractor.py picks it up
    │
    ▼
dispatches a small Claude Code job (10-min budget):
    "read SCHEMA.md, read this source, update relevant pages"
    │
    ▼
relevant entities/concepts/decisions get edited
log.md gets one line describing what changed
last_updated bumps
```

The extractor doesn't try to be clever — it delegates curation to Claude Code with the schema as the guidebook. Schema-driven means the rules live in one file you can read and edit.

## Querying

`wiki_query(question)` searches:
1. Filename / slug match (cheapest).
2. Frontmatter `name`, `description`, `tags` match.
3. Body semantic similarity via the `wiki` Chroma collection (separate from `nexus-memory` so wiki hits aren't drowned out by raw memory chunks).

Returns ranked hits with file path, snippet, and `last_updated`.

## Why this works (and where it can go wrong)

**Works because**:
- The schema makes the curation contract explicit and re-readable.
- Sources are immutable — provenance is never lost.
- The LLM does the boring synthesis work the human won't.
- Cross-links create a graph that gets richer over time.

**Goes wrong when**:
- Schema isn't followed (wiki devolves into a junk drawer).
- Updates aren't triggered on relevant events (wiki goes stale).
- Pages are written like chat replies instead of reference material.
- No one reads `index.md` and pages become orphans.

The schema explicitly calls these out as anti-patterns — see `wiki/SCHEMA.md`.

## Related
- [Decision: 2026-05-01 Phase 25 — Knowledge Garden](../decisions/2026-05-01_phase-25-knowledge-garden.md)
- Source: [research/llm_wiki_pattern.md](../sources/2026-05-01_llm-wiki-pattern-research.md)

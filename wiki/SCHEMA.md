---
name: Knowledge Garden Schema
description: How Nexus and Claude Code maintain the wiki at ~/AI_Agent/wiki/
type: schema
last_updated: 2026-05-01
---

# Knowledge Garden — Schema

Read this first whenever you touch `~/AI_Agent/wiki/`. It defines the contract every agent (Nexus, Claude Code dispatches, the wiki extractor worker) must follow so the garden stays coherent instead of devolving into another junk drawer.

## Why this exists

Findings get buried in chat history. Audits, decisions, dispatch results, research notes — they all evaporate unless something deliberate carries them forward. The wiki is that deliberate place. Inspired by Karpathy's LLM Wiki gist (see `concepts/llm-wiki-pattern.md`).

## Three layers

```
~/AI_Agent/wiki/
├── SCHEMA.md          ← you are here (the rules)
├── index.md           ← top-level navigation, gets injected into Nexus system prompt
├── log.md             ← chronological journal of significant updates
├── sources/           ← LAYER 1: raw, immutable inputs (articles, PDFs, transcripts, dispatch results)
├── entities/          ← LAYER 2a: people, projects, services, tools (one .md per noun)
├── concepts/          ← LAYER 2b: patterns, architectures, rationales
└── decisions/         ← LAYER 2c: date-stamped decision log with rationale
```

- **Layer 1 (sources/)** is append-only. Never edit. Naming: `YYYY-MM-DD_<short_descriptor>.{md,json,pdf,...}`.
- **Layer 2 (entities/, concepts/, decisions/)** is the curated wiki. Updated whenever a new source contradicts, supersedes, or expands existing knowledge.
- **Layer 3** is this file plus `index.md` — the meta layer that tells future agents how to navigate and maintain layers 1 and 2.

## Frontmatter standards

Every file under `entities/`, `concepts/`, `decisions/`, and `sources/` MUST start with YAML frontmatter.

### sources/<file> frontmatter
```yaml
---
ingested_at: 2026-05-01T14:23:00-07:00     # ISO-8601 with offset
source_url: https://gist.github.com/...    # optional, omit if local-origin
source_type: dispatch_result | research | article | transcript | screenshot | pdf | manual
descriptor: phase-22-dispatch-system       # the short_descriptor portion of the filename
---
```

### entities/, concepts/, decisions/ frontmatter
```yaml
---
name: BidWatt                               # canonical display name
description: One-line summary of what this page is about
type: entity | concept | decision
last_updated: 2026-05-01
sources:                                    # list of source files this page draws from
  - 2026-05-01_phase-22-dispatch-result.json
  - 2026-04-30_audit-bidwatt.md
tags: [project, nextjs, supabase, vercel]   # used for search + filtering
---
```

## Naming conventions

| Layer | Pattern | Example |
|---|---|---|
| sources | `YYYY-MM-DD_<kebab-descriptor>.<ext>` | `2026-05-01_phase-25-kickoff.md` |
| entities | `<lowercase-noun>.md` | `colton.md`, `bidwatt.md`, `nexus.md` |
| concepts | `<kebab-concept>.md` | `dispatch-system.md`, `intent-routing.md` |
| decisions | `YYYY-MM-DD_<kebab-decision>.md` | `2026-04-30_phase-22-dispatch.md` |

Use lowercase, hyphenate, keep slugs short and stable. Renaming a page means updating every cross-reference — avoid it.

## Cross-reference rules

Use markdown links with `[[wikilink-style]]` semantics rendered as standard relative paths. Two acceptable forms:

```markdown
See [BidWatt](../entities/bidwatt.md) for the project.
See [[bidwatt]] for the project.
```

The `[[name]]` shorthand is resolved by the wiki extractor by searching all of `entities/`, `concepts/`, `decisions/` for a file whose stem matches `name` (or whose frontmatter `name` matches case-insensitively). Prefer the explicit relative path when writing manually.

Cross-link aggressively. Every entity page should mention related projects, owners, and decisions. Every decision page should link to the entities and concepts it touches. Every concept page should cite the sources behind it.

## Update triggers

A wiki page must be updated when ANY of these happens:

1. A new source lands in `sources/` that adds, contradicts, or supersedes information on an existing page.
2. A `cc_dispatch` completes and its result implies a state change (new service deployed, schema migrated, decision reversed).
3. The user explicitly says "remember that…" or "update the wiki on…".
4. A decision is made — write a new `decisions/YYYY-MM-DD_*.md` immediately, don't wait.
5. An entity changes phase, ownership, or status (project goes from PLANNING → BUILDING → SHIPPED).

If a triggering event happens and you don't update, you are violating the schema. The whole point of the garden is automatic maintenance.

## Conflict resolution

When a new source contradicts existing wiki content:

1. Update the wiki page to reflect the new state.
2. Bump `last_updated`.
3. Append a one-line entry to `log.md` describing the change: `2026-05-01 — bidwatt.md: Vercel deploy now live (was: pending)`.
4. Append the conflicting source's filename to the page's `sources:` frontmatter.
5. If the contradiction is significant (architectural, scope, ownership), write a new `decisions/YYYY-MM-DD_*.md` with rationale.

Never delete superseded content silently. Either replace it (and log the change) or annotate it as historical with a `> NOTE: superseded YYYY-MM-DD` block.

## What goes where

| If the thing is… | …it belongs in |
|---|---|
| A person, project, service, app, or tool | `entities/` |
| A pattern, architecture, rationale, or how-it-works explanation | `concepts/` |
| A choice made on a specific date (with reasoning) | `decisions/YYYY-MM-DD_*.md` |
| Raw input (article, PDF, transcript, dispatch result, screenshot, audit) | `sources/YYYY-MM-DD_*` |
| A code-level convention | NOT here — put it in `CLAUDE.md` |
| Ephemeral task state | NOT here — put it in `STATE.md` or the task queue |
| User personal facts (preferences, role, communication style) | `entities/colton.md` |

If you can't decide between `entities/` and `concepts/`: ask "is this a noun a human would name?" If yes → entity. If no → concept.

## Maintenance loop (for the wiki extractor worker)

`workers/wiki_extractor.py` watches `wiki/sources/` for new files. On each new source:

1. Parse frontmatter and body.
2. Identify which entity / concept / decision pages the source touches (by tag match, name match, or LLM classification).
3. Dispatch a small Claude Code job (10-min budget) with this prompt template:
   ```
   New source landed at wiki/sources/<file>. Read SCHEMA.md, then update the
   relevant pages under entities/, concepts/, or decisions/. Append one line
   to log.md describing what changed. Bump last_updated. Don't touch
   unrelated pages.
   ```
4. The Claude Code dispatch is itself the worker — don't re-implement the LLM call inline.

If no relevant page exists, the worker creates one (entity if the source names a noun, concept otherwise) using the bootstrap template.

## Anti-patterns

- **Restating the source verbatim** — the source is already in `sources/`. The wiki page is the synthesis, not the copy.
- **Long page titles** — slug is the title. Keep it short.
- **Burying decisions in entity pages** — every decision deserves its own dated file. Cross-link.
- **Editing `sources/`** — never. They are immutable.
- **Skipping `last_updated`** — search and freshness checks rely on it.
- **No cross-links** — an island page is a useless page.
- **Writing wiki pages as if they're chat replies** — they are reference material. Past tense for facts, present tense for current state, terse.

## Index discipline

`index.md` is injected into the Nexus system prompt. Keep it under 200 lines. It lists every entity, concept, and recent decision with a one-line hook each. When you add a new page, add a line to `index.md` in the correct section. When you remove one, remove the line.

## Querying

Use `wiki_query(question)` from any agent path. It searches by:
1. Filename / slug match (fast)
2. Frontmatter `name`, `description`, `tags` match
3. Body semantic similarity via the `wiki` Chroma collection

Returns ranked hits with file path, snippet, and last_updated. Always cite the wiki page (relative path) when answering from it.

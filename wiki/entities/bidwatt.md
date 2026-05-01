---
name: BidWatt
description: Construction bid management app for [Colton](colton.md). Next.js + Supabase. Vercel deploy pending.
type: entity
last_updated: 2026-05-01
sources: []
tags: [project, nextjs, supabase, vercel, construction]
---

# BidWatt

Colton's side project for managing construction bids end-to-end at [Irex Argus](argus.md).

## Stack
- **Frontend**: Next.js (App Router)
- **Backend**: Supabase (Postgres + auth + storage)
- **Deploy target**: Vercel — pending as of 2026-05-01
- **Repo**: `~/Dev/cwatt-bidboard/`

## Status
- Local development active.
- Vercel deploy not yet live — when it ships, update this page and add a `decisions/` entry.

## Nexus integration
- Read-only Supabase access via `bidwatt_tool.py` (Phase 16.4): `bidwatt_list_bids`, `bidwatt_get_bid(id)`, `bidwatt_search_bids(query)`. No writes.
- Credentials live in `~/AI_Agent/.env`.

## Related
- Owner: [Colton](colton.md)
- Day-job context: [Argus](argus.md)

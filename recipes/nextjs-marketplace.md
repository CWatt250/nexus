# `nextjs-marketplace` — Multi-sided marketplace (Phase 23.1 → Phase 24)

The thorough recipe. This is the base for **Phase 24 — Shoppable Video Marketplace**. Stops short of Stripe Connect API integration and full UI.

## Stack
- Next.js 14 + Tailwind + shadcn shim (same base as the other Next.js recipes)
- `@supabase/ssr` + `@supabase/supabase-js` for auth + DB
- `stripe` placeholder client (Phase 24 wires the actual flows)

## Domain model

### User roles
- **buyer**, **seller**, **admin** (`user_role` enum in Postgres + TS type)

### Listings
- Title, description, `price_cents`, currency, category, video URL, thumbnail URL, duration, status (`draft | published | sold | archived`).

### Orders — 7-state machine
```
awaiting_payment ──(stripe webhook)──> paid ──> in_progress ──> delivered ──> completed
       │                                  │           │              │
       └──> refunded                      └──> refunded   └──> disputed ──> {refunded, completed}
```
Source: `src/lib/order-state.ts` exports `canTransition(from, to)`. Every transition MUST be validated server-side in Phase 24.

### Reviews
- One per order, 3 dimensions (1-5 each): **reliability**, **speed**, **quality**, plus a free-form comment.

### Reputation (3-dimension averages → 4-tier badge)
- bronze: <5 reviews **or** composite < 4.0
- silver: composite ≥ 4.0
- gold: composite ≥ 4.5 **and** ≥ 20 reviews
- platinum: composite ≥ 4.8 **and** ≥ 50 reviews

Pure function in `src/lib/reputation.ts` ready to call from a database trigger / scheduled job in Phase 24.

### Notifications
Generic `kind / payload jsonb / read_at` table — drop-in for whatever delivery channel (push / email / in-app).

## Generated layout (recipe additions)
```
<name>/
├── .env.example                 # Supabase + Stripe Connect keys
├── README.md
├── docs/
│   ├── architecture.md          # roles + state machine + reputation
│   └── stripe-connect.md        # Phase 24 follow-up checklist
├── db/
│   ├── schema.sql               # 7 tables + view + 4 enums
│   └── seed.sql                 # 3 sample profiles
└── src/
    ├── app/
    │   ├── page.tsx                              # landing
    │   ├── (app)/marketplace/page.tsx            # listing grid + search + filter
    │   ├── (app)/seller/[id]/page.tsx            # seller profile + reputation
    │   ├── (app)/listing/[id]/page.tsx           # listing detail + buy button stub
    │   └── (app)/orders/page.tsx                 # buyer order history
    ├── components/
    │   ├── listing-card.tsx
    │   ├── search-bar.tsx
    │   ├── filter-panel.tsx
    │   ├── reputation-badges.tsx                 # 3 dimensions, 1-5 each
    │   └── tier-badge.tsx                        # bronze / silver / gold / platinum
    └── lib/
        ├── types.ts                              # all domain types
        ├── supabase.ts
        ├── stripe.ts                             # placeholder + Connect URL constants
        ├── order-state.ts                        # transition matrix + canTransition()
        └── reputation.ts                         # computeAverages + computeTier
```

## Required services
- **Supabase project** with the schema applied: `psql $DATABASE_URL -f db/schema.sql`.
- **Stripe account** with Connect enabled (Phase 24's job).

## What the recipe does NOT do (Phase 24 owns)
- Stripe Connect onboarding flow (seller signup → Express account → onboarding URL).
- Stripe webhook handlers (`payment_intent.succeeded`, `charge.refunded`, `account.updated`, `charge.dispute.created`).
- Real Supabase queries — every page uses placeholder arrays so type-checking passes.
- Row-Level Security policies on the Postgres tables.
- Video + thumbnail upload flow.
- Notification dispatch (push/email).

## Smoke targets
- Skip-install: ~10 s
- Full install: ~8–10 min

## Phase 24 → Phase 25 trajectory
- Phase 24: real flows on top of this skeleton (auth → listing CRUD → order placement → Stripe Connect → reviews → reputation triggers).
- Phase 25: knowledge garden integration (sellers' RAG corpus from past listings).

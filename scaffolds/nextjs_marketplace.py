"""nextjs-marketplace: full multi-sided marketplace skeleton (Phase 23.1 → Phase 24).

Same Next.js 14 + Tailwind + shadcn base as the SaaS recipe, plus:
  - User profiles (sellers, buyers, admins)
  - Listings model (videos with metadata)
  - Orders state machine
  - Reviews + 3-dimension reputation (reliability / speed / quality)
  - Tier badge system (4 tiers)
  - Notifications model
  - Stripe Connect placeholder (NOT a full integration — Phase 24's job)
  - Search/filter base components

Schema lives in `db/schema.sql` so Supabase or any Postgres can adopt it.
"""
from __future__ import annotations

from .nextjs_base import make_nextjs_recipe


def _templates(ctx: dict) -> dict[str, str]:
    return {
        "src/app/page.tsx": _LANDING,
        "src/app/(app)/marketplace/page.tsx": _MARKET_INDEX,
        "src/app/(app)/seller/[id]/page.tsx": _SELLER_PROFILE,
        "src/app/(app)/listing/[id]/page.tsx": _LISTING_DETAIL,
        "src/app/(app)/orders/page.tsx": _ORDERS_LIST,
        "src/components/listing-card.tsx": _LISTING_CARD,
        "src/components/search-bar.tsx": _SEARCH_BAR,
        "src/components/filter-panel.tsx": _FILTER_PANEL,
        "src/components/reputation-badges.tsx": _REPUTATION_BADGES,
        "src/components/tier-badge.tsx": _TIER_BADGE,
        "src/lib/supabase.ts": _SUPABASE_CLIENT,
        "src/lib/stripe.ts": _STRIPE_PLACEHOLDER,
        "src/lib/types.ts": _TS_TYPES,
        "src/lib/order-state.ts": _ORDER_STATE,
        "src/lib/reputation.ts": _REPUTATION_LOGIC,
        "db/schema.sql": _SQL_SCHEMA,
        "db/seed.sql": _SQL_SEED,
        ".env.example": _ENV_EXAMPLE,
        "README.md": _README.format(name=ctx["name"]),
        "docs/architecture.md": _ARCHITECTURE_DOC,
        "docs/stripe-connect.md": _STRIPE_DOC,
    }


_LANDING = """import Link from "next/link";

export default function Landing() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <section className="mx-auto max-w-3xl px-6 py-24">
        <h1 className="text-5xl font-bold tracking-tight">Marketplace</h1>
        <p className="mt-4 text-lg text-slate-400">
          Buy and sell short videos. Built on Next.js 14 + Supabase + Stripe Connect.
        </p>
        <Link href="/marketplace" className="mt-10 inline-block rounded-md bg-cyan-500 px-5 py-3 font-medium text-slate-950">
          Browse listings
        </Link>
      </section>
    </main>
  );
}
"""

_MARKET_INDEX = """import ListingCard from "@/components/listing-card";
import SearchBar from "@/components/search-bar";
import FilterPanel from "@/components/filter-panel";

export default function MarketplaceHome() {
  // TODO: replace with a real query against listings.
  const listings = [
    { id: "1", title: "Drone shot of a sunset", price: 49, sellerId: "s1", sellerName: "Alex", reliability: 5, speed: 4, quality: 5, tier: "gold" },
    { id: "2", title: "Time-lapse city skyline",  price: 99, sellerId: "s2", sellerName: "Jamie", reliability: 4, speed: 5, quality: 4, tier: "silver" },
  ];
  return (
    <main className="min-h-screen bg-slate-950 p-8 text-slate-100">
      <h1 className="text-3xl font-bold">Marketplace</h1>
      <div className="mt-4 flex gap-4">
        <SearchBar />
        <FilterPanel />
      </div>
      <div className="mt-8 grid gap-4 md:grid-cols-3">
        {listings.map((l) => <ListingCard key={l.id} listing={l} />)}
      </div>
    </main>
  );
}
"""

_SELLER_PROFILE = """import ReputationBadges from "@/components/reputation-badges";
import TierBadge from "@/components/tier-badge";

export default function SellerProfile({ params }: { params: { id: string } }) {
  // TODO: fetch real seller from Supabase; this is placeholder data.
  const seller = { id: params.id, name: "Alex", bio: "Aerial / time-lapse", reliability: 5, speed: 4, quality: 5, tier: "gold" as const };
  return (
    <main className="min-h-screen bg-slate-950 p-8 text-slate-100">
      <div className="flex items-center gap-3">
        <h1 className="text-3xl font-bold">{seller.name}</h1>
        <TierBadge tier={seller.tier} />
      </div>
      <p className="mt-2 text-slate-400">{seller.bio}</p>
      <ReputationBadges reliability={seller.reliability} speed={seller.speed} quality={seller.quality} />
    </main>
  );
}
"""

_LISTING_DETAIL = """import ReputationBadges from "@/components/reputation-badges";

export default function ListingDetail({ params }: { params: { id: string } }) {
  // TODO: real fetch; this is placeholder.
  const l = { id: params.id, title: "Drone shot", price: 49, description: "4K drone footage of a desert sunset.", sellerName: "Alex", reliability: 5, speed: 4, quality: 5 };
  return (
    <main className="min-h-screen bg-slate-950 p-8 text-slate-100">
      <h1 className="text-3xl font-bold">{l.title}</h1>
      <p className="mt-2 text-slate-400">by {l.sellerName}</p>
      <p className="mt-4">{l.description}</p>
      <p className="mt-6 text-2xl font-semibold">${l.price}</p>
      <button className="mt-4 rounded-md bg-cyan-500 px-5 py-2 font-medium text-slate-950">
        Buy now
      </button>
      <ReputationBadges reliability={l.reliability} speed={l.speed} quality={l.quality} />
    </main>
  );
}
"""

_ORDERS_LIST = """export default function Orders() {
  // TODO: real fetch; this is placeholder.
  const orders = [
    { id: "o1", listing: "Drone shot", state: "delivered", price: 49 },
    { id: "o2", listing: "Time-lapse",  state: "in_progress", price: 99 },
  ];
  return (
    <main className="min-h-screen bg-slate-950 p-8 text-slate-100">
      <h1 className="text-3xl font-bold">My orders</h1>
      <ul className="mt-6 space-y-2">
        {orders.map((o) => (
          <li key={o.id} className="flex justify-between rounded-md border border-slate-800 bg-slate-900 p-4">
            <span>{o.listing}</span>
            <span className="text-slate-400">{o.state}</span>
            <span>${o.price}</span>
          </li>
        ))}
      </ul>
    </main>
  );
}
"""

_LISTING_CARD = """import Link from "next/link";
import TierBadge from "@/components/tier-badge";

interface Listing {
  id: string;
  title: string;
  price: number;
  sellerId: string;
  sellerName: string;
  reliability: number;
  speed: number;
  quality: number;
  tier: "bronze" | "silver" | "gold" | "platinum";
}

export default function ListingCard({ listing }: { listing: Listing }) {
  return (
    <Link href={`/listing/${listing.id}`} className="block rounded-md border border-slate-800 bg-slate-900 p-4 hover:border-cyan-500">
      <div className="flex justify-between">
        <h3 className="font-semibold">{listing.title}</h3>
        <TierBadge tier={listing.tier} />
      </div>
      <p className="mt-2 text-sm text-slate-400">by {listing.sellerName}</p>
      <p className="mt-3 text-lg font-semibold">${listing.price}</p>
    </Link>
  );
}
"""

_SEARCH_BAR = """\"use client\";

import { useState } from \"react\";

export default function SearchBar() {
  const [q, setQ] = useState(\"\");
  return (
    <input
      type=\"text\"
      value={q}
      onChange={(e) => setQ(e.target.value)}
      placeholder=\"Search listings...\"
      className=\"flex-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-2\"
    />
  );
}
"""

_FILTER_PANEL = """\"use client\";

import { useState } from \"react\";

const CATEGORIES = [\"all\", \"aerial\", \"timelapse\", \"motion-graphics\", \"interview\"] as const;

export default function FilterPanel() {
  const [cat, setCat] = useState<typeof CATEGORIES[number]>(\"all\");
  return (
    <select
      value={cat}
      onChange={(e) => setCat(e.target.value as typeof CATEGORIES[number])}
      className=\"rounded-md border border-slate-700 bg-slate-900 px-3 py-2\"
    >
      {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
    </select>
  );
}
"""

_REPUTATION_BADGES = """interface Props {
  reliability: number;
  speed: number;
  quality: number;
}

const dim = (label: string, score: number) => (
  <div key={label} className="flex flex-col items-center rounded-md border border-slate-800 bg-slate-900 px-4 py-2">
    <span className="text-xs text-slate-400">{label}</span>
    <span className="text-lg font-semibold">{score}/5</span>
  </div>
);

export default function ReputationBadges({ reliability, speed, quality }: Props) {
  return (
    <div className="mt-4 flex gap-3">
      {dim("Reliability", reliability)}
      {dim("Speed", speed)}
      {dim("Quality", quality)}
    </div>
  );
}
"""

_TIER_BADGE = """const STYLES = {
  bronze:   "bg-amber-900/40 text-amber-300 border-amber-800",
  silver:   "bg-slate-700/40 text-slate-200 border-slate-500",
  gold:     "bg-yellow-700/40 text-yellow-200 border-yellow-500",
  platinum: "bg-cyan-700/40 text-cyan-200 border-cyan-400",
} as const;

export default function TierBadge({ tier }: { tier: keyof typeof STYLES }) {
  return (
    <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${STYLES[tier]}`}>
      {tier}
    </span>
  );
}
"""

_SUPABASE_CLIENT = """import { createBrowserClient } from \"@supabase/ssr\";

export const supabase = createBrowserClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
);
"""

_STRIPE_PLACEHOLDER = """// Stripe Connect wiring lives in Phase 24. This placeholder lets
// imports compile and documents the secret-key surface so we don't
// drift later. Do NOT ship without the full Phase 24 integration.
import Stripe from \"stripe\";

export const stripe = new Stripe(process.env.STRIPE_SECRET_KEY ?? \"\", {
  apiVersion: \"2024-06-20\",
});

export const STRIPE_CONNECT_RETURN_URL =
  process.env.STRIPE_CONNECT_RETURN_URL ?? \"http://localhost:3000/seller/onboarding/return\";
export const STRIPE_CONNECT_REFRESH_URL =
  process.env.STRIPE_CONNECT_REFRESH_URL ?? \"http://localhost:3000/seller/onboarding/refresh\";
"""

_TS_TYPES = """export type UserRole = \"buyer\" | \"seller\" | \"admin\";

export interface UserProfile {
  id: string;
  email: string;
  display_name: string;
  role: UserRole;
  stripe_account_id: string | null;
  created_at: string;
}

export type ListingStatus = \"draft\" | \"published\" | \"sold\" | \"archived\";

export interface Listing {
  id: string;
  seller_id: string;
  title: string;
  description: string;
  price_cents: number;
  currency: string;
  category: string;
  video_url: string;
  thumbnail_url: string;
  duration_seconds: number;
  status: ListingStatus;
  created_at: string;
}

export type OrderState =
  | \"awaiting_payment\"
  | \"paid\"
  | \"in_progress\"
  | \"delivered\"
  | \"completed\"
  | \"refunded\"
  | \"disputed\";

export interface Order {
  id: string;
  buyer_id: string;
  seller_id: string;
  listing_id: string;
  state: OrderState;
  amount_cents: number;
  currency: string;
  stripe_payment_intent_id: string | null;
  created_at: string;
  delivered_at: string | null;
  completed_at: string | null;
}

export interface Review {
  id: string;
  order_id: string;
  reviewer_id: string;
  reviewee_id: string;
  reliability: number;  // 1-5
  speed: number;
  quality: number;
  comment: string;
  created_at: string;
}

export type Tier = \"bronze\" | \"silver\" | \"gold\" | \"platinum\";

export interface SellerReputation {
  seller_id: string;
  reliability_avg: number;
  speed_avg: number;
  quality_avg: number;
  total_reviews: number;
  tier: Tier;
}
"""

_ORDER_STATE = """import type { OrderState } from \"./types\";

// Allowed transitions. Anything not listed here MUST be rejected by
// the API layer when it lands in Phase 24.
export const ORDER_TRANSITIONS: Record<OrderState, OrderState[]> = {
  awaiting_payment: [\"paid\", \"refunded\"],
  paid:             [\"in_progress\", \"refunded\"],
  in_progress:      [\"delivered\", \"disputed\", \"refunded\"],
  delivered:        [\"completed\", \"disputed\"],
  completed:        [],
  refunded:         [],
  disputed:         [\"refunded\", \"completed\"],
};

export function canTransition(from: OrderState, to: OrderState): boolean {
  return ORDER_TRANSITIONS[from]?.includes(to) ?? false;
}
"""

_REPUTATION_LOGIC = """import type { Tier, Review } from \"./types\";

// 3-dimension reputation: reliability / speed / quality. Each is a 1-5
// average over all reviews for the seller. Tier is derived from the
// composite (mean of the three) AND total review count — new sellers
// stay bronze regardless of score until they have a track record.
export function computeAverages(reviews: Review[]) {
  if (reviews.length === 0) {
    return { reliability_avg: 0, speed_avg: 0, quality_avg: 0, total_reviews: 0 };
  }
  const sum = reviews.reduce(
    (acc, r) => ({ rel: acc.rel + r.reliability, spd: acc.spd + r.speed, qua: acc.qua + r.quality }),
    { rel: 0, spd: 0, qua: 0 },
  );
  const n = reviews.length;
  return {
    reliability_avg: sum.rel / n,
    speed_avg: sum.spd / n,
    quality_avg: sum.qua / n,
    total_reviews: n,
  };
}

export function computeTier(averages: { reliability_avg: number; speed_avg: number; quality_avg: number; total_reviews: number }): Tier {
  const { reliability_avg, speed_avg, quality_avg, total_reviews } = averages;
  if (total_reviews < 5) return \"bronze\";
  const composite = (reliability_avg + speed_avg + quality_avg) / 3;
  if (composite >= 4.8 && total_reviews >= 50) return \"platinum\";
  if (composite >= 4.5 && total_reviews >= 20) return \"gold\";
  if (composite >= 4.0) return \"silver\";
  return \"bronze\";
}
"""

_SQL_SCHEMA = """-- Marketplace schema. Run against your Supabase Postgres.
-- Phase 24 will own migrations; this is the initial baseline.

create extension if not exists "uuid-ossp";

create type user_role as enum ('buyer', 'seller', 'admin');
create type listing_status as enum ('draft', 'published', 'sold', 'archived');
create type order_state as enum (
  'awaiting_payment', 'paid', 'in_progress',
  'delivered', 'completed', 'refunded', 'disputed'
);
create type tier as enum ('bronze', 'silver', 'gold', 'platinum');

create table profiles (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null unique,                  -- references auth.users on Supabase
  email text not null,
  display_name text not null,
  role user_role not null default 'buyer',
  bio text,
  stripe_account_id text,
  created_at timestamptz not null default now()
);

create table listings (
  id uuid primary key default uuid_generate_v4(),
  seller_id uuid not null references profiles(id) on delete cascade,
  title text not null,
  description text,
  price_cents integer not null check (price_cents >= 0),
  currency text not null default 'usd',
  category text,
  video_url text,
  thumbnail_url text,
  duration_seconds integer,
  status listing_status not null default 'draft',
  created_at timestamptz not null default now()
);
create index listings_seller_idx on listings(seller_id);
create index listings_category_idx on listings(category);
create index listings_status_idx on listings(status);

create table orders (
  id uuid primary key default uuid_generate_v4(),
  buyer_id uuid not null references profiles(id),
  seller_id uuid not null references profiles(id),
  listing_id uuid not null references listings(id),
  state order_state not null default 'awaiting_payment',
  amount_cents integer not null,
  currency text not null default 'usd',
  stripe_payment_intent_id text,
  created_at timestamptz not null default now(),
  delivered_at timestamptz,
  completed_at timestamptz
);
create index orders_buyer_idx on orders(buyer_id);
create index orders_seller_idx on orders(seller_id);
create index orders_state_idx on orders(state);

create table reviews (
  id uuid primary key default uuid_generate_v4(),
  order_id uuid not null references orders(id) unique,  -- one review per order
  reviewer_id uuid not null references profiles(id),
  reviewee_id uuid not null references profiles(id),
  reliability integer not null check (reliability between 1 and 5),
  speed integer not null check (speed between 1 and 5),
  quality integer not null check (quality between 1 and 5),
  comment text,
  created_at timestamptz not null default now()
);
create index reviews_reviewee_idx on reviews(reviewee_id);

create table seller_reputation (
  seller_id uuid primary key references profiles(id) on delete cascade,
  reliability_avg numeric(3,2) not null default 0,
  speed_avg numeric(3,2) not null default 0,
  quality_avg numeric(3,2) not null default 0,
  total_reviews integer not null default 0,
  tier tier not null default 'bronze',
  updated_at timestamptz not null default now()
);

create table notifications (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null references profiles(id) on delete cascade,
  kind text not null,                            -- 'order_paid', 'review_left', etc.
  payload jsonb not null,
  read_at timestamptz,
  created_at timestamptz not null default now()
);
create index notifications_user_idx on notifications(user_id, read_at);

-- View: convenient join for listing cards.
create view listing_cards as
select l.*, p.display_name as seller_name, sr.tier as seller_tier,
       sr.reliability_avg, sr.speed_avg, sr.quality_avg
from listings l
join profiles p on p.id = l.seller_id
left join seller_reputation sr on sr.seller_id = l.seller_id;
"""

_SQL_SEED = """-- Optional seed data for local dev.
insert into profiles (user_id, email, display_name, role) values
  ('00000000-0000-0000-0000-000000000001', 'alex@example.com',  'Alex',  'seller'),
  ('00000000-0000-0000-0000-000000000002', 'jamie@example.com', 'Jamie', 'seller'),
  ('00000000-0000-0000-0000-000000000003', 'colton@example.com','Colton','buyer');
"""

_ENV_EXAMPLE = """# Supabase
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=

# Stripe Connect (Phase 24 wires the actual flows)
STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_CONNECT_CLIENT_ID=
STRIPE_CONNECT_RETURN_URL=
STRIPE_CONNECT_REFRESH_URL=

# App
NEXT_PUBLIC_APP_URL=http://localhost:3000
"""

_README = """# {name}

Multi-sided marketplace skeleton, scaffolded by Nexus
(`nextjs-marketplace` recipe). Phase 23.1 → Phase 24.

## What's included
- Pages: landing, marketplace index, seller profile, listing detail,
  buyer orders.
- Models (TypeScript types + Postgres schema): profiles, listings,
  orders + 7-state machine, reviews, seller_reputation, notifications.
- Components: listing-card, search-bar, filter-panel,
  reputation-badges (3-dim Reliability/Speed/Quality),
  tier-badge (4 tiers: bronze/silver/gold/platinum).
- Reputation logic (`src/lib/reputation.ts`): pure function for
  derived tier from review history.
- Stripe Connect placeholder (`src/lib/stripe.ts`) — Phase 24 wires
  the actual flows.
- Schema: `db/schema.sql` to run on your Supabase Postgres.

## What's NOT included (Phase 24's job)
- Stripe Connect onboarding flow + webhook handlers.
- Real Supabase queries / RLS policies.
- File upload (video + thumbnail) flow.
- Notification dispatch (push/email).

## Run
```bash
npm install
cp .env.example .env.local       # fill Supabase + Stripe keys
psql $DATABASE_URL -f db/schema.sql
npm run dev                       # http://localhost:3000
```

## Layout
- `src/app/` — App Router pages
- `src/components/` — shared UI
- `src/lib/` — types, Supabase client, Stripe client, order state, reputation
- `db/` — SQL schema + seed
- `docs/` — architecture + Stripe Connect notes
"""

_ARCHITECTURE_DOC = """# Marketplace architecture

## Roles
- **Buyer** — browses listings, places orders, leaves reviews.
- **Seller** — onboards via Stripe Connect, lists videos, fulfills orders, gets paid.
- **Admin** — moderates listings, resolves disputes, adjusts tiers.

## Order state machine
```
awaiting_payment ──(stripe webhook)──> paid ──> in_progress ──> delivered ──> completed
       │                                  │           │              │
       └──> refunded                      └──> refunded   └──> disputed ──> {refunded, completed}
```
Source: `src/lib/order-state.ts`. Every transition MUST be validated server-side.

## Reputation
Three independent 1-5 averages (reliability, speed, quality) plus a derived tier:
- bronze: <5 reviews OR composite < 4.0
- silver: composite >= 4.0
- gold:   composite >= 4.5 AND >= 20 reviews
- platinum: composite >= 4.8 AND >= 50 reviews

Source: `src/lib/reputation.ts`. Triggered on every new review (TODO: Phase 24
implements the trigger; for now it's a pure function ready to call).
"""

_STRIPE_DOC = """# Stripe Connect setup notes (Phase 24 follow-up)

This recipe stops at the placeholder. Phase 24 will:

1. **Onboard sellers**: build the seller-onboarding flow that creates a
   Stripe Express account, redirects to Connect onboarding, and stores
   `stripe_account_id` on the profile.

2. **Charge buyers + split**: use `stripe.checkout.sessions.create`
   with `payment_intent_data.transfer_data` to route the seller share
   to their connected account, keeping the platform fee on the
   primary account.

3. **Webhook handlers** (in `src/app/api/stripe/webhook/route.ts`):
   - `payment_intent.succeeded` → orders.state = 'paid'
   - `charge.refunded` → orders.state = 'refunded'
   - `account.updated` → profiles.stripe_account_id, kyc status

4. **Disputes**: `charge.dispute.created` → orders.state = 'disputed';
   notify both sides.

Required env (already in `.env.example`):
- `STRIPE_SECRET_KEY` (sk_test_… in dev)
- `STRIPE_PUBLISHABLE_KEY` (pk_test_…)
- `STRIPE_WEBHOOK_SECRET` (whsec_…)
- `STRIPE_CONNECT_CLIENT_ID` (ca_…)
- `STRIPE_CONNECT_RETURN_URL`, `STRIPE_CONNECT_REFRESH_URL`
"""


RECIPE = make_nextjs_recipe(
    slug="nextjs-marketplace",
    display="Next.js 14 Marketplace (multi-sided + reputation + Stripe-ready)",
    description="Multi-sided marketplace skeleton: profiles, listings, "
                "orders state machine, reviews, 3-dimension reputation, "
                "4-tier badges, notifications, Stripe Connect placeholder.",
    extra_templates=_templates,
    extra_packages=["@supabase/ssr", "@supabase/supabase-js", "stripe"],
    notes="Designed as Phase 23.1 → Phase 24 handoff. Stripe + queries "
          "are placeholders.",
)

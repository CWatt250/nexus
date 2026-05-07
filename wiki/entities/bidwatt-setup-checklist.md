# BidWatt — NIMO Setup Checklist
_Recon date: 2026-05-07 — branch HEAD: fac18d7 (2026-05-01)_

---

## What BidWatt Is

BidWatt is Colton's full-stack bid management web app built for Irex Argus, a mechanical insulation contractor with 5 branches (Pasco/Seattle/Portland/Phoenix/SLC). It tracks bids through a Kanban pipeline, a spreadsheet (Bid Board), a calendar view, and a reports layer, with role-based access for Estimators, Branch Managers, and Admins. The stack is Next.js 16 App Router + Supabase (PostgreSQL + RLS), styled with shadcn/ui and Tailwind v4, and was being developed on Windows (`C:\Dev\cwatt-bidboard`). It has never been deployed to Vercel — it runs locally only so far.

---

## Stack Confirmed

| Layer | Technology | Notes |
|-------|-----------|-------|
| Framework | Next.js 16.2.1 (App Router, Turbopack) | `next.config.ts` is basically empty |
| React | 19.2.4 | |
| Language | TypeScript 5, strict mode | `tsconfig.json` target ES2017, bundler moduleResolution |
| Database | Supabase (PostgreSQL + RLS) | `@supabase/supabase-js ^2.100.0`, `@supabase/ssr ^0.9.0` |
| UI | shadcn/ui (base-nova style) + lucide-react | `components.json` present |
| Styling | Tailwind CSS v4 + tw-animate-css | |
| Charts | Recharts ^3.8.1 | |
| Tables | TanStack React Table ^8.21.3 | |
| Calendar | react-big-calendar ^1.19.4 | |
| DnD | @hello-pangea/dnd ^18.0.1 | Kanban drag-drop |
| Forms | react-hook-form + @hookform/resolvers + zod | |
| Deployment | **Not deployed** | No vercel.json, no Vercel project linked |
| Node version required | **Node 22 LTS** (recommended) or 20 LTS minimum | `@types/node ^20`, Next.js 16 + React 19 = Node 22 |

---

## Migrations State

**17 migrations in `supabase/migrations/`** — CLAUDE.md only documents 001–009. Migrations 010–017 were added during April 2026 development.

| # | File | Status |
|---|------|--------|
| 001 | `001_initial_schema.sql` | Documented |
| 002 | `002_update_scope_enum.sql` | Documented |
| 003 | `003_bid_line_items.sql` | Documented |
| 004 | `004_branch_names.sql` | Documented |
| 005 | `005_roles_and_branches.sql` | Documented |
| 006 | `006_rls_role_policies.sql` | Documented |
| 007 | `007_activity_log.sql` | Documented |
| 008 | `008_bid_notes.sql` | Documented |
| 009 | `009_bid_clients.sql` | Documented |
| 010 | `010_permissions.sql` | **Undocumented — added after CLAUDE.md was written** |
| 011 | `011_clients.sql` | **Undocumented** — full clients table (replaces junction?) |
| 012 | `012_bid_documents.sql` | **Undocumented** — document upload support |
| 013 | `013_document_categories.sql` | **Undocumented** — categories for documents |
| 014 | `014_seed_clients.sql` | **Undocumented** — seeds client data |
| 015 | `015_jurisdiction_map.sql` | **Undocumented** — jurisdiction map tables |
| 016 | `016_drop_jurisdiction_tables.sql` | **Undocumented** — DROPS what 015 created (feature was rolled back) |
| 017 | `017_add_location_mike.sql` | **Undocumented** — adds Project Location + MIKE # fields |

**Risk flags:**
- Migrations 010–017 are **not documented** in CLAUDE.md — if the production Supabase hasn't had all 17 run, data will be missing columns (location, MIKE #, documents, clients, permissions).
- Migration 015 creates jurisdiction tables that 016 immediately drops — confirms the jurisdiction map feature (PR #61, still open) was partially reverted.
- Run all 17 in order in the Supabase SQL editor to ensure prod is current.

---

## Required Env Vars

No `.env.example` exists in the repo. Variables are documented in `CLAUDE.md`.

| Variable | Value / Where to Get It |
|----------|------------------------|
| `NEXT_PUBLIC_SUPABASE_URL` | `https://cbntiiixrlxkdxafxivl.supabase.co` — Supabase dashboard → project settings → API → Project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase dashboard → project settings → API → `anon public` key |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase dashboard → project settings → API → `service_role secret` key — **never expose client-side** |

That's the full set — only 3 vars needed for local dev. No other env vars referenced in CLAUDE.md.

---

## Tonight Setup Commands

```bash
# 1. Ensure nvm is available
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc   # or restart terminal

# 2. Install Node 22 LTS
nvm install 22
nvm use 22
node -v  # should print v22.x.x

# 3. Clone the repo
mkdir -p ~/Dev && cd ~/Dev
git clone https://github.com/CWatt250/cwatt-bidboard.git
cd cwatt-bidboard

# 4. Install dependencies
npm install

# 5. Create local env file  (no .env.example exists — create manually)
cat > .env.local << 'EOF'
NEXT_PUBLIC_SUPABASE_URL=https://cbntiiixrlxkdxafxivl.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=PASTE_ANON_KEY_HERE
SUPABASE_SERVICE_ROLE_KEY=PASTE_SERVICE_ROLE_KEY_HERE
EOF

# 6. Paste your keys (open Supabase dashboard → project settings → API)
#    Replace PASTE_ANON_KEY_HERE and PASTE_SERVICE_ROLE_KEY_HERE above

# 7. Check Supabase is awake (free tier pauses after 7 days idle)
#    Go to https://supabase.com and resume project if paused

# 8. Verify all 17 migrations have run in Supabase SQL editor
#    Run any missing ones from supabase/migrations/ in numeric order

# 9. Start dev server
npm run dev
# → Open http://localhost:3000
# → Login: wattattack@yahoo.com

# Optional: type-check
npx tsc --noEmit
```

---

## What's Blocking Deploy

1. **Never deployed to Vercel** — no `vercel.json`, no project linked. First deploy will need `vercel link` + `vercel env add` for all 3 vars.
2. **PR #61 is still open** — "feat: add jurisdiction map sidebar and calculator components" (branch `worktree-agent-af963903`, created 2026-04-17). The corresponding DB tables were created then dropped (migrations 015 → 016), so this PR may have stale code referencing tables that no longer exist. Don't merge without reviewing.
3. **No .env.example** — whoever sets this up next has no file to copy. Consider adding one.
4. **Supabase free tier may be paused** — if the project has been idle since the Windows machine was left behind, resume it at supabase.com before running the app.
5. **CLAUDE.md migration list is stale** — only documents 001–009 of 17. If Supabase prod hasn't had 010–017 run, features like Project Location, MIKE #, and document uploads will be broken.

---

## Recommended First Edit Session

1. **Add `.env.example`** — 10-minute task. Just the 3 var names with placeholder values. Prevents this same confusion next time.
2. **Audit PR #61** — Check if `components/jurisdiction/Sidebar.tsx` and `Calculator.tsx` reference any tables that migration 016 dropped. Either finish it or close it.
3. **Update CLAUDE.md migrations list** — Extend to document migrations 010–017 so the context file matches reality.
4. **First Vercel deploy** — Run `npx vercel link`, add env vars via `vercel env add`, then `npx vercel --prod`. This is low-risk since no prod traffic yet.

---

**What Colton needs to gather tonight from cloud dashboards before running setup commands:**

Open Supabase → project settings → API and copy the **Anon Key** and **Service Role Key** for project `cbntiiixrlxkdxafxivl.supabase.co` — those are the only two secrets not already known.

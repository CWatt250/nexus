# `nextjs-dashboard` — Next.js 14 + Recharts + Supabase

## Stack
- Same Next.js 14 + Tailwind + shadcn shim as `nextjs-landing`
- `recharts` (responsive charts; one revenue line example)
- `@supabase/ssr` + `@supabase/supabase-js` (auth client wired, no middleware gate yet)

## Generated layout (recipe additions)
```
<name>/
├── .env.example
├── README.md
└── src/
    ├── app/page.tsx                       # 3 metric cards + revenue chart
    ├── components/
    │   ├── metric-card.tsx
    │   └── revenue-chart.tsx              # Recharts ResponsiveContainer
    └── lib/supabase.ts
```

## Smoke targets
- Skip-install: ~30 s
- Full install: ~5 min

## Required services
- Supabase project (auth client expects it). For an internal dashboard
  with no auth, you can leave Supabase env empty and remove the import.

## Next steps
- Replace placeholder data in `src/app/page.tsx` with real queries.
- Add more chart components — Recharts has Bar, Pie, Area; the same `ResponsiveContainer` pattern works for all.
- Add auth gating if this dashboard is for an admin role only — see `nextjs-saas` middleware pattern.

# `nextjs-saas` — Next.js 14 + Supabase auth + Stripe placeholder

## Stack
- Same Next.js 14 + Tailwind + shadcn shim as `nextjs-landing`
- `@supabase/ssr` + `@supabase/supabase-js` for magic-link auth
- `stripe` (placeholder client only — Phase 24 wires actual flows)

## Generated layout (recipe additions on top of the Next.js base)
```
<name>/
├── ...                      # standard create-next-app output
├── .env.example             # Supabase + Stripe keys
├── README.md                # SaaS-tailored
├── docs/auth.md             # Supabase magic-link setup notes
└── src/
    ├── app/
    │   ├── page.tsx         # marketing landing
    │   ├── (app)/dashboard/page.tsx     # auth-gated workspace stub
    │   └── (auth)/sign-in/page.tsx      # magic-link form
    ├── lib/
    │   ├── supabase.ts      # createBrowserClient
    │   └── stripe.ts        # placeholder Stripe client
    └── middleware.ts        # auth gate stub for /dashboard
```

## Required external services
- **Supabase project** (free tier works). Set
  `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` in `.env.local`.
- **Stripe account** (test mode is fine). Phase 24 will create products + webhook secret; this recipe just stubs the client.

## Required steps after scaffold
1. Create Supabase project, copy URL + anon key into `.env.local`.
2. Configure Auth → URL Configuration: add `http://localhost:3000` and your prod origin.
3. Wire the middleware at `src/middleware.ts` to actually check the Supabase session — see `docs/auth.md`.
4. Decide pricing model — Phase 24 wires Stripe Checkout / subscriptions.

## Smoke targets
- Skip-install: ~30 s
- Full install: ~5–8 min

## Notes
- The middleware is intentionally a pass-through stub. Don't ship without replacing it with the documented `@supabase/ssr` server-session check.
- `src/lib/stripe.ts` instantiates a real Stripe client with a possibly-empty secret. Calls will fail loudly until you set `STRIPE_SECRET_KEY`.

"""nextjs-saas: Next.js + Tailwind + shadcn + Supabase auth + Stripe placeholder."""
from __future__ import annotations

from .nextjs_base import make_nextjs_recipe


def _templates(ctx: dict) -> dict[str, str]:
    return {
        "src/app/page.tsx": _LANDING,
        "src/app/(app)/dashboard/page.tsx": _DASHBOARD,
        "src/app/(auth)/sign-in/page.tsx": _SIGN_IN,
        "src/lib/supabase.ts": _SUPABASE_CLIENT,
        "src/lib/stripe.ts": _STRIPE_PLACEHOLDER,
        "src/middleware.ts": _MIDDLEWARE,
        ".env.example": _ENV_EXAMPLE,
        "README.md": _README.format(name=ctx["name"]),
        "docs/auth.md": _AUTH_DOCS,
    }


_LANDING = """export default function Landing() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <section className="mx-auto max-w-3xl px-6 py-24">
        <h1 className="text-5xl font-bold tracking-tight">Your SaaS, fast.</h1>
        <p className="mt-4 text-lg text-slate-400">
          Next.js 14 + Supabase auth + Stripe-ready. Replace this copy with
          your actual pitch.
        </p>
        <a href="/sign-in" className="mt-10 inline-block rounded-md bg-cyan-500 px-5 py-3 font-medium text-slate-950">
          Get started
        </a>
      </section>
    </main>
  );
}
"""

_DASHBOARD = """export default function DashboardHome() {
  return (
    <main className="min-h-screen bg-slate-950 p-8 text-slate-100">
      <h1 className="text-3xl font-bold">Dashboard</h1>
      <p className="mt-2 text-slate-400">Auth-gated. Replace with your real workspace.</p>
    </main>
  );
}
"""

_SIGN_IN = """\"use client\";

import { useState } from \"react\";
import { supabase } from \"@/lib/supabase\";

export default function SignIn() {
  const [email, setEmail] = useState(\"\");
  const [sent, setSent] = useState(false);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    await supabase.auth.signInWithOtp({ email, options: { emailRedirectTo: window.location.origin + \"/dashboard\" } });
    setSent(true);
  }

  if (sent) return <p className=\"p-8 text-slate-200\">Check your email for a magic link.</p>;
  return (
    <form onSubmit={send} className=\"mx-auto max-w-sm space-y-3 p-8 text-slate-100\">
      <h1 className=\"text-2xl font-semibold\">Sign in</h1>
      <input type=\"email\" required value={email} onChange={(e) => setEmail(e.target.value)}
        className=\"w-full rounded-md border border-slate-700 bg-slate-900 p-2\" placeholder=\"you@example.com\" />
      <button type=\"submit\" className=\"w-full rounded-md bg-cyan-500 px-4 py-2 font-medium text-slate-950\">
        Send magic link
      </button>
    </form>
  );
}
"""

_SUPABASE_CLIENT = """import { createBrowserClient } from \"@supabase/ssr\";

export const supabase = createBrowserClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
);
"""

_STRIPE_PLACEHOLDER = """// Stripe wiring is a Phase 24 follow-up — this placeholder exists so
// imports don't break and the env keys are documented.
import Stripe from \"stripe\";

export const stripe = new Stripe(process.env.STRIPE_SECRET_KEY ?? \"\", {
  apiVersion: \"2024-06-20\",
});
"""

_MIDDLEWARE = """import { NextResponse, type NextRequest } from \"next/server\";

// Replace with real Supabase session check using @supabase/ssr.
export function middleware(req: NextRequest) {
  return NextResponse.next();
}

export const config = {
  matcher: [\"/dashboard/:path*\"],
};
"""

_ENV_EXAMPLE = """# Supabase
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=

# Stripe (placeholder until Phase 24 wires it)
STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
STRIPE_WEBHOOK_SECRET=
"""

_README = """# {name}

Next.js 14 SaaS scaffold by Nexus (`nextjs-saas` recipe).

Stack: Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui +
Supabase auth (magic link) + Stripe placeholder.

## Run
```bash
npm install
cp .env.example .env.local      # fill Supabase + Stripe keys
npm run dev                      # http://localhost:3000
```

## Auth
Magic-link via `@supabase/ssr`. See `docs/auth.md`. Middleware gate at
`src/middleware.ts` — wire real session check before going live.

## Stripe
`src/lib/stripe.ts` is a typed placeholder so future Phase 24 wiring
is one diff away. Don't ship without configuring webhook secret.
"""

_AUTH_DOCS = """# Auth

Supabase magic-link auth via `@supabase/ssr`. After signup:

1. Go to your Supabase project → Authentication → URL Configuration.
2. Add `http://localhost:3000` (dev) and your production origin.
3. Set `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   in `.env.local`.

The middleware at `src/middleware.ts` is currently a pass-through
placeholder. Replace it with the recommended `@supabase/ssr` server
session check — see https://supabase.com/docs/guides/auth/server-side/nextjs.
"""


RECIPE = make_nextjs_recipe(
    slug="nextjs-saas",
    display="Next.js 14 SaaS (Supabase auth + Stripe placeholder)",
    description="SaaS skeleton: landing, auth-gated dashboard, magic-link "
                "sign in, Stripe placeholder, env template.",
    extra_templates=_templates,
    extra_packages=["@supabase/ssr", "@supabase/supabase-js", "stripe"],
)

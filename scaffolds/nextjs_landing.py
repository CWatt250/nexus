"""nextjs-landing: Next.js 14 + Tailwind + shadcn + simple lead form."""
from __future__ import annotations

from .nextjs_base import make_nextjs_recipe


def _templates(ctx: dict) -> dict[str, str]:
    return {
        "src/app/page.tsx": _PAGE_TSX,
        "src/components/lead-form.tsx": _LEAD_FORM,
        "README.md": _README.format(name=ctx["name"]),
    }


_PAGE_TSX = """import LeadForm from "@/components/lead-form";

export default function Page() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <section className="mx-auto max-w-3xl px-6 py-24">
        <h1 className="text-5xl font-bold tracking-tight">Coming soon.</h1>
        <p className="mt-4 text-lg text-slate-400">
          Drop your email and we&apos;ll let you know when it&apos;s live.
        </p>
        <div className="mt-10">
          <LeadForm />
        </div>
      </section>
    </main>
  );
}
"""

_LEAD_FORM = """\"use client\";

import { useState, type FormEvent } from \"react\";

export default function LeadForm() {
  const [email, setEmail] = useState(\"\");
  const [status, setStatus] = useState<\"idle\" | \"submitting\" | \"ok\" | \"err\">(\"idle\");

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setStatus(\"submitting\");
    try {
      // TODO: wire to your real /api/leads endpoint.
      await new Promise((r) => setTimeout(r, 400));
      setStatus(\"ok\");
    } catch {
      setStatus(\"err\");
    }
  }

  if (status === \"ok\") {
    return <p className=\"text-emerald-400\">Got it — we&apos;ll be in touch.</p>;
  }

  return (
    <form onSubmit={onSubmit} className=\"flex max-w-md gap-3\">
      <input
        type=\"email\"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder=\"you@example.com\"
        className=\"flex-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-2\"
      />
      <button
        type=\"submit\"
        disabled={status === \"submitting\"}
        className=\"rounded-md bg-cyan-500 px-4 py-2 font-medium text-slate-950 hover:bg-cyan-400 disabled:opacity-60\"
      >
        {status === \"submitting\" ? \"…\" : \"Notify me\"}
      </button>
    </form>
  );
}
"""

_README = """# {name}

Next.js 14 landing page scaffolded by Nexus (`nextjs-landing` recipe).

## Run
```bash
npm run dev   # http://localhost:3000
```

## Stack
- Next.js 14 (App Router)
- TypeScript
- Tailwind CSS
- shadcn/ui peer deps installed; `npx shadcn@latest add <component>` works

## Wire the lead form
`src/components/lead-form.tsx` posts to a TODO endpoint. Add an
`src/app/api/leads/route.ts` that talks to your CRM / database / Slack.

## Deploy
Vercel: `vercel --prod` (or import the repo on vercel.com).
"""


RECIPE = make_nextjs_recipe(
    slug="nextjs-landing",
    display="Next.js 14 Landing Page",
    description="Single-page landing with hero + lead form. Cheapest "
                "Next.js recipe — no DB, no auth.",
    extra_templates=_templates,
    notes="Smallest Next.js scaffold; good smoke target.",
)

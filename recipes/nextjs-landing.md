# `nextjs-landing` — Next.js 14 + Tailwind + shadcn shim + lead form

## Stack
- Next.js 14 (App Router)
- TypeScript
- Tailwind CSS
- shadcn/ui peer deps (`class-variance-authority`, `clsx`, `tailwind-merge`, `lucide-react`, `tailwindcss-animate`) — `npx shadcn@latest add <component>` works after scaffold.
- ESLint

## Generated layout (after `create-next-app` + recipe templates)
```
<name>/
├── package.json
├── next.config.mjs
├── tailwind.config.ts
├── tsconfig.json
├── components.json          # shadcn config
├── .env.example
├── .gitignore               # base + Nexus additions
├── README.md                # recipe-tailored
├── public/
└── src/
    ├── app/
    │   ├── layout.tsx       # base from create-next-app
    │   ├── globals.css
    │   └── page.tsx         # OVERRIDDEN — hero + LeadForm
    ├── components/
    │   ├── lead-form.tsx    # NEW — client component
    │   └── ui/.gitkeep      # shadcn drop-target
    └── lib/utils.ts         # shadcn `cn()` helper
```

## Steps the runner executes
1. `npx create-next-app@14 <name> --ts --tailwind --eslint --app --src-dir --import-alias '@/*' --no-turbopack --use-npm` (~25–30 s warm cache, longer cold)
2. Render Nexus templates (page, lead-form, utils, components.json, README, .env.example)
3. Append Nexus rules to `.gitignore`
4. `npm install class-variance-authority clsx tailwind-merge lucide-react tailwindcss-animate`
5. `npx tsc --noEmit` (sanity check)
6. `git init` + initial commit
7. (optional) GitHub repo + push
8. (optional) `npm run dev` smoke against `http://localhost:3000`

## Smoke-test latencies
- Skip-install: **~30 s** (just `create-next-app` + template writes)
- Full install + type check: ~3–5 min depending on npm cache state

## Next steps
- Replace `LeadForm`'s TODO with a real submit handler — typically `src/app/api/leads/route.ts` that posts to your CRM, Slack, or DB.
- Customize `src/app/page.tsx` for your hero copy.
- `npx shadcn@latest add button input form` if you want shadcn primitives.
- Deploy: `vercel --prod` (set `VERCEL_TOKEN` in `.env`).

## Notes
- Pinned to Next.js 14 because the host runs Node 18. To upgrade to
  Next.js 16, host needs Node ≥ 20.9; bump
  `scaffolds.nextjs_base.NEXTJS_VERSION` to `"16"`.

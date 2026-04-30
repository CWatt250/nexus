# Nexus Scaffolding Recipes

Each recipe describes one project archetype. The Python implementation lives in `~/AI_Agent/scaffolds/<slug_underscore>.py`; the markdown file in this directory is the human-facing reference (what's in the stack, what gets generated, what to do next).

| Slug | Use when | Cost |
|---|---|---|
| [`python-cli`](python-cli.md) | Building a Click + rich CLI tool | ~10 s |
| [`python-fastapi`](python-fastapi.md) | REST/JSON API service in Python | ~30–60 s |
| [`nextjs-landing`](nextjs-landing.md) | Single-page marketing / coming-soon page | ~3–5 min |
| [`nextjs-saas`](nextjs-saas.md) | Subscription SaaS with Supabase auth | ~5–8 min |
| [`nextjs-dashboard`](nextjs-dashboard.md) | Internal analytics / admin dashboard | ~5 min |
| [`nextjs-marketplace`](nextjs-marketplace.md) | Multi-sided marketplace (Phase 24 base) | ~8–10 min |

## Triggering a scaffold

Send a Telegram message like:

- "Scaffold a Next.js marketplace called shoppable-video"
- "Create a SaaS app for vendor onboarding called vendor-os"
- "Spin up a landing page named coming-soon-cli"
- "Start a FastAPI backend called api-server"

The conversation handler detects the intent, parses the recipe slug + project name, and enqueues a structured TASK. The agent calls `scaffold_project(name, recipe)` which runs the full pipeline (base scaffolder → templates → install → git init → GitHub repo → push → dev-server smoke). Heartbeats land in Telegram every 60 s for long steps.

## Adding a new recipe

1. Drop a `~/AI_Agent/scaffolds/<slug_underscore>.py` exporting a `RECIPE = Recipe(...)` constant.
2. Import it in `~/AI_Agent/scaffolds/__init__.py:registry()`.
3. Write a markdown doc in this directory describing what it generates.
4. (Optional) Add a recipe-hint regex in `workers/conversation_handler._RECIPE_HINTS` so the intent detector picks it up.

## Customizing an existing recipe

Templates are inline in the recipe's Python file. Edit the `_templates(ctx)` function — every value gets `.format(**ctx)`'d when needed. Keep recipes deterministic (no network during template rendering, no random values without seeds).

## Common gotchas

See `~/AI_Agent/docs/scaffolding.md` for the full list. Quick hits:

- **Node 18 host** — recipes pin Next.js 14. To upgrade to Next.js 16, host needs Node ≥ 20.9. One line in `scaffolds/nextjs_base.py::NEXTJS_VERSION` to swap.
- **Supabase CLI not installed** — recipes drop a `.env.example` with placeholders; `supabase init` is your job (or install via `bash ~/AI_Agent/SUDO_INTEGRATIONS.sh`).
- **Stripe Connect** — every recipe stops at the placeholder. Phase 24 wires the actual flows.
- **GitHub repo name conflicts** — `github_create_repo` will fail if the slug already exists in your account. Pick a unique name or pass `options={skip_github: true}` for a local-only scaffold.

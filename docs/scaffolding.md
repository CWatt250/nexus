# Project Scaffolding (Phase 23.1)

Single-tool scaffolding system: `scaffold_project(name, recipe, options)`. Recipes live in `~/AI_Agent/scaffolds/`, documentation in `~/AI_Agent/recipes/`.

## What recipes exist

| Slug | Stack | When to use | Cost |
|---|---|---|---|
| `python-cli` | Click + rich + pytest | CLI tools | ~10 s |
| `python-fastapi` | FastAPI + SQLAlchemy + Alembic + Pydantic v2 | REST/JSON APIs | ~30–60 s |
| `nextjs-landing` | Next.js 14 + Tailwind + shadcn + lead form | Marketing landing | ~3–5 min |
| `nextjs-saas` | + Supabase magic-link auth + Stripe placeholder | Subscription SaaS | ~5–8 min |
| `nextjs-dashboard` | + Recharts + Supabase | Internal dashboard | ~5 min |
| `nextjs-marketplace` | Multi-sided + reputation + state machine + Stripe Connect placeholder | Phase 24 marketplace base | ~8–10 min |

Full per-recipe details: `~/AI_Agent/recipes/<slug>.md`.

## How to trigger a scaffold

**Telegram (preferred)**:
- "Scaffold a Next.js marketplace called shoppable-video"
- "Spin up a SaaS app named creator-os"
- "Create a landing page called coming-soon"

The conversation handler detects the intent, picks the recipe, parses the project name, and enqueues a structured TASK. Heartbeats land in Telegram every 60 s during long steps; the final summary lands when the scaffold completes.

**Programmatic**:
```python
from tools.scaffold_tool import scaffold_project
print(scaffold_project.invoke({
    "name": "shoppable-video",
    "recipe": "nextjs-marketplace",
    "options": {},  # see options below
}))
```

## Options

| Key | Default | Effect |
|---|---|---|
| `skip_github` | `false` | Skip the GitHub repo creation + push step (local-only scaffold). |
| `skip_install` | `false` | Skip `npm install` / `pip install` steps (templates still write). |
| `skip_dev_smoke` | `false` | Skip the `npm run dev` localhost:3000 probe. |
| `skip_typecheck` | `false` | Skip `npx tsc --noEmit`. |
| `skip_tests` | `false` | Skip the recipe's initial test run. |
| `base_dir` | `~/Dev` | Override the parent directory for the new project. |

## How to create a new recipe

1. **Recipe module**: drop `~/AI_Agent/scaffolds/<slug_underscore>.py` exporting a single `RECIPE = Recipe(...)` constant. The `Recipe` dataclass lives in `scaffolds/base.py`. Pure-Python recipes set `base_command=None`; Node/Python tooling recipes set it to a list like `["npx", "-y", "create-next-app@14"]` and supply a `base_command_args(project_dir, opts)` callable.
2. **Register**: import the module in `scaffolds/__init__.py::registry()`.
3. **Markdown doc**: write `~/AI_Agent/recipes/<slug>.md` mirroring the structure of the existing ones (Stack / Layout / Steps / Smoke targets / Required services / Next steps).
4. **Hint regex**: add an entry to `workers/conversation_handler._RECIPE_HINTS` so natural-language requests resolve to the new slug.
5. **Tests**: add a parametrize case to `tests/test_scaffold_routing.py::test_detect_recipe`.

## How to customize an existing recipe

Templates are inline strings inside the `_templates(ctx)` function of each recipe module. `ctx` is `{name, project_dir, opts, recipe}`; package names are computed from `name.replace("-", "_")`. Edit, save, re-run — no rebuild needed.

## Common gotchas

### Node 18 host vs Next.js 16
The host runs Node 18.19 — Next.js 16 requires Node ≥ 20.9. **Recipes pin Next.js 14**, which works on Node 18 cleanly. To upgrade once the host has Node 20:
```python
# scaffolds/nextjs_base.py
NEXTJS_VERSION = "16"   # was "14"
```
That's the entire change.

### Supabase CLI not installed
`scaffolds/nextjs_saas.py` and `nextjs-marketplace.py` write `.env.example` with Supabase keys but **don't** run `supabase init`. To install the CLI:
```bash
bash ~/AI_Agent/SUDO_INTEGRATIONS.sh   # installs supabase via npm-global
```
Or manually: `sudo npm install -g supabase`.

### GitHub repo name conflicts
`github_create_repo` fails if the repo name already exists in your account. Either pick a unique slug or pass `options={"skip_github": true}` for a local-only scaffold; you can `gh repo create` later.

### Stripe Connect
**Every recipe stops at the placeholder.** `src/lib/stripe.ts` instantiates a Stripe client with whatever's in `STRIPE_SECRET_KEY` (possibly empty), so calls fail loudly. Phase 24 owns the actual onboarding flow + webhook handlers; the recipe gives you the type-safe scaffolding so wiring is one PR away.

### Vercel deploy
Recipes do **not** call `vercel deploy`. After scaffold:
- `vercel --prod` (CLI is installed)
- Or import the GitHub repo on vercel.com
- Set `VERCEL_TOKEN` in `.env` to use the `vercel_deploy` Nexus tool.

### Dev-server smoke flakes
The `npm run dev` smoke waits up to 60 s for `http://127.0.0.1:3000` to respond. If your machine is under load (other Next.js dev servers, qwen3.6 inference), it can time out. Fall back: `options={"skip_dev_smoke": true}` and run `npm run dev` by hand.

## What the runner does

`scaffolds/base.py::_scaffold_recipe` drives every recipe through:

1. **base_scaffold** — `npx create-next-app` (or no-op for Python recipes)
2. **render_templates** — write the recipe's template dict to disk
3. **extend_gitignore** — append our extra rules (`.env.local`, etc.)
4. **npm_install_runtime** + **npm_install_dev** — recipe's extra packages
5. **pip_install** — for Python recipes
6. **extra_steps** — recipe-defined steps (venv creation, custom config writes, type checks, smoke tests)
7. **git_init** — `git init -q -b main` + initial commit (uses `nexus@wattbott.local` identity so it doesn't pollute the global git config)
8. **github_create** + **git_push** — unless `skip_github`
9. **dev_smoke** — for Node recipes, `npm run dev` + curl probe

Each step has a 120 s default timeout (overridable per-step). A heartbeat thread fires every 60 s during shell steps so Telegram users see progress on long installs.

## File layout summary

```
~/AI_Agent/
├── scaffolds/                   # implementation
│   ├── __init__.py              # registry()
│   ├── base.py                  # Recipe + Runner
│   ├── nextjs_base.py           # shared Next.js helper
│   ├── nextjs_landing.py
│   ├── nextjs_saas.py
│   ├── nextjs_marketplace.py
│   ├── nextjs_dashboard.py
│   ├── python_cli.py
│   └── python_fastapi.py
├── recipes/                     # markdown docs (this dir)
│   ├── README.md
│   └── <slug>.md (×6)
├── tools/scaffold_tool.py       # @tool entry point
└── docs/scaffolding.md          # this file
```

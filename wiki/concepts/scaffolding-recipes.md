---
name: Scaffolding Recipes
description: Phase 23.1 — declarative project starters under recipes/ + scaffolds/ that turn a one-line prompt into a runnable repo.
type: concept
last_updated: 2026-05-01
sources: []
tags: [phase-23, scaffolding, recipes, codegen]
---

# Scaffolding Recipes (Phase 23.1)

When Colton (or Nexus on his behalf) wants a new project, the scaffold tool reads a recipe + scaffold pair and emits a runnable repo without hand-typing boilerplate.

## Layout

```
recipes/                          ← human-readable spec, one .md per recipe
├── README.md
├── nextjs-landing.md
├── nextjs-dashboard.md
├── nextjs-marketplace.md
├── nextjs-saas.md
├── python-cli.md
└── python-fastapi.md

scaffolds/                        ← Python modules that materialize the recipe
├── base.py                       ← shared helpers
├── nextjs_base.py
├── nextjs_landing.py
├── nextjs_dashboard.py
├── nextjs_marketplace.py
├── nextjs_saas.py
├── python_cli.py
└── python_fastapi.py
```

Each scaffold module exposes a `scaffold(target_dir, **opts)` entry point that writes files, creates the package manifest, and optionally runs install.

## How it's invoked

- LangGraph: `tools/scaffold_tool.SCAFFOLD_TOOLS` — `scaffold_list`, `scaffold_create(recipe, name, **opts)`.
- CLI: `python3 ~/AI_Agent/nexus.py "scaffold a nextjs landing page named foobar"` routes through the agent and triggers `scaffold_create`.

## Adding a new recipe

1. Write `recipes/<name>.md` — one-line description, stack, file tree, key dependencies, a worked example.
2. Add `scaffolds/<name>.py` mirroring an existing one (e.g. `nextjs_landing.py`) with the `scaffold()` entry point.
3. Recipe + scaffold get auto-discovered by `scaffold_tool` — no registration needed.

## Related
- [Nexus](../entities/nexus.md) — the agent that drives scaffolding.

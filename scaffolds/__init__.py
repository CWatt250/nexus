"""Project scaffolding recipes (Phase 23.1).

`scaffolds.registry()` returns a name → Recipe map. The
`tools.scaffold_tool.scaffold_project(...)` LangChain tool resolves
recipe slug → Recipe instance and runs `recipe.scaffold(...)`.

Each recipe module exposes a single `RECIPE = Recipe(...)` constant.
"""
from __future__ import annotations

from typing import Dict

from .base import Recipe


def registry() -> Dict[str, Recipe]:
    """Build the slug → Recipe map. Imported lazily so a broken recipe
    module doesn't take the whole tool surface down at import time."""
    from . import nextjs_landing, nextjs_saas, nextjs_marketplace
    from . import nextjs_dashboard, python_fastapi, python_cli
    return {
        m.RECIPE.name: m.RECIPE
        for m in (
            nextjs_landing,
            nextjs_saas,
            nextjs_marketplace,
            nextjs_dashboard,
            python_fastapi,
            python_cli,
        )
    }


def get(name: str) -> Recipe | None:
    """Resolve a recipe by slug. Returns None if unknown."""
    return registry().get(name)

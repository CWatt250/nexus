"""scaffold_project tool — Phase 23.1 entry point.

Wraps `scaffolds.registry()` behind a single LangChain tool the agent
can call. Returns a structured dict that includes a Telegram-friendly
summary; the runner sends progress heartbeats by itself.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

log = logging.getLogger("nexus.scaffold_tool")

DEFAULT_BASE_DIR = Path.home() / "Dev"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}[a-z0-9]$")


def _list_recipes() -> str:
    from scaffolds import registry  # noqa: PLC0415
    rows = []
    for name, r in registry().items():
        rows.append(f"- {name}: {r.display} — {r.description}")
    return "\n".join(rows)


@tool
def scaffold_project(name: str, recipe: str, options: dict | None = None) -> str:
    """Scaffold a complete project from a starter recipe.

    Creates `~/Dev/<name>` (rejects existing non-empty dirs), runs the
    recipe's base scaffolder + extra steps, initializes git, optionally
    creates a private GitHub repo + pushes, and (for Node recipes)
    smoke-tests the dev server.

    Args:
        name: project slug, e.g. "test-landing-1". Must match
            `^[a-z0-9][a-z0-9-]+[a-z0-9]$` (URL-safe, GitHub-safe).
        recipe: one of {nextjs-landing, nextjs-saas, nextjs-marketplace,
            nextjs-dashboard, python-fastapi, python-cli}.
        options: optional flags:
            - skip_github (bool): don't create the GitHub repo / push
            - skip_install (bool): skip dependency install steps
            - skip_dev_smoke (bool): skip the npm-run-dev probe
            - skip_typecheck (bool): skip the tsc --noEmit step
            - skip_tests (bool): skip running the recipe's initial tests
            - base_dir (str): override `~/Dev` for the parent directory

    Returns a multi-line summary safe to paste into Telegram. Long
    Telegram-friendly progress heartbeats are sent during the run by
    `scaffolds.base.Runner`.
    """
    options = options or {}

    if not isinstance(name, str) or not SLUG_RE.match(name):
        return (
            f"ERROR: invalid project name {name!r}. Use lowercase letters, "
            f"digits, and hyphens only — start and end with alphanumeric."
        )

    from scaffolds import get  # noqa: PLC0415

    rec = get(recipe)
    if rec is None:
        return f"ERROR: unknown recipe {recipe!r}.\nAvailable:\n{_list_recipes()}"

    base = Path(options.get("base_dir") or DEFAULT_BASE_DIR).expanduser()
    project_dir = (base / name).resolve()

    log.info("scaffold_project starting: name=%s recipe=%s dir=%s opts=%s",
             name, recipe, project_dir, sorted(options.keys()))

    result: dict[str, Any] = rec.scaffold(project_dir, options)
    summary = result.get("summary") or "(no summary)"
    if not result.get("ok"):
        summary += f"\n\nFAILED: {result.get('error', '')}"
    return summary


@tool
def list_recipes() -> str:
    """List every available scaffold recipe (slug → description)."""
    return _list_recipes()


SCAFFOLD_TOOLS = [scaffold_project, list_recipes]

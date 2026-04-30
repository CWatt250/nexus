"""Shared helper for the four Next.js recipes.

`create-next-app@14` is run with non-interactive flags so each child
recipe just adds its own packages, env keys, and template files on top.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .base import Recipe, Step

# Pinned to Next.js 14 because Node 18 (currently installed) is below
# the >=20.9 requirement of Next.js 16. Once the host upgrades to
# Node 20, bump this to 16 in one line.
NEXTJS_VERSION = "14"


def _base_args(project_dir: Path, opts: dict) -> list[str]:
    """create-next-app args. Keep it non-interactive."""
    return [
        project_dir.name,
        "--ts",                # TypeScript
        "--tailwind",          # Tailwind CSS
        "--eslint",
        "--app",               # App Router
        "--src-dir",
        "--import-alias", "@/*",
        "--no-turbopack",
        "--use-npm",
    ]


_GITIGNORE_EXTRA = """
# Nexus scaffold additions
.env.local
.env.*.local
"""


def _common_extra_steps(ctx: dict, *, do_shadcn: bool = True) -> list[Step]:
    pdir = ctx["project_dir"]
    steps: list[Step] = []
    if do_shadcn:
        # `shadcn-ui init` is interactive in normal use; we wire its
        # config files directly via templates so this step just installs
        # the runtime peer deps so `shadcn add <component>` works after.
        steps.append(Step(
            name="install_shadcn_runtime",
            command="npm install -q "
                    "class-variance-authority clsx tailwind-merge "
                    "lucide-react tailwindcss-animate",
            cwd=pdir, timeout_s=180,
            progress="Installing shadcn/ui runtime peers",
            skip_if=lambda c: c["opts"].get("skip_install"),
        ))
    steps.append(Step(
        name="typecheck",
        command="npx -y tsc --noEmit",
        cwd=pdir, timeout_s=120,
        progress="Running tsc --noEmit (quick type check)",
        skip_if=lambda c: c["opts"].get("skip_install") or c["opts"].get("skip_typecheck"),
    ))
    return steps


def _shadcn_components_json(ctx: dict) -> str:
    return """{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "default",
  "rsc": true,
  "tsx": true,
  "tailwind": {
    "config": "tailwind.config.ts",
    "css": "src/app/globals.css",
    "baseColor": "slate",
    "cssVariables": true,
    "prefix": ""
  },
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/utils",
    "ui": "@/components/ui",
    "lib": "@/lib",
    "hooks": "@/hooks"
  }
}
"""


def _utils_ts(ctx: dict) -> str:
    return '''import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
'''


def make_nextjs_recipe(
    *,
    slug: str,
    display: str,
    description: str,
    extra_templates: Callable[[dict], dict[str, str]] | None = None,
    extra_steps: Callable[[dict], list[Step]] | None = None,
    extra_packages: list[str] | None = None,
    extra_dev_packages: list[str] | None = None,
    notes: str = "",
) -> Recipe:
    """Build a Recipe pre-wired with the Next.js 14 base + shadcn shim."""

    def _templates(ctx: dict) -> dict[str, str]:
        files = {
            "components.json": _shadcn_components_json(ctx),
            "src/lib/utils.ts": _utils_ts(ctx),
            "src/components/ui/.gitkeep": "",
            ".env.example": "# Add app secrets here.\n",
        }
        if extra_templates:
            files.update(extra_templates(ctx))
        # Ensure .gitignore appendage doesn't blow away create-next-app's
        # own .gitignore — we only add our extra rules at the end.
        # (Handled by extra_steps below to APPEND not overwrite.)
        return files

    def _steps(ctx: dict) -> list[Step]:
        steps: list[Step] = []
        # Append our gitignore additions instead of overwriting.
        steps.append(Step(
            name="extend_gitignore",
            command=f"printf '%s' {_quote_for_shell(_GITIGNORE_EXTRA)} >> .gitignore",
            cwd=ctx["project_dir"], timeout_s=10,
        ))
        steps.extend(_common_extra_steps(ctx))
        if extra_steps:
            steps.extend(extra_steps(ctx))
        return steps

    return Recipe(
        name=slug,
        display=display,
        description=description,
        base_command=["npx", "-y", f"create-next-app@{NEXTJS_VERSION}"],
        base_command_args=_base_args,
        extra_npm_packages=extra_packages or [],
        extra_dev_npm_packages=extra_dev_packages or [],
        template_files=_templates,
        extra_steps=_steps,
        requires_node_version=(20, 0),  # current host is 18 — recipe still pins next@14 to keep it working
        notes=notes,
    )


def _quote_for_shell(s: str) -> str:
    """Quote a string for safe `printf '%s' …` interpolation."""
    return "'" + s.replace("'", "'\\''") + "'"

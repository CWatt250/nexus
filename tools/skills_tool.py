"""Skill tools (step 4) — read/save/list reusable procedures, backed by core.skills."""
from __future__ import annotations

from langchain_core.tools import tool

from core import skills


@tool
def skill_read(name: str) -> str:
    """Read one of your saved SKILLS (step-by-step procedures) by name. Call this
    BEFORE starting a task that matches a skill listed in your prompt, then
    follow its steps."""
    body = skills.read(name)
    if body:
        return body
    names = ", ".join(s["name"] for s in skills.list_skills()) or "none yet"
    return f"No skill named {name!r}. Saved skills: {names}"


@tool
def skill_save(name: str, when: str, steps: str) -> str:
    """Save (or overwrite) a reusable SKILL: a numbered procedure for a task that
    will come up again. `when` = one line describing the situation that should
    trigger it; `steps` = the concrete steps (commands, tools, paths, gotchas).
    Use when Colton says to save/remember how to do something, or after you
    figure out a non-obvious multi-step procedure. Never put secrets in a skill."""
    try:
        slug = skills.save(name, when, steps)
    except ValueError as exc:
        return f"Not saved: {exc}"
    return f"Skill saved: {slug}"


@tool
def skill_list() -> str:
    """List your saved skills (name — when to use it — times used)."""
    ss = skills.list_skills()
    if not ss:
        return "No skills saved yet."
    return "\n".join(f"- {s['name']} — {s['when']} (used {s['uses']}x)" for s in ss)


SKILLS_TOOLS = [skill_read, skill_save, skill_list]

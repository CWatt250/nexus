"""Persistent-goal tools (G3) — add/list/done/advance, backed by core.goals."""
from __future__ import annotations

from langchain_core.tools import tool

from core import goals


@tool
def goal_add(text: str) -> str:
    """Add a PERSISTENT goal that Nexus pursues across sessions until it's done
    (a standing objective, not a one-shot task). A background driver advances
    each active goal one concrete step every ~6h and reports to Telegram.
    Use for things like 'get image-gen to SDXL quality' or 'ship BidWatt auth'."""
    g = goals.add_goal(text)
    return f"Goal {g['id']} added (active, {g['max_advances']}-step budget): {g['text']}"


@tool
def goal_list() -> str:
    """List persistent goals with status, step count, and latest progress."""
    gs = goals.list_goals()
    if not gs:
        return "No goals yet. Add one with goal_add."
    out = []
    for g in gs:
        last = g["notes"][-1]["note"] if g["notes"] else "(no progress yet)"
        out.append(f"[{g['status']}] {g['id']} ({g['advances']}/{g['max_advances']}): "
                   f"{g['text']}\n    ↳ {last[:90]}")
    return "\n".join(out)


@tool
def goal_done(goal_id: str) -> str:
    """Mark a persistent goal complete by id (full or suffix)."""
    g = goals.set_status(goal_id, "done")
    return f"Goal {g['id']} marked done." if g else f"Goal {goal_id!r} not found."


@tool
def goal_advance(goal_id: str = "") -> str:
    """Advance a goal one step right now. With no id, advances ALL active goals
    (the same step the 6h driver runs)."""
    return goals.advance_goal(goal_id) if goal_id.strip() else goals.advance_all_goals()


GOALS_TOOLS = [goal_add, goal_list, goal_done, goal_advance]

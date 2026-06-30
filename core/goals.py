"""Persistent goals (G3, Hermes Ralph-loop).

A goal is a standing objective Nexus pursues across sessions until done —
unlike a one-shot task. Each `advance` step asks the brain for the single
next concrete action, records it, and either dispatches a build task or notes
status. A per-goal budget + active/paused/done states keep it from running
away; every advance reports to Telegram for visibility.

Store: memory/goals.json (append-only-ish, full rewrite per change).
Driver: advance_all_goals() — run periodically via a recurring scheduler
entry firing the "[goal-advance]" sentinel (handled in task_worker).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("nexus.goals")

ROOT = Path.home() / "AI_Agent"
STORE = ROOT / "memory" / "goals.json"
DEFAULT_MAX_ADVANCES = 25
ADVANCE_SCHEDULE_SPEC = "21600"  # seconds — every 6h (interval schedule)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    if not STORE.exists():
        return {"goals": []}
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"goals": []}


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _find(data: dict, goal_id: str) -> dict | None:
    gid = (goal_id or "").strip()
    for g in data["goals"]:
        if g["id"] == gid or g["id"].endswith(gid) or gid in g["id"]:
            return g
    return None


def add_goal(text: str, *, max_advances: int = DEFAULT_MAX_ADVANCES) -> dict:
    data = _load()
    goal = {
        "id": "g_" + uuid.uuid4().hex[:8],
        "text": text.strip(),
        "status": "active",
        "created": _now(), "updated": _now(),
        "advances": 0, "max_advances": int(max_advances),
        "notes": [], "dispatched_tasks": [],
    }
    data["goals"].append(goal)
    _save(data)
    _ensure_advance_schedule()
    return goal


def list_goals(status: str | None = None) -> list[dict]:
    goals = _load()["goals"]
    return [g for g in goals if not status or g["status"] == status]


def set_status(goal_id: str, status: str) -> dict | None:
    data = _load()
    g = _find(data, goal_id)
    if not g:
        return None
    g["status"] = status
    g["updated"] = _now()
    _save(data)
    return g


def add_note(goal_id: str, note: str) -> None:
    data = _load()
    g = _find(data, goal_id)
    if not g:
        return
    g["notes"].append({"ts": _now(), "note": note.strip()[:500]})
    g["updated"] = _now()
    _save(data)


def _ensure_advance_schedule() -> None:
    """Register the recurring goal-advance driver once (idempotent)."""
    try:
        from core import scheduler
        for s in scheduler.list_schedules():
            if (s.get("input") or "").strip() == "[goal-advance]":
                return
        scheduler.add_schedule("interval", ADVANCE_SCHEDULE_SPEC, "[goal-advance]")
        log.info("registered recurring goal-advance schedule (every 6h)")
    except Exception as exc:
        log.warning("could not register goal-advance schedule: %s", exc)


_ADVANCE_PROMPT = (
    "You are advancing a long-running GOAL by exactly ONE concrete step.\n\n"
    "GOAL: {text}\n\nPROGRESS LOG (oldest first):\n{log}\n\n"
    "Reply with ONE line, exactly one of these forms:\n"
    "  DONE                       — the goal is fully complete.\n"
    "  DISPATCH: <one build/code task to run now>   — next step is actionable work.\n"
    "  NOTE: <short status/next-step>   — waiting, needs user input, or research note.\n"
    "No preamble, no extra lines."
)


def advance_goal(goal_id: str) -> str:
    """One Ralph-loop step on a single goal. Returns a short summary."""
    data = _load()
    g = _find(data, goal_id)
    if not g:
        return f"goal {goal_id} not found"
    if g["status"] != "active":
        return f"{g['id']} is {g['status']} — skipped"
    if g["advances"] >= g["max_advances"]:
        g["status"] = "paused"
        g["notes"].append({"ts": _now(), "note": "budget reached — paused"})
        _save(data)
        return f"{g['id']} hit its {g['max_advances']}-step budget — paused"

    log_text = "\n".join(f"- {n['note']}" for n in g["notes"][-12:]) or "(none yet)"
    try:
        from core import brain
        reply = brain.chat(
            [{"role": "user",
              "content": _ADVANCE_PROMPT.format(text=g["text"], log=log_text)}],
            options={"temperature": 0.3, "num_ctx": 8192, "num_predict": 300},
            timeout=120.0,
        ).strip()
    except Exception as exc:
        return f"{g['id']} advance failed: {type(exc).__name__}: {exc}"

    g["advances"] += 1
    g["updated"] = _now()
    line = reply.splitlines()[0].strip() if reply else "NOTE: (no response)"
    summary: str

    if line.upper().startswith("DONE"):
        g["status"] = "done"
        g["notes"].append({"ts": _now(), "note": "DONE"})
        summary = f"✅ {g['id']} complete: {g['text'][:60]}"
    elif line.upper().startswith("DISPATCH:"):
        task = line.split(":", 1)[1].strip()
        try:
            from core import task_queue
            tid = task_queue.enqueue(task)
            g["dispatched_tasks"].append(tid)
            g["notes"].append({"ts": _now(), "note": f"dispatched: {task} (task {tid})"})
            summary = f"🚀 {g['id']} step {g['advances']}: dispatched task {tid} — {task[:60]}"
        except Exception as exc:
            g["notes"].append({"ts": _now(), "note": f"dispatch failed: {exc}"})
            summary = f"⚠️ {g['id']} dispatch failed: {exc}"
    else:
        note = line.split(":", 1)[1].strip() if ":" in line else line
        g["notes"].append({"ts": _now(), "note": note[:400]})
        summary = f"📝 {g['id']} step {g['advances']}: {note[:80]}"

    _save(data)
    return summary


def advance_all_goals() -> str:
    """Advance every active goal one step + report to Telegram. Returns the
    combined summary. Called by the recurring [goal-advance] schedule."""
    active = list_goals("active")
    if not active:
        return "no active goals"
    lines = [advance_goal(g["id"]) for g in active]
    report = "🎯 Goal progress:\n" + "\n".join(lines)
    try:
        import asyncio
        from tools.telegram_tool import proactive_send
        asyncio.run(proactive_send(report))
    except Exception as exc:
        log.warning("goal Telegram report failed: %s", exc)
    return report

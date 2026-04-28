"""Daily end-of-day summary (Phase 19.3).

5pm local Sparky+Telegram summary that pulls from today's task_metrics,
agent-events, retros, run-log, and produces a short brief: what shipped,
what's pending, what to pick up tomorrow.

Triggered by `nexus-eod-summary.timer` (Mon-Sun 17:00 local).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time as dtime, timezone
from pathlib import Path

import ollama  # noqa: F401  — used inline below
from langchain_core.tools import tool

ROOT = Path.home() / "AI_Agent"
TASK_LOG = ROOT / "memory" / "task_metrics.jsonl"
EVENT_LOG = ROOT / "memory" / "agent-events.jsonl"
REMINDERS = ROOT / "memory" / "reminders.jsonl"
TQ_DB = ROOT / "memory" / "tasks.db"
OLLAMA_URL = "http://localhost:11434"
SUMMARY_MODEL = "qwen3:4b"

log = logging.getLogger("nexus.eod_summary")


def _today_window() -> tuple[datetime, datetime]:
    now = datetime.now().astimezone()
    start = datetime.combine(now.date(), dtime(0, 0), tzinfo=now.tzinfo).astimezone(timezone.utc)
    end = now.astimezone(timezone.utc)
    return start, end


def _read_jsonl(path: Path, predicate) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if predicate(obj):
            out.append(obj)
    return out


def _ts_in(start: datetime, end: datetime, ts_field: str = "ts"):
    def predicate(obj):
        ts = obj.get(ts_field)
        if not isinstance(ts, str):
            return False
        try:
            return start <= datetime.fromisoformat(ts) <= end
        except ValueError:
            return False
    return predicate


def _due_in_next_24h() -> list[dict]:
    """Reminders whose `due` lands within the next 24h."""
    if not REMINDERS.exists():
        return []
    now = datetime.now(timezone.utc)
    horizon = now.replace(hour=23, minute=59) if now.hour < 17 else None
    out = []
    for line in REMINDERS.read_text().splitlines():
        try:
            obj = json.loads(line)
        except Exception:
            continue
        due = obj.get("due")
        if not due:
            continue
        try:
            d = datetime.fromisoformat(due)
        except Exception:
            continue
        if 0 <= (d - now).total_seconds() <= 86400:
            out.append(obj)
    return out


def _todays_summary() -> str:
    start, end = _today_window()
    turns = _read_jsonl(TASK_LOG, _ts_in(start, end))
    events = _read_jsonl(EVENT_LOG, _ts_in(start, end))
    reminders = _due_in_next_24h()

    completed = [t for t in turns if t.get("success")]
    failed = [t for t in turns if not t.get("success")]
    git_events = [e for e in events if e.get("event") == "git_commit"]
    file_events = [e for e in events if e.get("event") == "file_ingested"]

    bits = []
    if completed:
        bits.append(f"{len(completed)} agent turn(s) completed")
    if failed:
        bits.append(f"{len(failed)} failed")
    if git_events:
        repos = sorted({e.get("repo", "?") for e in git_events})
        bits.append(f"{len(git_events)} commit(s) across {repos}")
    if file_events:
        bits.append(f"{len(file_events)} file ingestion(s)")
    if reminders:
        bits.append(f"{len(reminders)} reminder(s) due in 24h")
    today_one_liner = "; ".join(bits) or "quiet day"

    # Optional LLM-condensed brief — keep it short. Falls back to the
    # one-liner if qwen3:4b is unavailable.
    sample_turns = [
        f"- {t.get('user_preview','')[:80]}"
        for t in completed[:6]
    ]
    sample_commits = [
        f"- {e.get('repo','?')} {e.get('sha','')} {e.get('subject','')[:80]}"
        for e in git_events[:6]
    ]
    sample_reminders = [
        f"- {r.get('text','')[:80]} (due {r.get('due','?')})"
        for r in reminders[:6]
    ]

    prompt = (
        "Write a single 4-6 sentence end-of-day brief in plain markdown for "
        "Colton. Cover: what shipped today, anything broken, and what's queued "
        "for tomorrow. No preamble.\n\n"
        f"Today summary: {today_one_liner}\n\n"
        f"Sample completed turns:\n{chr(10).join(sample_turns) or '(none)'}\n\n"
        f"Sample commits:\n{chr(10).join(sample_commits) or '(none)'}\n\n"
        f"Reminders within 24h:\n{chr(10).join(sample_reminders) or '(none)'}\n\n"
        "Brief:"
    )
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=SUMMARY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=False, think=False, keep_alive=-1,
            options={"temperature": 0.2, "num_predict": 350, "num_ctx": 8192},
        )
    except Exception as exc:
        return f"## EOD\n{today_one_liner}\n\n_(LLM unavailable: {exc})_"
    if isinstance(resp, dict):
        body = ((resp.get("message") or {}).get("content") or "").strip()
    else:
        m = getattr(resp, "message", None)
        body = (getattr(m, "content", "") or "").strip()
    return body or f"## EOD\n{today_one_liner}"


@tool
def eod_summary_run() -> str:
    """Generate today's EOD brief, push to Sparky bubble + Telegram.
    Returns the text for inspection."""
    summary = _todays_summary()
    # Sparky bubble — best-effort.
    try:
        from tools.sparky_state import post_bubble
        post_bubble("📋 End-of-day brief — see Telegram.")
    except Exception:
        pass
    # Telegram — best-effort.
    try:
        from tools.telegram_tool import proactive_send
        asyncio.run(proactive_send(f"📋 EOD brief\n\n{summary}"))
    except Exception:
        pass
    return summary


EOD_SUMMARY_TOOLS = [eod_summary_run]

"""Calendar prep briefs (Phase 19.4).

Polls a local .ics file (path from `NEXUS_ICS_PATH` env var, default
`~/.local/share/nexus-calendar.ics`) for events starting in the next
30 minutes. For each, pulls RAG context for the event title, formats
a brief, posts to Sparky bubble + Telegram.

Avoids Google OAuth — Colton can drop a synced .ics from any provider.
Dedups via `memory/calendar-briefed.jsonl`.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langchain_core.tools import tool

ROOT = Path.home() / "AI_Agent"
DEFAULT_ICS = Path.home() / ".local" / "share" / "nexus-calendar.ics"
BRIEFED_LOG = ROOT / "memory" / "calendar-briefed.jsonl"

log = logging.getLogger("nexus.calendar_prep")


def _ics_path() -> Path:
    return Path(os.getenv("NEXUS_ICS_PATH", str(DEFAULT_ICS))).expanduser()


def _parse_ics_events(text: str) -> list[dict]:
    """Parse a minimal subset of iCalendar — VEVENT blocks with DTSTART /
    SUMMARY / DESCRIPTION. Times treated as UTC; floating times approximated."""
    events: list[dict] = []
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current and current.get("DTSTART"):
                events.append(current)
            current = None
            continue
        if current is None:
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.split(";")[0]
            current[key] = val.strip()
    return events


def _parse_ics_dt(s: str) -> datetime | None:
    s = s.replace("Z", "")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _next_events(window_minutes: int = 30) -> list[dict]:
    p = _ics_path()
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(minutes=window_minutes)
    out = []
    for ev in _parse_ics_events(text):
        dt = _parse_ics_dt(ev.get("DTSTART", ""))
        if not dt:
            continue
        if now <= dt <= horizon:
            out.append({
                "title": ev.get("SUMMARY", ""),
                "description": ev.get("DESCRIPTION", ""),
                "start": dt.isoformat(),
            })
    return out


def _already_briefed(event_key: str) -> bool:
    if not BRIEFED_LOG.exists():
        return False
    try:
        return event_key in BRIEFED_LOG.read_text()
    except OSError:
        return False


def _record_briefed(event_key: str) -> None:
    BRIEFED_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with BRIEFED_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event_key": event_key, "ts": datetime.now(timezone.utc).isoformat()}) + "\n")
    except OSError:
        pass


def _brief(event: dict) -> str:
    title = event["title"]
    rag_ctx = ""
    try:
        from tools.rag_tool import memory_search
        rag_ctx = memory_search.invoke({"query_text": title, "k": 3})
    except Exception:
        rag_ctx = "_(rag unavailable)_"
    return (
        f"📆 *{title}* — starts {event['start']}\n\n"
        f"{event.get('description','')[:300]}\n\n"
        f"_Relevant memory:_\n{rag_ctx}"
    )


@tool
def calendar_prep_run(window_minutes: int = 30) -> str:
    """Scan the .ics calendar and post prep briefs for events starting
    within `window_minutes`. Returns a one-line status."""
    events = _next_events(window_minutes)
    if not events:
        return f"no events in the next {window_minutes} minutes."
    briefed = 0
    for ev in events:
        key = re.sub(r"\s+", "_", f"{ev['title']}@{ev['start']}").lower()
        if _already_briefed(key):
            continue
        text = _brief(ev)
        try:
            from tools.sparky_state import post_bubble
            post_bubble(f"📆 prep brief: {ev['title'][:60]}")
        except Exception:
            pass
        try:
            import asyncio
            from tools.telegram_tool import proactive_send
            asyncio.run(proactive_send(text))
        except Exception:
            pass
        _record_briefed(key)
        briefed += 1
    return f"calendar prep: briefed {briefed} of {len(events)} upcoming events."


CALENDAR_PREP_TOOLS = [calendar_prep_run]

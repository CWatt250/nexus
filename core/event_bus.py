"""In-process event bus + JSONL persistence for the dashboard (Phase 17.1).

Publishers (agents, tools, workers) call `publish(record)` to fan an event
out to every subscribed websocket queue. Each event is also appended to
`memory/agent-events.jsonl` so the dashboard can replay history.

The bus is process-local — separate processes (e.g. the standalone task
worker) need to ship their events through the FastAPI HTTP layer rather
than directly into this bus. For that we expose `publish_remote(url,
record)` plus a `/ws/publish` POST endpoint in nexus_api.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path.home() / "AI_Agent"
EVENT_LOG = ROOT / "memory" / "agent-events.jsonl"

log = logging.getLogger("nexus.event_bus")

_subscribers: list[asyncio.Queue] = []
_subs_lock = threading.Lock()
_log_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(record: dict) -> None:
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _log_lock, EVENT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("event log write failed: %s", exc)


def subscribe() -> asyncio.Queue:
    """Register a new subscriber queue. Caller is responsible for draining
    and unsubscribing on disconnect."""
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    with _subs_lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _subs_lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def publish(record: dict) -> None:
    """Fire-and-forget. Ensures `ts` and persists to JSONL."""
    record = dict(record)
    record.setdefault("ts", _now_iso())
    _append(record)
    with _subs_lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(record)
        except asyncio.QueueFull:
            log.debug("subscriber queue full — dropping event")
        except RuntimeError:
            pass


def replay_recent(limit: int = 200) -> list[dict]:
    """Read the last `limit` events from the JSONL for a fresh subscriber."""
    if not EVENT_LOG.exists():
        return []
    try:
        lines = EVENT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def emit(event: str, **fields: Any) -> None:
    """Convenience: emit a typed event with arbitrary fields."""
    record = {"event": event, **fields}
    publish(record)

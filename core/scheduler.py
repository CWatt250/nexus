"""Task scheduler (Phase 16.5).

Persistent schedule store at `memory/scheduled_tasks.db` with three trigger
shapes:
  - once   — fire at a specific UTC datetime
  - cron   — fire on a 5-field cron expression (UTC)
  - interval — fire every N seconds

When a trigger fires, the scheduler enqueues the underlying input into the
Phase 15 task queue (`core.task_queue.enqueue`) with the configured
priority. The actual heavy work runs on the task_worker, never inside the
scheduler tick.

The runtime tick loop is exposed as `tick_once()` for one-off use and
`run_forever(poll_seconds=10)` for the systemd service `nexus-scheduler`.
The FastAPI endpoints live in `nexus_api.py`.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path.home() / "AI_Agent"
DB_PATH = ROOT / "memory" / "scheduled_tasks.db"

VALID_KINDS = ("once", "cron", "interval")
log = logging.getLogger("nexus.scheduler")

_INIT = False
_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()


def _init_schema() -> None:
    global _INIT
    if _INIT:
        return
    with _LOCK:
        if _INIT:
            return
        with _connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS schedules (
                    schedule_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,         -- once | cron | interval
                    spec TEXT NOT NULL,         -- ISO datetime | cron expr | seconds
                    input TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    next_fire_at TEXT NOT NULL,
                    last_fire_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sched_next ON schedules(enabled, next_fire_at);
            """)
        _INIT = True


# ---------------------------------------------------------------------------
# Trigger math
# ---------------------------------------------------------------------------

def _parse_cron_field(field: str, lo: int, hi: int) -> set[int]:
    """Tiny cron expander supporting `*`, `*/N`, `a,b,c`, `a-b`."""
    out: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if part == "*":
            out.update(range(lo, hi + 1))
            continue
        if part.startswith("*/"):
            step = int(part[2:])
            out.update(range(lo, hi + 1, step))
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
            continue
        out.add(int(part))
    return {v for v in out if lo <= v <= hi}


def _next_cron(spec: str, after: datetime) -> datetime:
    """Return the next firing >= `after` for a 5-field cron (UTC).
    Fields: minute hour dom month dow."""
    minute, hour, dom, month, dow = spec.split()
    minutes = _parse_cron_field(minute, 0, 59)
    hours = _parse_cron_field(hour, 0, 23)
    doms = _parse_cron_field(dom, 1, 31)
    months = _parse_cron_field(month, 1, 12)
    dows = _parse_cron_field(dow, 0, 6)  # 0 = Sunday (cron) — Python uses Mon=0; mapping below.

    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(60 * 24 * 366):  # bound at ~1 year of minutes
        py_dow = (candidate.weekday() + 1) % 7  # Mon..Sun (0..6) → Sun..Sat (0..6) cron-ish
        if (
            candidate.minute in minutes
            and candidate.hour in hours
            and candidate.day in doms
            and candidate.month in months
            and py_dow in dows
        ):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError(f"cron {spec!r} did not match within 1 year")


def _next_fire(kind: str, spec: str, after: datetime) -> datetime:
    if kind == "once":
        target = datetime.fromisoformat(spec)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return target
    if kind == "interval":
        seconds = int(float(spec))
        return after + timedelta(seconds=seconds)
    if kind == "cron":
        return _next_cron(spec, after)
    raise ValueError(f"unknown kind {kind!r}")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def add_schedule(kind: str, spec: str, input_text: str, *, priority: int = 0) -> str:
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be one of {VALID_KINDS}")
    _init_schema()
    sid = uuid.uuid4().hex[:16]
    nxt = _next_fire(kind, spec, _now())
    ts = _iso(_now())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO schedules (schedule_id, kind, spec, input, priority, "
            "enabled, next_fire_at, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (sid, kind, spec, input_text, int(priority), _iso(nxt), ts),
        )
    return sid


def list_schedules() -> list[dict]:
    _init_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM schedules ORDER BY next_fire_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_schedule(schedule_id: str) -> bool:
    _init_schema()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM schedules WHERE schedule_id=?", (schedule_id,))
        return cur.rowcount > 0


def disable_schedule(schedule_id: str) -> bool:
    _init_schema()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE schedules SET enabled=0 WHERE schedule_id=?", (schedule_id,)
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Tick loop
# ---------------------------------------------------------------------------

def tick_once(now: Optional[datetime] = None) -> list[str]:
    """Fire any schedule whose next_fire_at <= now. Returns the list of
    enqueued task_ids. For 'once' schedules we delete after firing; for
    'cron' / 'interval' we update next_fire_at to the upcoming target."""
    _init_schema()
    from core import task_queue
    now = now or _now()
    enqueued: list[str] = []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE enabled=1 AND next_fire_at<=?",
            (_iso(now),),
        ).fetchall()
    for row in rows:
        try:
            task_id = task_queue.enqueue(row["input"], priority=int(row["priority"]))
            enqueued.append(task_id)
            try:
                from core import event_bus
                event_bus.publish_remote(
                    "scheduler_fired",
                    schedule_id=row["schedule_id"], kind=row["kind"],
                    spec=row["spec"], task_id=task_id,
                )
            except Exception:
                pass
        except Exception as exc:
            log.warning("scheduler enqueue failed for %s: %s", row["schedule_id"], exc)
            continue
        if row["kind"] == "once":
            with _connect() as conn:
                conn.execute(
                    "DELETE FROM schedules WHERE schedule_id=?",
                    (row["schedule_id"],),
                )
            continue
        try:
            nxt = _next_fire(row["kind"], row["spec"], now)
        except Exception as exc:
            log.warning("scheduler next_fire failed for %s: %s", row["schedule_id"], exc)
            with _connect() as conn:
                conn.execute(
                    "UPDATE schedules SET enabled=0 WHERE schedule_id=?",
                    (row["schedule_id"],),
                )
            continue
        with _connect() as conn:
            conn.execute(
                "UPDATE schedules SET next_fire_at=?, last_fire_at=? WHERE schedule_id=?",
                (_iso(nxt), _iso(now), row["schedule_id"]),
            )
    return enqueued


def run_forever(poll_seconds: float = 10.0) -> None:
    log.info("nexus scheduler started (poll=%.0fs)", poll_seconds)
    while True:
        try:
            ids = tick_once()
            if ids:
                log.info("fired %d schedule(s) → tasks=%s", len(ids), ids)
        except Exception as exc:
            log.exception("tick error: %s", exc)
        time.sleep(poll_seconds)

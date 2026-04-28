"""Persistent task queue (Phase 15.2).

A single-writer, multi-reader SQLite queue stored in `memory/tasks.db`
(WAL, busy_timeout). Schema:

    tasks(
        task_id TEXT PRIMARY KEY,        -- uuid hex
        status TEXT NOT NULL,            -- pending | running | paused | done | cancelled | failed
        kind TEXT NOT NULL DEFAULT 'agent',
        priority INTEGER NOT NULL DEFAULT 0,
        thread_id TEXT,                  -- LangGraph checkpoint namespace (per-task isolation)
        input TEXT NOT NULL,             -- prompt / payload (json or plain)
        output TEXT,                     -- final reply
        error TEXT,
        modifications TEXT,              -- JSON list of {ts, note} entries the conversation handler appended
        owner TEXT,                      -- worker pid that claimed it
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT
    )

The conversation handler writes new tasks via `enqueue`; the worker pulls
the oldest pending row via `claim_next` (FOR UPDATE-style: status->'running'
in the same transaction). Both use plain `sqlite3` because the queue is a
small set of short transactions — the agent's heavy state lives in
`checkpoints.db`.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path.home() / "AI_Agent"
DB_PATH = ROOT / "memory" / "tasks.db"

VALID_STATUSES = ("pending", "running", "paused", "done", "cancelled", "failed")

log = logging.getLogger("nexus.task_queue")
_INIT_LOCK = threading.Lock()
_INITIALISED = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _connect():
    _ensure_dir()
    conn = sqlite3.connect(str(DB_PATH), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()


def _init_schema() -> None:
    global _INITIALISED
    if _INITIALISED:
        return
    with _INIT_LOCK:
        if _INITIALISED:
            return
        with _connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'agent',
                    priority INTEGER NOT NULL DEFAULT 0,
                    thread_id TEXT,
                    input TEXT NOT NULL,
                    output TEXT,
                    error TEXT,
                    modifications TEXT,
                    owner TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_priority_created ON tasks(priority DESC, created_at ASC);
                """
            )
        _INITIALISED = True


def enqueue(input_text: str, *, kind: str = "agent", priority: int = 0, thread_id: str | None = None) -> str:
    """Insert a pending task. Returns the task_id."""
    _init_schema()
    task_id = uuid.uuid4().hex[:16]
    ts = _now()
    tid = thread_id or f"task:{task_id}"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, status, kind, priority, thread_id, input, "
            "modifications, created_at, updated_at) VALUES (?, 'pending', ?, ?, ?, ?, '[]', ?, ?)",
            (task_id, kind, priority, tid, input_text, ts, ts),
        )
    return task_id


def claim_next(owner: Optional[str] = None) -> Optional[dict]:
    """Atomically grab the highest-priority oldest pending task, set status=running.
    Returns the row as a dict, or None if the queue is empty."""
    _init_schema()
    owner = owner or f"pid:{os.getpid()}"
    ts = _now()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE status='pending' "
            "ORDER BY priority DESC, created_at ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE tasks SET status='running', started_at=?, updated_at=?, owner=? "
            "WHERE task_id=? AND status='pending'",
            (ts, ts, owner, row["task_id"]),
        )
        # Re-read so caller sees the new state.
        row = conn.execute(
            "SELECT * FROM tasks WHERE task_id=?", (row["task_id"],)
        ).fetchone()
    return dict(row) if row else None


def update_status(task_id: str, status: str, *, output: str | None = None, error: str | None = None) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    _init_schema()
    finished = _now() if status in ("done", "cancelled", "failed") else None
    with _connect() as conn:
        conn.execute(
            "UPDATE tasks SET status=?, output=COALESCE(?, output), error=COALESCE(?, error), "
            "updated_at=?, finished_at=COALESCE(?, finished_at) WHERE task_id=?",
            (status, output, error, _now(), finished, task_id),
        )


def append_modification(task_id: str, note: str) -> None:
    _init_schema()
    with _connect() as conn:
        row = conn.execute("SELECT modifications FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            return
        try:
            mods = json.loads(row["modifications"] or "[]")
        except json.JSONDecodeError:
            mods = []
        mods.append({"ts": _now(), "note": note[:1000]})
        conn.execute(
            "UPDATE tasks SET modifications=?, updated_at=? WHERE task_id=?",
            (json.dumps(mods, ensure_ascii=False), _now(), task_id),
        )


def get_task(task_id: str) -> Optional[dict]:
    _init_schema()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_tasks(*, status: str | None = None, limit: int = 50) -> list[dict]:
    _init_schema()
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def cancel(task_id: str, note: str = "") -> bool:
    """Flip a non-terminal task to cancelled. Returns True if the row was updated."""
    _init_schema()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='cancelled', updated_at=?, finished_at=?, error=COALESCE(error, ?) "
            "WHERE task_id=? AND status IN ('pending','running','paused')",
            (_now(), _now(), note or None, task_id),
        )
        return cur.rowcount > 0


def pause(task_id: str) -> bool:
    _init_schema()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='paused', updated_at=? WHERE task_id=? AND status='running'",
            (_now(), task_id),
        )
        return cur.rowcount > 0


def resume(task_id: str) -> bool:
    _init_schema()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='pending', updated_at=? WHERE task_id=? AND status='paused'",
            (_now(), task_id),
        )
        return cur.rowcount > 0

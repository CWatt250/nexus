"""Persistent Telegram chat log (Phase 38).

Backs the quick_chat conversation buffer: every inbound user message and
every outbound assistant reply is written here, scoped by chat_id. The
quick_chat path queries the last N turns within the last M hours to
build a rolling-context messages array for DeepSeek.

Schema (memory/telegram_chats.db):

    chats(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
        content TEXT NOT NULL,
        ts INTEGER NOT NULL  -- unix epoch seconds
    )
    INDEX idx_chats_chat_id_ts ON chats(chat_id, ts DESC)

Plain sqlite3, same WAL + busy_timeout pattern as task_queue. Writes are
fire-and-forget — `write_turn` swallows OSError and logs, so a DB hiccup
never blocks a Telegram reply.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

ROOT = Path.home() / "AI_Agent"
DEFAULT_DB_PATH = ROOT / "memory" / "telegram_chats.db"

VALID_ROLES = ("user", "assistant")

log = logging.getLogger("nexus.telegram_chats")
_INIT_LOCK = threading.Lock()
_INITIALISED_PATHS: set[str] = set()


def _resolve_db_path(db_path: Optional[Path | str] = None) -> Path:
    if db_path is None:
        return DEFAULT_DB_PATH
    p = Path(db_path)
    if not p.is_absolute():
        p = ROOT / p
    return p


@contextmanager
def _connect(db_path: Optional[Path | str] = None):
    path = _resolve_db_path(db_path)
    _ensure_initialised(path)
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        yield conn
    finally:
        conn.close()


def _ensure_initialised(path: Path) -> None:
    key = str(path)
    if key in _INITIALISED_PATHS:
        return
    with _INIT_LOCK:
        if key in _INITIALISED_PATHS:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    ts INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chats_chat_id_ts "
                "ON chats(chat_id, ts DESC)"
            )
        finally:
            conn.close()
        _INITIALISED_PATHS.add(key)


def write_turn(chat_id: int, role: str, content: str,
               *, db_path: Optional[Path | str] = None,
               ts: Optional[int] = None) -> None:
    """Append a single turn. Best-effort — never raises on storage failure
    so a Telegram reply path can't be blocked by a DB hiccup."""
    if role not in VALID_ROLES:
        log.warning("write_turn: invalid role %r — skipping", role)
        return
    if not content:
        return
    ts_val = int(ts if ts is not None else time.time())
    try:
        with _connect(db_path) as conn:
            conn.execute(
                "INSERT INTO chats (chat_id, role, content, ts) VALUES (?, ?, ?, ?)",
                (int(chat_id), role, content, ts_val),
            )
    except (sqlite3.Error, OSError) as exc:
        log.warning("write_turn failed (chat_id=%s role=%s): %s",
                    chat_id, role, exc)


def fetch_recent_turns(chat_id: int, *,
                       max_turns: int = 20,
                       max_age_hours: float = 2.0,
                       db_path: Optional[Path | str] = None,
                       now: Optional[int] = None) -> list[dict]:
    """Return up to `max_turns` most recent turns for chat_id within
    `max_age_hours`, in chronological order (oldest first) — ready to
    drop into an OpenAI-style messages array between system and the
    current user message.

    Returns an empty list on any storage error so callers fall back to
    stateless behavior.
    """
    cutoff = int((now if now is not None else time.time()) - max_age_hours * 3600)
    try:
        with _connect(db_path) as conn:
            cur = conn.execute(
                "SELECT role, content FROM chats "
                "WHERE chat_id = ? AND ts >= ? "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (int(chat_id), cutoff, int(max_turns)),
            )
            rows = cur.fetchall()
    except (sqlite3.Error, OSError) as exc:
        log.warning("fetch_recent_turns failed (chat_id=%s): %s", chat_id, exc)
        return []
    # Reverse to chronological order.
    return [{"role": role, "content": content} for role, content in reversed(rows)]


def delete_older_than(days: int, *, db_path: Optional[Path | str] = None) -> int:
    """Retention helper — delete rows older than `days`. Returns row count.
    Not wired into a cron yet; called manually or by a future Phase 38.1
    timer."""
    cutoff = int(time.time() - days * 86400)
    try:
        with _connect(db_path) as conn:
            cur = conn.execute("DELETE FROM chats WHERE ts < ?", (cutoff,))
            return cur.rowcount or 0
    except (sqlite3.Error, OSError) as exc:
        log.warning("delete_older_than failed: %s", exc)
        return 0

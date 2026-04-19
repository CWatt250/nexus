#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Nexus session utility.

Tracks LangGraph checkpoint sessions via a sidecar sessions.json file so we
can list, inspect, and prune them without spelunking inside SqliteSaver.

Usage:
    python3 ~/AI_Agent/memory/sessions.py list
    python3 ~/AI_Agent/memory/sessions.py load <thread_id>
    python3 ~/AI_Agent/memory/sessions.py delete-old [--days 30]
    python3 ~/AI_Agent/memory/sessions.py delete <thread_id>
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

MEMORY = Path.home() / "AI_Agent" / "memory"
DB = MEMORY / "checkpoints.db"
SESSIONS_JSON = MEMORY / "sessions.json"
CURRENT_THREAD = MEMORY / "current_thread.txt"


# ---------------------------------------------------------------------------
# sessions.json helpers (used by nexus.py + nexus_api.py too)
# ---------------------------------------------------------------------------

def _load_sessions() -> dict:
    if not SESSIONS_JSON.exists():
        return {}
    try:
        return json.loads(SESSIONS_JSON.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_sessions(data: dict) -> None:
    SESSIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_JSON.write_text(json.dumps(data, indent=2))


def touch_session(thread_id: str, *, source: str = "nexus", first_msg: str | None = None) -> None:
    """Create or update the session record for thread_id."""
    if not thread_id:
        return
    now = int(time.time())
    data = _load_sessions()
    rec = data.setdefault(thread_id, {"created_at": now, "source": source})
    if first_msg and not rec.get("title"):
        rec["title"] = first_msg.strip()[:80]
    rec["last_used"] = now
    rec["source"] = source
    data[thread_id] = rec
    _save_sessions(data)


def get_current_thread() -> str | None:
    if not CURRENT_THREAD.exists():
        return None
    try:
        v = CURRENT_THREAD.read_text().strip()
        return v or None
    except OSError:
        return None


def set_current_thread(thread_id: str | None) -> None:
    if thread_id is None:
        CURRENT_THREAD.unlink(missing_ok=True)
        return
    CURRENT_THREAD.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_THREAD.write_text(thread_id.strip())


# ---------------------------------------------------------------------------
# Inspection helpers that touch the SqliteSaver db
# ---------------------------------------------------------------------------

def _thread_checkpoint_counts() -> dict[str, int]:
    if not DB.exists():
        return {}
    try:
        conn = sqlite3.connect(DB)
        cur = conn.execute("SELECT thread_id, COUNT(*) FROM checkpoints GROUP BY thread_id")
        out = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()
        return out
    except sqlite3.Error:
        return {}


def _load_last_messages(thread_id: str) -> list:
    """Return the message list from the latest checkpoint of `thread_id`."""
    if not DB.exists():
        return []
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        return []
    conn = sqlite3.connect(DB, check_same_thread=False)
    try:
        saver = SqliteSaver(conn)
        tup = saver.get_tuple({"configurable": {"thread_id": thread_id}})
        if not tup:
            return []
        vals = tup.checkpoint.get("channel_values") or {}
        return vals.get("messages") or []
    finally:
        conn.close()


def _delete_thread_rows(thread_id: str) -> int:
    if not DB.exists():
        return 0
    conn = sqlite3.connect(DB)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
        n1 = cur.rowcount
        try:
            cur.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
        except sqlite3.Error:
            pass
        try:
            cur.execute("DELETE FROM checkpoint_writes WHERE thread_id = ?", (thread_id,))
        except sqlite3.Error:
            pass
        conn.commit()
        return n1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _fmt_ts(ts: int | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def cmd_list(_args) -> int:
    sessions = _load_sessions()
    counts = _thread_checkpoint_counts()
    # also surface any threads present in the db but missing from sessions.json
    for tid in counts:
        sessions.setdefault(tid, {})
    if not sessions:
        print("(no sessions)")
        return 0
    rows = []
    for tid, rec in sessions.items():
        rows.append({
            "tid": tid,
            "created": rec.get("created_at", 0),
            "last": rec.get("last_used", 0),
            "title": rec.get("title", ""),
            "source": rec.get("source", ""),
            "checkpoints": counts.get(tid, 0),
        })
    rows.sort(key=lambda r: r["last"], reverse=True)
    cur = get_current_thread()
    print(f"{'current':1} {'thread_id':36}  {'created':16}  {'last used':16}  {'chkpt':>5}  {'src':<7}  title")
    print("-" * 120)
    for r in rows:
        mark = "*" if r["tid"] == cur else " "
        title = (r["title"] or "")[:40]
        print(f"{mark}       {r['tid']:36}  {_fmt_ts(r['created']):16}  {_fmt_ts(r['last']):16}  {r['checkpoints']:>5}  {r['source']:<7}  {title}")
    return 0


def cmd_load(args) -> int:
    tid = args.thread_id
    msgs = _load_last_messages(tid)
    sessions = _load_sessions()
    meta = sessions.get(tid, {})
    if meta:
        print(f"thread_id:  {tid}")
        print(f"created:    {_fmt_ts(meta.get('created_at'))}")
        print(f"last used:  {_fmt_ts(meta.get('last_used'))}")
        print(f"title:      {meta.get('title','')}")
        print()
    if not msgs:
        print("(no messages found)")
        return 1
    print(f"{len(msgs)} messages:")
    print("-" * 80)
    for m in msgs:
        role = m.__class__.__name__.replace("Message", "").lower() if not isinstance(m, dict) else m.get("type", "?")
        content = getattr(m, "content", None) if not isinstance(m, dict) else m.get("content")
        if isinstance(content, list):
            content = " ".join(str(c.get("text", c)) if isinstance(c, dict) else str(c) for c in content)
        text = str(content or "").strip()
        if len(text) > 600:
            text = text[:600] + "…"
        print(f"[{role}]")
        print(text)
        print()
    return 0


def cmd_delete(args) -> int:
    n = _delete_thread_rows(args.thread_id)
    sessions = _load_sessions()
    sessions.pop(args.thread_id, None)
    _save_sessions(sessions)
    if get_current_thread() == args.thread_id:
        set_current_thread(None)
    print(f"deleted {args.thread_id} (removed {n} checkpoint rows)")
    return 0


def cmd_delete_old(args) -> int:
    cutoff = int(time.time()) - args.days * 86400
    sessions = _load_sessions()
    victims = [tid for tid, rec in sessions.items() if (rec.get("last_used") or rec.get("created_at") or 0) < cutoff]
    if not victims:
        print(f"no sessions older than {args.days} days")
        return 0
    total = 0
    for tid in victims:
        total += _delete_thread_rows(tid)
        sessions.pop(tid, None)
    _save_sessions(sessions)
    print(f"deleted {len(victims)} session(s) older than {args.days} days ({total} checkpoint rows removed)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Nexus session utility")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List sessions")

    lp = sub.add_parser("load", help="Show messages for a session")
    lp.add_argument("thread_id")

    dp = sub.add_parser("delete", help="Delete a single session")
    dp.add_argument("thread_id")

    op = sub.add_parser("delete-old", help="Delete sessions older than N days (default 30)")
    op.add_argument("--days", type=int, default=30)

    args = p.parse_args(argv)
    return {"list": cmd_list, "load": cmd_load, "delete": cmd_delete, "delete-old": cmd_delete_old}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

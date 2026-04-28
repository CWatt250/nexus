"""Conversation handler (Phase 15.4 + 15.5).

A small, fast surface that answers Telegram / API messages about *running*
tasks without ever pulling a heavy model into the request path.

Two layers:

  1. Pattern-based intent classifier (`classify_intent`) handles the four
     status/modify/cancel/new-task shapes deterministically — no LLM in
     the loop. These are the operations the spec requires under <5s.
  2. Free-form chat falls through to a tiny ReAct agent on qwen3:4b that
     can still call any HANDLER_TOOLS, but with a short timeout so it
     never blocks Telegram.

Long-running work is never executed here. `queue_new_task` enqueues to
the task_worker (Phase 15.3). The handler keeps its own LangGraph
checkpointer namespace (`thread_id="handler:..."`) so its conversation
state stays isolated from any task's state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import nexus  # noqa: E402  — loads tools, prompt, etc.
from core import task_queue  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

log = logging.getLogger("nexus.conversation_handler")

HANDLER_MODEL = "qwen3:4b"
HANDLER_PROMPT = (
    "You are Nexus's conversation handler. You only manage tasks — you do "
    "NOT run them. Use tools to inspect the task queue, pause, cancel, or "
    "modify in-flight tasks, and to queue new tasks for the worker. Reply "
    "in 1-3 short sentences. If asked anything you can't answer with these "
    "tools, queue_new_task and tell the user you've handed it off."
)


@tool
def get_task_status(task_id: str = "") -> str:
    """Return status of one task (when task_id is given) or a list of the
    most recent tasks (when omitted)."""
    if task_id:
        row = task_queue.get_task(task_id)
        if not row:
            return f"no task with id {task_id}"
        return json.dumps({
            "task_id": row["task_id"],
            "status": row["status"],
            "kind": row["kind"],
            "thread_id": row["thread_id"],
            "input_preview": (row["input"] or "")[:160],
            "output_preview": (row.get("output") or "")[:160],
            "error": row.get("error"),
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "modifications": row.get("modifications"),
        }, ensure_ascii=False)
    rows = task_queue.list_tasks(limit=10)
    if not rows:
        return "queue empty"
    out_lines = []
    for r in rows:
        out_lines.append(
            f"{r['task_id']}  status={r['status']}  "
            f"started={r['started_at'] or '-'}  "
            f"input={(r['input'] or '')[:60]!r}"
        )
    return "\n".join(out_lines)


@tool
def pause_task(task_id: str) -> str:
    """Pause a running task (worker checks status between turns)."""
    return f"paused" if task_queue.pause(task_id) else "not running — nothing to pause"


@tool
def cancel_task(task_id: str, note: str = "") -> str:
    """Cancel a pending/running/paused task. Worker stops before its next turn."""
    return "cancelled" if task_queue.cancel(task_id, note) else "already finished — nothing to cancel"


@tool
def modify_task(task_id: str, note: str) -> str:
    """Append a modification note to a task's history. The worker reads these
    between turns so the user can refine scope without re-queuing."""
    task_queue.append_modification(task_id, note)
    return "noted"


@tool
def queue_new_task(input_text: str, priority: int = 0) -> str:
    """Enqueue a new heavy task for the worker to pick up. Returns the task_id."""
    if not input_text.strip():
        return "refusing: empty input"
    tid = task_queue.enqueue(input_text, priority=int(priority))
    return f"queued task {tid}"


HANDLER_TOOLS = [get_task_status, pause_task, cancel_task, modify_task, queue_new_task]


def _build_handler_agent_sync():
    """Build a sync ReAct agent on qwen3:4b with HANDLER_TOOLS only.

    Uses the existing _CHECKPOINTER (sync) namespaced via thread_id so the
    handler's conversation state never collides with any task's state."""
    from langgraph.prebuilt import create_react_agent
    from langchain_ollama import ChatOllama
    llm = ChatOllama(model=HANDLER_MODEL, base_url=nexus.OLLAMA_URL, reasoning=False)
    return create_react_agent(llm, HANDLER_TOOLS, prompt=HANDLER_PROMPT, checkpointer=nexus._CHECKPOINTER)


_handler_agent = None


def get_agent():
    global _handler_agent
    if _handler_agent is None:
        _handler_agent = _build_handler_agent_sync()
    return _handler_agent


_HANDLER_SAVER = None


async def _get_handler_saver():
    """Build a dedicated AsyncSqliteSaver on its own aiosqlite connection
    so handler turns don't queue behind the task worker's heavy checkpoint
    writes. Both connections share the same WAL'd checkpoints.db file."""
    global _HANDLER_SAVER
    if _HANDLER_SAVER is None:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        conn = await aiosqlite.connect(str(nexus.CHECKPOINT_DB), check_same_thread=False)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.commit()
        saver = AsyncSqliteSaver(conn)
        try:
            await saver.setup()
        except Exception:
            pass
        _HANDLER_SAVER = saver
    return _HANDLER_SAVER


async def _build_handler_agent_async():
    from langgraph.prebuilt import create_react_agent
    from langchain_ollama import ChatOllama
    saver = await _get_handler_saver()
    llm = ChatOllama(model=HANDLER_MODEL, base_url=nexus.OLLAMA_URL, reasoning=False)
    return create_react_agent(llm, HANDLER_TOOLS, prompt=HANDLER_PROMPT, checkpointer=saver)


_handler_agent_async = None


async def get_agent_async():
    global _handler_agent_async
    if _handler_agent_async is None:
        _handler_agent_async = await _build_handler_agent_async()
    return _handler_agent_async


_TASK_ID_RE = re.compile(r"\b([0-9a-f]{8,16})\b", re.IGNORECASE)
_STATUS_VERB_RE = re.compile(
    r"\b(status|state|progress|how[\s_-]*is|what'?s|where['\s_-]*is|check|where stand)\b",
    re.IGNORECASE,
)
_CANCEL_VERB_RE = re.compile(r"\b(cancel|abort|kill|stop)\b", re.IGNORECASE)
_PAUSE_VERB_RE = re.compile(r"\b(pause|hold)\b", re.IGNORECASE)
_LIST_VERB_RE = re.compile(r"\b(list|show|recent|what.*tasks?|all\s+tasks?)\b", re.IGNORECASE)
_QUEUE_VERB_RE = re.compile(
    r"^(queue|enqueue|new\s+task|add\s+task|launch|run|please)\b[: ]?\s*(.*)",
    re.IGNORECASE,
)
_MODIFY_VERB_RE = re.compile(
    r"\b(modify|note|update|append|add\s+note|annotate)\b",
    re.IGNORECASE,
)


def classify_intent(message: str) -> dict:
    """Cheap pattern-based intent classifier. Returns one of:
      {kind: 'status', task_id}
      {kind: 'list'}
      {kind: 'cancel', task_id} | 'pause'
      {kind: 'modify', task_id, note}
      {kind: 'queue', input}
      {kind: 'chat'}
    """
    msg = (message or "").strip()
    if not msg:
        return {"kind": "chat"}
    tid_match = _TASK_ID_RE.search(msg)
    tid = tid_match.group(1) if tid_match else None
    if _CANCEL_VERB_RE.search(msg) and tid:
        return {"kind": "cancel", "task_id": tid}
    if _PAUSE_VERB_RE.search(msg) and tid:
        return {"kind": "pause", "task_id": tid}
    if _MODIFY_VERB_RE.search(msg) and tid:
        note = _TASK_ID_RE.sub("", msg)
        for re_ in (_MODIFY_VERB_RE, _STATUS_VERB_RE):
            note = re_.sub("", note, count=1)
        note = note.strip(" :,.;-")
        if note:
            return {"kind": "modify", "task_id": tid, "note": note}
    if tid and (_STATUS_VERB_RE.search(msg) or len(msg) < 40):
        return {"kind": "status", "task_id": tid}
    if _LIST_VERB_RE.search(msg) and not tid:
        return {"kind": "list"}
    qm = _QUEUE_VERB_RE.match(msg)
    if qm:
        body = qm.group(2).strip(" :")
        if body:
            return {"kind": "queue", "input": body}
    return {"kind": "chat"}


def _format_status(row: dict | None, task_id: str) -> str:
    if not row:
        return f"no task with id {task_id}"
    bits = [f"task {row['task_id']} → {row['status']}", f"created {row['created_at']}"]
    if row.get("started_at"):
        bits.append(f"started {row['started_at']}")
    if row.get("finished_at"):
        bits.append(f"finished {row['finished_at']}")
    if row.get("output"):
        bits.append("output: " + (row["output"] or "")[:200].replace("\n", " "))
    if row.get("error"):
        bits.append(f"error: {row['error']}")
    return "\n".join(bits)


def _busy_summary() -> str:
    """One-line summary of any running task; empty string if idle."""
    rows = task_queue.list_tasks(limit=10)
    running = [r for r in rows if r["status"] == "running"]
    if not running:
        return ""
    r = running[0]
    return f"running: {r['task_id']} ({(r['input'] or '')[:60]})"


def fast_handle(message: str, *, allow_llm_chat: bool = True) -> str | None:
    """Synchronous, no-LLM fast path. Returns a reply for status/list/
    cancel/pause/modify/queue intents in microseconds. For chat with a
    running task, returns a busy-with-task template (still <5s).

    Returns None only when the caller has set `allow_llm_chat=True` AND
    the queue is idle — in which case the LLM fallback is allowed to
    answer free-form chat. With the default allow_llm_chat=True we keep
    that escape hatch; for Telegram-bot path the caller passes False so
    chat never blocks on a contended LLM."""
    intent = classify_intent(message)
    kind = intent["kind"]
    if kind == "list":
        rows = task_queue.list_tasks(limit=10)
        if not rows:
            return "Queue is empty."
        return "\n".join(
            f"- {r['task_id']} [{r['status']}] {(r['input'] or '')[:60]}" for r in rows
        )
    if kind == "status":
        return _format_status(task_queue.get_task(intent["task_id"]), intent["task_id"])
    if kind == "cancel":
        ok = task_queue.cancel(intent["task_id"], note="cancelled via handler")
        return "cancelled." if ok else "already finished — nothing to cancel."
    if kind == "pause":
        ok = task_queue.pause(intent["task_id"])
        return "paused." if ok else "not running — nothing to pause."
    if kind == "modify":
        task_queue.append_modification(intent["task_id"], intent["note"])
        return f"noted on task {intent['task_id']}."
    if kind == "queue":
        tid = task_queue.enqueue(intent["input"])
        return f"queued task {tid}."
    # kind == 'chat'
    busy = _busy_summary()
    if busy:
        return (
            f"I'm here. A task is in flight — {busy}. "
            "For free-form chat without contention, wait until it finishes "
            "or send: 'queue: <your task>' to enqueue."
        )
    return None if allow_llm_chat else (
        "I'm here. The queue is idle. Send 'queue: <your task>' to launch one, "
        "or '<task_id>' to check a specific task's status."
    )


def handle_sync(message: str, *, thread_id: str = "handler:default") -> str:
    """Sync handler entrypoint. Tries the no-LLM fast path first; falls
    back to the qwen3:4b ReAct agent for free-form chat."""
    fast = fast_handle(message)
    if fast is not None:
        return fast
    agent = get_agent()
    config = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, config=config)
    msgs = result.get("messages", [])
    for m in reversed(msgs):
        if m.__class__.__name__ == "AIMessage" and getattr(m, "content", ""):
            return nexus.strip_thinking(m.content)
    return ""


async def handle_async(message: str, *, thread_id: str = "handler:default") -> str:
    """Async handler entrypoint. Same two-tier strategy as handle_sync."""
    fast = fast_handle(message)
    if fast is not None:
        return fast
    agent = await get_agent_async()
    config = {"configurable": {"thread_id": thread_id}}
    result = await agent.ainvoke({"messages": [HumanMessage(content=message)]}, config=config)
    msgs = result.get("messages", [])
    for m in reversed(msgs):
        if m.__class__.__name__ == "AIMessage" and getattr(m, "content", ""):
            return nexus.strip_thinking(m.content)
    return ""


def main() -> int:
    """CLI smoke entrypoint: read a single line from argv, print the handler reply."""
    if len(sys.argv) < 2:
        print("usage: conversation_handler.py <message>", file=sys.stderr)
        return 2
    msg = " ".join(sys.argv[1:])
    print(handle_sync(msg))
    return 0


if __name__ == "__main__":
    sys.exit(main())

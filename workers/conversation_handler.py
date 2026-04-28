"""Conversation handler (Phase 15.4).

A small, fast surface that answers Telegram / API messages about *running*
tasks without ever pulling a heavy model into the request path. It uses
qwen3:4b only (router model, pinned via Phase 13.1) and exposes five
tools that read/modify the task queue:

  - get_task_status(task_id?)   → status snapshot or list
  - pause_task(task_id)
  - cancel_task(task_id)
  - modify_task(task_id, note)
  - queue_new_task(input, priority?)

Long-running work is never executed here — `queue_new_task` enqueues to
the task_worker (Phase 15.3). The handler keeps its own LangGraph
checkpointer namespace (`thread_id="handler:..."`) so its conversation
state stays separate from any in-flight task's state.

Used as a library (`HANDLER_TOOLS`) by the Telegram bot and any other
fast-path entrypoint. Also exposes `handle_async(message, thread_id)` for
direct calls.
"""
from __future__ import annotations

import asyncio
import json
import logging
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


async def _build_handler_agent_async():
    from langgraph.prebuilt import create_react_agent
    from langchain_ollama import ChatOllama
    saver = await nexus._get_async_checkpointer()
    llm = ChatOllama(model=HANDLER_MODEL, base_url=nexus.OLLAMA_URL, reasoning=False)
    return create_react_agent(llm, HANDLER_TOOLS, prompt=HANDLER_PROMPT, checkpointer=saver)


_handler_agent_async = None


async def get_agent_async():
    global _handler_agent_async
    if _handler_agent_async is None:
        _handler_agent_async = await _build_handler_agent_async()
    return _handler_agent_async


def handle_sync(message: str, *, thread_id: str = "handler:default") -> str:
    """Sync handler entrypoint. Returns the assistant reply text."""
    agent = get_agent()
    config = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, config=config)
    msgs = result.get("messages", [])
    for m in reversed(msgs):
        if m.__class__.__name__ == "AIMessage" and getattr(m, "content", ""):
            return nexus.strip_thinking(m.content)
    return ""


async def handle_async(message: str, *, thread_id: str = "handler:default") -> str:
    """Async handler entrypoint."""
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

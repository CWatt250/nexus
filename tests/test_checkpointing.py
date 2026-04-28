"""Per-task checkpoint isolation tests (Phase 15.6)."""
from __future__ import annotations

import socket

import pytest


def _ollama_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _ollama_up(), reason="Ollama not running on :11434")


def test_task_queue_assigns_per_task_thread_id() -> None:
    """enqueue() stamps each row with thread_id='task:<id>' so LangGraph
    sees a unique checkpoint namespace per task."""
    from core import task_queue
    a = task_queue.enqueue("isolation a")
    b = task_queue.enqueue("isolation b")
    ra = task_queue.get_task(a)
    rb = task_queue.get_task(b)
    assert ra["thread_id"] != rb["thread_id"]
    assert ra["thread_id"].startswith("task:")
    assert rb["thread_id"].startswith("task:")
    # Cleanup so we don't leave pending rows for the worker.
    task_queue.cancel(a)
    task_queue.cancel(b)


def test_handler_thread_id_is_distinct_from_task_thread_ids() -> None:
    """The conversation handler uses a 'handler:...' namespace that can
    never collide with a 'task:...' namespace."""
    from core import task_queue
    tid = task_queue.enqueue("handler-iso")
    row = task_queue.get_task(tid)
    handler_namespace = "handler:default"
    assert row["thread_id"] != handler_namespace
    assert not row["thread_id"].startswith("handler:")
    task_queue.cancel(tid)


def test_two_task_threads_have_independent_state() -> None:
    """Write a checkpoint snapshot under two different task thread_ids
    and confirm reading one never returns the other."""
    import nexus
    nexus.set_system_prompt(nexus.load_system_prompt())
    agent = nexus.build_agent("qwen3:4b")
    from langchain_core.messages import HumanMessage

    cfg_a = {"configurable": {"thread_id": "task:isolation-A"}}
    cfg_b = {"configurable": {"thread_id": "task:isolation-B"}}
    agent.invoke({"messages": [HumanMessage(content="reply with: ALPHA")]}, config=cfg_a)
    agent.invoke({"messages": [HumanMessage(content="reply with: BETA")]}, config=cfg_b)

    snap_a = agent.get_state(cfg_a)
    snap_b = agent.get_state(cfg_b)
    msgs_a = " ".join(getattr(m, "content", "") or "" for m in (getattr(snap_a, "values", {}) or {}).get("messages", []))
    msgs_b = " ".join(getattr(m, "content", "") or "" for m in (getattr(snap_b, "values", {}) or {}).get("messages", []))
    assert "ALPHA" in msgs_a or "alpha" in msgs_a.lower()
    assert "BETA" in msgs_b or "beta" in msgs_b.lower()
    # Crucially: A's history does not contain B's prompt and vice versa.
    assert "BETA" not in msgs_a and "beta" not in msgs_a.lower()
    assert "ALPHA" not in msgs_b and "alpha" not in msgs_b.lower()

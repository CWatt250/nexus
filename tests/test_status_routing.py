"""Tests for the STATUS classifier override and the datetime-prefix
strip on the recent-tasks display."""
from __future__ import annotations

from typing import List

import pytest


# --- 1. _is_genuine_queue_status -----------------------------------------
@pytest.mark.parametrize("msg,expected", [
    # Genuine queue references — STAY as STATUS
    ("queue status", True),
    ("what's in the queue", True),
    ("any tasks running right now", True),
    ("show me running tasks", True),
    ("any pending tasks", True),
    ("status of task abc12345", True),  # has a task_id
    ("is task ff00deadbeef done", True),
    # NOT queue references — promote to TASK
    ("what's my github auth status", False),
    ("github auth status", False),
    ("supabase status", False),
    ("ollama status", False),
    ("weather status", False),
    ("system status", False),
    ("wifi status", False),
])
def test_is_genuine_queue_status(msg: str, expected: bool) -> None:
    from workers.conversation_handler import _is_genuine_queue_status
    assert _is_genuine_queue_status(msg) is expected


# --- 2. _strip_datetime_prefix -------------------------------------------
def test_strip_datetime_prefix_removes_injected_block() -> None:
    from workers.conversation_handler import _strip_datetime_prefix
    raw = (
        "[Current date and time: 2026-04-29T17:38:44-07:00. "
        "Current day of week: Wednesday. When asked about the current "
        "time, date, or day, use ONLY the datetime above. Never guess "
        "or use training data.]\n\n"
        "what's my github auth status"
    )
    out = _strip_datetime_prefix(raw)
    assert out == "what's my github auth status"


def test_strip_datetime_prefix_is_noop_for_clean_input() -> None:
    from workers.conversation_handler import _strip_datetime_prefix
    raw = "hi nexus, list my repos"
    assert _strip_datetime_prefix(raw) == raw


def test_strip_datetime_prefix_handles_empty() -> None:
    from workers.conversation_handler import _strip_datetime_prefix
    assert _strip_datetime_prefix("") == ""
    assert _strip_datetime_prefix(None) is None  # type: ignore[arg-type]


# --- 3. _route_status renders user input, not the datetime block ---------
def test_route_status_strips_datetime_in_recent_list(monkeypatch) -> None:
    from workers import conversation_handler as ch

    rows = [
        {
            "task_id": "deadbeef01",
            "status": "done",
            "input": (
                "[Current date and time: 2026-04-29T17:38:44-07:00. "
                "Current day of week: Wednesday. When asked about the "
                "current time, date, or day, use ONLY the datetime above. "
                "Never guess or use training data.]\n\n"
                "what's my github auth status"
            ),
        },
        {
            "task_id": "abc1234567",
            "status": "running",
            "input": "summarize https://example.com/post",
        },
    ]
    monkeypatch.setattr(ch.task_queue, "list_tasks", lambda limit=10: rows)

    out = ch._route_status("any tasks running")
    assert "deadbeef01" in out
    assert "abc1234567" in out
    assert "what's my github auth status" in out
    assert "Current date and time" not in out
    assert "Current d" not in out  # the truncated leak from the bug report


# --- 4. route_message: 'github auth status' now caught by hard-override --
def test_route_message_routes_github_auth_status_to_query_tool(monkeypatch) -> None:
    """Fix #3 part 3 added a deterministic hard-override regex that
    catches '<tool/domain> status' shapes BEFORE the classifier runs.
    'what's my github auth status' should land on QUERY_TOOL via
    lite_agent — not get enqueued as a TASK and not even reach the
    STATUS-override branch."""
    from workers import conversation_handler as ch
    from workers.conversation_handler import Intent

    enq_calls: List[str] = []
    monkeypatch.setattr(
        ch.task_queue, "enqueue",
        lambda input_text, **_: (enq_calls.append(input_text) or "shouldNotHappen"),
    )
    monkeypatch.setattr(ch, "lite_agent", lambda m: {
        "ok": True, "tool": "github_auth_status",
        "reply": "Authenticated as CWatt250.",
    })
    # If something forces the classifier path, fail loudly.
    monkeypatch.setattr(ch, "classify_intent_llm",
                        lambda msg: Intent(kind="STATUS", raw="STATUS (should not fire)"))

    result = ch.route_message("what's my github auth status")
    assert result["kind"] == "query_tool"
    assert result["meta"].get("fast_tool_override") is True
    assert "CWatt250" in result["reply"]
    assert not enq_calls, "should not enqueue when hard-override succeeds"


# --- 5. route_message: STATUS stays STATUS for "queue status" ------------
def test_route_message_keeps_queue_status_as_status(monkeypatch) -> None:
    from workers import conversation_handler as ch
    from workers.conversation_handler import Intent

    monkeypatch.setattr(ch, "classify_intent_llm",
                        lambda msg: Intent(kind="STATUS", raw="STATUS"))
    monkeypatch.setattr(ch.task_queue, "list_tasks", lambda limit=10: [])

    result = ch.route_message("any tasks running right now")
    assert result["kind"] == "status"
    assert "queue is empty" in result["reply"].lower() or "no recent" in result["reply"].lower()


# --- 6. route_message: STATUS with a task_id stays STATUS ----------------
def test_route_message_keeps_explicit_task_id_as_status(monkeypatch) -> None:
    from workers import conversation_handler as ch
    from workers.conversation_handler import Intent

    monkeypatch.setattr(ch, "classify_intent_llm",
                        lambda msg: Intent(kind="STATUS", raw="STATUS"))
    monkeypatch.setattr(ch.task_queue, "get_task",
                        lambda tid: {
                            "task_id": tid,
                            "status": "done",
                            "input": "summarize x.com/foo",
                            "created_at": "2026-04-29T10:00:00Z",
                            "started_at": None,
                            "finished_at": None,
                            "output": "all done",
                            "error": None,
                        })

    result = ch.route_message("what's the status of task abc12345")
    assert result["kind"] == "status"
    assert "abc12345" in result["reply"]
    assert "summarize x.com/foo" in result["reply"]

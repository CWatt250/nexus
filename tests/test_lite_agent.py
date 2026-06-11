"""Tests for the QUERY_TOOL fast path (lite_agent) in workers.conversation_handler.

The Ollama HTTP layer is monkeypatched so these run offline. Live latency
smoke runs separately against the real container + models.
"""
from __future__ import annotations

from typing import Any

import pytest


# --- 1. picker_prompt_block lists every registered tool ------------------
def test_picker_prompt_block_lists_registry() -> None:
    from tools import lite_agent_tools
    block = lite_agent_tools.picker_prompt_block()
    for name in lite_agent_tools.get_registry():
        assert name in block, f"{name} missing from picker prompt"


# --- 2. lite_agent picks tool, invokes it, formats result ----------------
def test_lite_agent_happy_path(monkeypatch) -> None:
    from workers import conversation_handler as ch
    from tools import lite_agent_tools

    # Stub out the ollama chat call. First call = picker (returns JSON).
    # Second call = formatter (returns prose).
    calls = {"n": 0}

    def fake_chat(messages, *, timeout, num_predict=250, fmt=None):
        calls["n"] += 1
        if calls["n"] == 1:
            assert fmt == "json"
            return '{"tool": "searxng_search", "args": {"query": "weather Pasco WA", "count": 3}}'
        return "Sunny, 72F in Pasco, WA today."

    monkeypatch.setattr(ch, "_ollama_chat", fake_chat)

    # Stub the actual tool to avoid the real http call.
    fake_tool_invocation = {"called_with": None}

    class _StubTool:
        def invoke(self, args):
            fake_tool_invocation["called_with"] = args
            return "[search:searxng]\n- Sunny 72F\n  weather.com\n  forecast"

    registry = dict(lite_agent_tools.get_registry())
    registry["searxng_search"] = {**registry["searxng_search"], "tool": _StubTool()}
    monkeypatch.setattr(lite_agent_tools, "get_registry", lambda: registry)

    out = ch.lite_agent("what's the weather in Pasco WA")
    assert out["ok"] is True
    assert out["tool"] == "searxng_search"
    assert "Sunny" in out["reply"]
    # Fix #4 part B: SearXNG output triggers the search-top-hit shortcut,
    # so only the picker LLM runs — the formatter is skipped. Hence n==1.
    assert calls["n"] == 1
    assert out.get("fast_format") == "search_top_hit"
    assert fake_tool_invocation["called_with"]["query"] == "weather Pasco WA"


# --- 3. lite_agent falls through when picker returns _none ---------------
def test_lite_agent_falls_through_on_none(monkeypatch) -> None:
    from workers import conversation_handler as ch

    def fake_chat(messages, *, timeout, num_predict=250, fmt=None):
        return '{"tool": "_none", "args": {}}'

    monkeypatch.setattr(ch, "_ollama_chat", fake_chat)
    out = ch.lite_agent("write me a 5-page research report")
    assert out["ok"] is False
    assert "_none" in out["reason"]


# --- 4. lite_agent falls through on bogus tool name ----------------------
def test_lite_agent_falls_through_on_unknown_tool(monkeypatch) -> None:
    from workers import conversation_handler as ch

    def fake_chat(messages, *, timeout, num_predict=250, fmt=None):
        return '{"tool": "build_a_house", "args": {}}'

    monkeypatch.setattr(ch, "_ollama_chat", fake_chat)
    out = ch.lite_agent("anything")
    assert out["ok"] is False
    assert "not in lite registry" in out["reason"]


# --- 5. lite_agent falls through on non-JSON picker response -------------
def test_lite_agent_falls_through_on_bad_json(monkeypatch) -> None:
    from workers import conversation_handler as ch

    def fake_chat(messages, *, timeout, num_predict=250, fmt=None):
        return "Hmm let me think about which tool..."

    monkeypatch.setattr(ch, "_ollama_chat", fake_chat)
    out = ch.lite_agent("anything")
    assert out["ok"] is False


# --- 6. lite_agent survives a tool that raises ---------------------------
def test_lite_agent_handles_tool_exception(monkeypatch) -> None:
    from workers import conversation_handler as ch
    from tools import lite_agent_tools

    seq = iter([
        '{"tool": "searxng_health", "args": {}}',
        "The SearXNG container is currently unreachable; try again in a moment.",
    ])
    monkeypatch.setattr(ch, "_ollama_chat",
                        lambda messages, *, timeout, num_predict=250, fmt=None: next(seq))

    class _BoomTool:
        def invoke(self, args):
            raise RuntimeError("connection refused")

    registry = dict(lite_agent_tools.get_registry())
    registry["searxng_health"] = {**registry["searxng_health"], "tool": _BoomTool()}
    monkeypatch.setattr(lite_agent_tools, "get_registry", lambda: registry)

    out = ch.lite_agent("is search up?")
    assert out["ok"] is True  # we still produce a reply via the formatter
    assert "unreachable" in out["reply"].lower() or "search" in out["reply"].lower()


# --- 7. classify_intent_llm exposes new labels (parse path) --------------
def test_classifier_parses_query_tool_label() -> None:
    from workers.conversation_handler import _LABEL_RE
    assert _LABEL_RE.findall("the answer is QUERY_TOOL") == ["QUERY_TOOL"]
    assert _LABEL_RE.findall("QUERY_INLINE") == ["QUERY_INLINE"]
    # Bare QUERY still matches for backwards-compat
    assert _LABEL_RE.findall("just a QUERY") == ["QUERY"]


# --- 8. route_message: lite_agent route → one tool, no enqueue -----------
def test_route_message_query_tool_uses_lite_agent(monkeypatch) -> None:
    from workers import conversation_handler as ch
    from workers import llm_router

    monkeypatch.setattr(
        llm_router, "route_llm",
        lambda msg: {"route": "lite_agent", "tier": None, "recon_mode": False},
    )

    captured: dict[str, Any] = {}

    def fake_lite(message):
        captured["msg"] = message
        return {"ok": True, "tool": "searxng_search", "reply": "It's sunny in Pasco."}

    monkeypatch.setattr(ch, "lite_agent", fake_lite)

    # Make sure NOTHING gets enqueued.
    def must_not_enqueue(*a, **kw):
        pytest.fail("QUERY_TOOL must not enqueue when lite_agent succeeds")

    monkeypatch.setattr(ch.task_queue, "enqueue", must_not_enqueue)

    result = ch.route_message("what's the weather in Pasco")
    assert result["kind"] == "query_tool"
    assert "sunny" in result["reply"].lower()
    assert result["meta"]["tool"] == "searxng_search"
    assert captured["msg"] == "what's the weather in Pasco"


# --- 9. route_message: lite_agent miss falls through to TASK -------------
def test_route_message_query_tool_falls_through_on_miss(monkeypatch) -> None:
    from workers import conversation_handler as ch
    from workers import llm_router

    monkeypatch.setattr(
        llm_router, "route_llm",
        lambda msg: {"route": "lite_agent", "tier": None, "recon_mode": False},
    )

    monkeypatch.setattr(ch, "lite_agent",
                        lambda m: {"ok": False, "reason": "picker chose _none"})

    enqueued: list[str] = []
    monkeypatch.setattr(ch.task_queue, "enqueue",
                        lambda input_text, **kw: (enqueued.append(input_text) or "queuedXYZ"))

    msg = "research the top 5 AI agent frameworks please"
    result = ch.route_message(msg)
    assert result["kind"] == "task"
    assert "task_id=queuedXYZ" in result["reply"]
    assert result["meta"]["lite_agent_fallthrough"] == "picker chose _none"
    assert enqueued == [msg], "fall-through must enqueue the message verbatim"


# --- 9b. Hard-override patterns short-circuit straight to QUERY_TOOL -----
@pytest.mark.parametrize("msg", [
    "what's my github auth status",
    "github auth status",
    "am I logged in to github",
    "what's the weather in Pasco WA",
    "weather in seattle",
    "the weather",
    "search the web for langchain",
    "look something up about React",
    "search my notes for BidWatt schema",
    "list my github repos",
    "what repos do I have",
])
def test_hard_override_recognises_single_tool_shapes(msg: str) -> None:
    from workers.conversation_handler import _looks_like_single_fast_tool
    assert _looks_like_single_fast_tool(msg), f"missed: {msg!r}"


@pytest.mark.parametrize("msg", [
    "research the top 5 AI agent frameworks and write me a summary",
    "fix the bug in eod_summary.py",
    "what's 7+8",
    "hi nexus",
    "queue: anything goes here",
])
def test_hard_override_does_not_match_task_or_chat(msg: str) -> None:
    from workers.conversation_handler import _looks_like_single_fast_tool
    assert not _looks_like_single_fast_tool(msg), f"false-positive on: {msg!r}"


def test_route_message_legacy_classifier_not_called(monkeypatch) -> None:
    """Phase 39 — the legacy label classifier (classify_intent_llm) is
    out of the request path entirely; routing decisions come from
    llm_router.route_llm."""
    from workers import conversation_handler as ch
    from workers import llm_router

    classifier_calls = {"n": 0}

    def loud_classifier(msg):
        classifier_calls["n"] += 1
        from workers.conversation_handler import Intent
        return Intent(kind="TASK", raw="TASK")

    monkeypatch.setattr(ch, "classify_intent_llm", loud_classifier)
    monkeypatch.setattr(
        llm_router, "route_llm",
        lambda msg: {"route": "lite_agent", "tier": None, "recon_mode": False},
    )
    monkeypatch.setattr(ch, "lite_agent", lambda m: {
        "ok": True, "tool": "github_auth_status",
        "reply": "Authenticated as CWatt250."
    })

    result = ch.route_message("what's my github auth status")
    assert result["kind"] == "query_tool"
    assert classifier_calls["n"] == 0, "legacy classifier must not run"


# --- 10. STATUS-without-queue-trigger demotes to lite_agent --------------
def test_status_override_demotes_to_query_tool(monkeypatch) -> None:
    """Phrases like 'supabase status' may get route=status from the LLM
    router, but they don't reference Nexus's queue — the deterministic
    `_is_genuine_queue_status` guard demotes them to lite_agent so the
    user gets a fast inline answer rather than an empty queue lookup."""
    from workers import conversation_handler as ch
    from workers import llm_router

    monkeypatch.setattr(
        llm_router, "route_llm",
        lambda msg: {"route": "status", "tier": None, "recon_mode": False},
    )

    captured = {}

    def fake_lite(message):
        captured["msg"] = message
        return {"ok": True, "tool": "web_search",
                "reply": "Supabase status page reports all systems operational."}

    monkeypatch.setattr(ch, "lite_agent", fake_lite)

    result = ch.route_message("what's the supabase status")
    assert result["kind"] == "query_tool"
    assert "operational" in result["reply"].lower()
    assert result["meta"].get("status_demoted_to_lite_agent") is True
    assert captured["msg"] == "what's the supabase status"

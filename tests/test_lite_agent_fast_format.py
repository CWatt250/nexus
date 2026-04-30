"""Tests for the formatter-skip shortcuts in lite_agent (Fix #4 part B).

Verifies:
- SearXNG result lists collapse to "title: snippet" without an LLM call.
- Clean single-sentence tool outputs return verbatim with no formatter.
- ERROR / JSON / oversized outputs still go through the formatter.
"""
from __future__ import annotations

import pytest


# --- 1. _searxng_top_hit parses standard formatted output ----------------
def test_searxng_top_hit_with_search_router_header() -> None:
    from workers.conversation_handler import _searxng_top_hit

    raw = (
        "[search:searxng]\n"
        "- [google] Pasco WA Forecast | weather.com\n"
        "  https://weather.com/weather/today/l/Pasco+Washington\n"
        "  Today: sunny with a high of 72F.\n"
        "- [bing] AccuWeather Pasco WA\n"
        "  https://accuweather.com/x\n"
        "  Detailed 10-day forecast.\n"
    )
    out = _searxng_top_hit(raw)
    assert out is not None
    assert out.startswith("Pasco WA Forecast | weather.com:")
    assert "sunny with a high of 72F" in out


def test_searxng_top_hit_without_header() -> None:
    """Direct searxng_search calls don't have the [search:...] header."""
    from workers.conversation_handler import _searxng_top_hit

    raw = (
        "- [duckduckgo] LangGraph Tutorial - Real Python\n"
        "  https://realpython.com/langgraph-python/\n"
        "  Build stateful AI agents with LangGraph.\n"
    )
    out = _searxng_top_hit(raw)
    assert out == "LangGraph Tutorial - Real Python: Build stateful AI agents with LangGraph."


def test_searxng_top_hit_truncates_long_snippet() -> None:
    from workers.conversation_handler import _searxng_top_hit

    long_snippet = "x" * 500
    raw = f"- [g] Title\n  https://x\n  {long_snippet}\n"
    out = _searxng_top_hit(raw)
    assert out is not None
    assert out.endswith("…")
    assert len(out) < 300


def test_searxng_top_hit_returns_none_for_non_search_output() -> None:
    from workers.conversation_handler import _searxng_top_hit
    assert _searxng_top_hit("Authenticated as CWatt250.") is None
    assert _searxng_top_hit("") is None
    assert _searxng_top_hit("   ") is None
    assert _searxng_top_hit("ERROR: rate-limited") is None


# --- 2. _looks_like_clean_output ----------------------------------------
@pytest.mark.parametrize("text", [
    "Authenticated as CWatt250.",
    "It's 15.",
    "Sunny, 72F in Pasco WA today.",
    "Queue is empty.",
    "Memory store has 1024 documents.",
])
def test_looks_like_clean_output_accepts_short_prose(text: str) -> None:
    from workers.conversation_handler import _looks_like_clean_output
    assert _looks_like_clean_output(text), f"rejected: {text!r}"


@pytest.mark.parametrize("text", [
    "",
    "x" * 700,                                   # too long
    '{"results": [{"title": "x"}]}',              # JSON
    "[item1, item2]",                             # list
    "ERROR: something blew up",
    "(no clean answer extracted)",
    "Add BRAVE_SEARCH_API_KEY to .env",
    "Authenticated as CWatt250\n  scopes: ...\n  rate limit: ...\n  expires"  # no terminator
])
def test_looks_like_clean_output_rejects(text: str) -> None:
    from workers.conversation_handler import _looks_like_clean_output
    assert not _looks_like_clean_output(text), f"accepted: {text!r}"


# --- 3. lite_agent uses search shortcut for SearXNG tool -----------------
def test_lite_agent_uses_search_shortcut_for_searxng(monkeypatch) -> None:
    from workers import conversation_handler as ch
    from tools import lite_agent_tools

    # Picker chooses searxng_search.
    seq = iter([
        '{"tool": "searxng_search", "args": {"query": "weather pasco"}}',
        # If the formatter is called, we want the test to fail loudly.
        "FORMATTER MUST NOT RUN FOR CLEAN SEARCH OUTPUT",
    ])

    def fake_chat(messages, *, timeout, num_predict=250, fmt=None):
        return next(seq)

    monkeypatch.setattr(ch, "_ollama_chat", fake_chat)

    class _StubTool:
        def invoke(self, args):
            return (
                "- [google] Pasco WA Forecast | weather.com\n"
                "  https://weather.com/foo\n"
                "  Sunny, high of 72F.\n"
            )

    registry = dict(lite_agent_tools.get_registry())
    registry["searxng_search"] = {**registry["searxng_search"], "tool": _StubTool()}
    monkeypatch.setattr(lite_agent_tools, "get_registry", lambda: registry)

    out = ch.lite_agent("what's the weather in Pasco WA")
    assert out["ok"] is True
    assert out["fast_format"] == "search_top_hit"
    assert "Sunny" in out["reply"]
    # The formatter must NOT have run.
    assert "FORMATTER" not in out["reply"]


# --- 4. lite_agent uses clean-output shortcut for github_auth_status -----
def test_lite_agent_uses_clean_output_shortcut(monkeypatch) -> None:
    from workers import conversation_handler as ch
    from tools import lite_agent_tools

    seq = iter([
        '{"tool": "github_auth_status", "args": {}}',
        "FORMATTER MUST NOT RUN FOR CLEAN OUTPUT",
    ])

    def fake_chat(messages, *, timeout, num_predict=250, fmt=None):
        return next(seq)

    monkeypatch.setattr(ch, "_ollama_chat", fake_chat)

    class _StubTool:
        def invoke(self, args):
            return "Authenticated as CWatt250 with fine-grained PAT, expires 2027-04-29."

    registry = dict(lite_agent_tools.get_registry())
    registry["github_auth_status"] = {**registry["github_auth_status"], "tool": _StubTool()}
    monkeypatch.setattr(lite_agent_tools, "get_registry", lambda: registry)

    out = ch.lite_agent("what's my github auth status")
    assert out["ok"] is True
    assert out["fast_format"] == "clean_output"
    assert "CWatt250" in out["reply"]
    assert "FORMATTER" not in out["reply"]


# --- 5. lite_agent still calls formatter when output isn't clean ----------
def test_lite_agent_still_formats_messy_output(monkeypatch) -> None:
    from workers import conversation_handler as ch
    from tools import lite_agent_tools

    seq = iter([
        '{"tool": "memory_search", "args": {"query_text": "BidWatt"}}',
        "Found 3 memory snippets about BidWatt schema migrations.",
    ])

    def fake_chat(messages, *, timeout, num_predict=250, fmt=None):
        return next(seq)

    monkeypatch.setattr(ch, "_ollama_chat", fake_chat)

    class _StubTool:
        def invoke(self, args):
            # Ugly multi-line dump, no terminator → not "clean".
            return (
                '{"hits":[{"id":"a","text":"schema v1"},'
                '{"id":"b","text":"schema v2 introduces nullable"}]}'
            )

    registry = dict(lite_agent_tools.get_registry())
    registry["memory_search"] = {**registry["memory_search"], "tool": _StubTool()}
    monkeypatch.setattr(lite_agent_tools, "get_registry", lambda: registry)

    out = ch.lite_agent("search my notes for BidWatt schema")
    assert out["ok"] is True
    # Formatter ran → reply is the formatter's output, not the JSON dump.
    assert "Found 3" in out["reply"]
    assert "fast_format" not in out  # explicit absence — formatter path used

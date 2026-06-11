"""Tests for intent latency telemetry + the /metrics/intent_latency endpoint."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


# --- 1. _record_intent_latency writes one line per call ------------------
def test_record_intent_latency_appends(tmp_path, monkeypatch) -> None:
    from workers import conversation_handler as ch

    log_path = tmp_path / "intent_latencies.jsonl"
    monkeypatch.setattr(ch, "_INTENT_LATENCIES", log_path)

    ch._record_intent_latency("query_inline", 1.42)
    ch._record_intent_latency("query_tool", 3.14, fast_format="search_top_hit", tool="web_search")
    ch._record_intent_latency("task", 4.0, tool=None)

    lines = [json.loads(l) for l in log_path.read_text().strip().splitlines()]
    assert len(lines) == 3
    assert lines[0]["intent"] == "query_inline"
    assert lines[0]["elapsed_s"] == 1.42
    assert lines[1]["fast_format"] == "search_top_hit"
    assert lines[1]["tool"] == "web_search"


# --- 2. route_message wrapper records on every return -------------------
def test_route_message_records_latency_on_each_call(monkeypatch, tmp_path) -> None:
    from workers import conversation_handler as ch

    log_path = tmp_path / "intent_latencies.jsonl"
    monkeypatch.setattr(ch, "_INTENT_LATENCIES", log_path)

    monkeypatch.setattr(ch, "_route_message_inner",
                        lambda msg, **kw: {"kind": "task", "reply": "On it.", "meta": {"task_id": "abc"}})
    out = ch.route_message("research everything please")
    assert out["kind"] == "task"

    lines = [json.loads(l) for l in log_path.read_text().strip().splitlines()]
    assert len(lines) == 1
    assert lines[0]["intent"] == "task"
    assert lines[0]["elapsed_s"] >= 0.0


# --- 3. fast_format propagates from meta into the log -------------------
def test_route_message_records_fast_format(monkeypatch, tmp_path) -> None:
    from workers import conversation_handler as ch

    log_path = tmp_path / "intent_latencies.jsonl"
    monkeypatch.setattr(ch, "_INTENT_LATENCIES", log_path)

    monkeypatch.setattr(
        ch, "_route_message_inner",
        lambda msg, **kw: {
            "kind": "query_tool",
            "reply": "...",
            "meta": {"tool": "searxng_search", "fast_format": "search_top_hit"},
        },
    )
    ch.route_message("search foo")
    rec = json.loads(log_path.read_text().strip())
    assert rec["intent"] == "query_tool"
    assert rec["fast_format"] == "search_top_hit"
    assert rec["tool"] == "searxng_search"


# --- 4. /metrics/intent_latency aggregates correctly --------------------
def test_metrics_intent_latency_endpoint(tmp_path, monkeypatch) -> None:
    """Drive the FastAPI handler directly with a fixture log file."""
    import nexus_api

    home = tmp_path / "home"
    log_dir = home / "AI_Agent" / "memory"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "intent_latencies.jsonl"

    now = datetime.now(timezone.utc)
    rows = [
        # Within window
        {"ts": (now - timedelta(hours=1)).isoformat(timespec="seconds"),
         "intent": "query_inline", "elapsed_s": 1.5},
        {"ts": (now - timedelta(hours=2)).isoformat(timespec="seconds"),
         "intent": "query_inline", "elapsed_s": 2.0},
        {"ts": (now - timedelta(hours=3)).isoformat(timespec="seconds"),
         "intent": "query_tool",   "elapsed_s": 4.0, "fast_format": "search_top_hit"},
        {"ts": (now - timedelta(hours=12)).isoformat(timespec="seconds"),
         "intent": "task",         "elapsed_s": 5.0},
        # Outside window
        {"ts": (now - timedelta(hours=30)).isoformat(timespec="seconds"),
         "intent": "query_inline", "elapsed_s": 99.0},
    ]
    log_path.write_text("\n".join(json.dumps(r) for r in rows))

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home), raising=False)

    import asyncio
    out = asyncio.run(nexus_api.intent_latency(hours=24))
    assert out["window_hours"] == 24
    assert out["total"] == 4
    by = out["by_intent"]
    assert by["query_inline"]["n"] == 2
    assert by["query_inline"]["mean"] == 1.75
    assert by["query_tool"]["n"] == 1
    assert by["task"]["n"] == 1
    assert out["fast_format"] == {"search_top_hit": 1}


def test_metrics_endpoint_handles_missing_file(tmp_path, monkeypatch) -> None:
    import nexus_api

    home = tmp_path / "empty"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home), raising=False)

    import asyncio
    out = asyncio.run(nexus_api.intent_latency(hours=24))
    assert out["total"] == 0
    assert out["by_intent"] == {}
    assert out["fast_format"] == {}


# --- 5. /metrics/quick_chat_cleanliness aggregates correctly ------------
def test_cleanliness_endpoint(tmp_path, monkeypatch) -> None:
    import nexus_api
    from datetime import datetime, timedelta, timezone

    home = tmp_path / "home"
    log_dir = home / "AI_Agent" / "memory"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "quick_chat_cleanliness.jsonl"

    now = datetime.now(timezone.utc)
    rows = [
        {"ts": (now - timedelta(hours=1)).isoformat(timespec="seconds"),
         "model": "qwen3:4b", "elapsed_s": 0.4, "clean": True,  "fallback_used": False},
        {"ts": (now - timedelta(hours=2)).isoformat(timespec="seconds"),
         "model": "qwen3:4b", "elapsed_s": 0.5, "clean": True,  "fallback_used": False},
        {"ts": (now - timedelta(hours=3)).isoformat(timespec="seconds"),
         "model": "qwen3.6",  "elapsed_s": 7.2, "clean": True,  "fallback_used": True,
         "leak_kind": "thinking"},
        {"ts": (now - timedelta(hours=4)).isoformat(timespec="seconds"),
         "model": "qwen3:4b", "elapsed_s": 0.5, "clean": False, "fallback_used": True,
         "leak_kind": "denial"},
        # outside window
        {"ts": (now - timedelta(hours=30)).isoformat(timespec="seconds"),
         "model": "qwen3:4b", "elapsed_s": 0.4, "clean": True,  "fallback_used": False},
    ]
    log_path.write_text("\n".join(json.dumps(r) for r in rows))

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home), raising=False)

    import asyncio
    out = asyncio.run(nexus_api.quick_chat_cleanliness(hours=24))
    assert out["total"] == 4
    assert out["clean"] == 3
    assert out["leaked"] == 1
    assert out["clean_rate"] == 0.75
    assert out["fallback_used"] == 2
    assert out["leak_breakdown"] == {"thinking": 1, "denial": 1}

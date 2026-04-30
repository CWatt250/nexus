"""Tests for the qwen3:4b → qwen3.6 denial fallback in quick_chat (Fix #4 A).

The Ollama HTTP layer is monkey-patched throughout — these run offline.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _patch_ollama(monkeypatch, replies_by_model):
    """Stub `_ollama_quick_chat` to return canned replies per model id.
    `replies_by_model` is a dict {model_name: reply_text} OR a callable
    (model, message, sys) → str for sequence-aware tests."""
    from workers import conversation_handler as ch

    def fake(model, message, system_prompt):
        if callable(replies_by_model):
            return replies_by_model(model, message, system_prompt)
        return replies_by_model[model]

    monkeypatch.setattr(ch, "_ollama_quick_chat", fake)


# --- 1. Happy path: qwen3:4b answers cleanly, no fallback ----------------
def test_quick_chat_returns_primary_when_no_denial(monkeypatch, tmp_path) -> None:
    from workers import conversation_handler as ch

    monkeypatch.setattr(ch, "_DENIAL_LOG", tmp_path / "denials.jsonl")
    _patch_ollama(monkeypatch, {"qwen3:4b": "It's 15."})

    out = ch.quick_chat("what's 7+8")
    assert out == "It's 15."
    assert not (tmp_path / "denials.jsonl").exists()


# --- 2. Primary denies → fallback to qwen3.6, log entry written -----------
def test_quick_chat_falls_back_on_denial(monkeypatch, tmp_path) -> None:
    from workers import conversation_handler as ch

    log_path = tmp_path / "denials.jsonl"
    monkeypatch.setattr(ch, "_DENIAL_LOG", log_path)
    monkeypatch.setattr(ch, "_DENIAL_LAST_ALERT", tmp_path / "last_alert")
    monkeypatch.setattr(ch, "_maybe_alert_telegram", lambda n: None)

    _patch_ollama(monkeypatch, {
        "qwen3:4b": "I can't browse the web for current weather.",
        "qwen3.6":  "Sunny, 72F in Pasco, WA today.",
    })

    out = ch.quick_chat("what's the weather in Pasco WA")
    assert "Sunny" in out
    assert log_path.exists()
    entry = json.loads(log_path.read_text().strip().splitlines()[-1])
    assert entry["model"] == "qwen3:4b"
    assert "weather" in entry["msg"]


# --- 3. Both deny → return primary (don't make things worse) -------------
def test_quick_chat_returns_primary_if_both_deny(monkeypatch, tmp_path) -> None:
    from workers import conversation_handler as ch

    monkeypatch.setattr(ch, "_DENIAL_LOG", tmp_path / "denials.jsonl")
    monkeypatch.setattr(ch, "_DENIAL_LAST_ALERT", tmp_path / "last_alert")
    monkeypatch.setattr(ch, "_maybe_alert_telegram", lambda n: None)

    _patch_ollama(monkeypatch, {
        "qwen3:4b": "I can't help with that.",
        "qwen3.6":  "I cannot browse the web.",
    })

    out = ch.quick_chat("anything")
    assert "can't help" in out  # primary returned, not "cannot browse"


# --- 4. Fallback model raises → return primary --------------------------
def test_quick_chat_handles_fallback_exception(monkeypatch, tmp_path) -> None:
    from workers import conversation_handler as ch

    monkeypatch.setattr(ch, "_DENIAL_LOG", tmp_path / "denials.jsonl")
    monkeypatch.setattr(ch, "_DENIAL_LAST_ALERT", tmp_path / "last_alert")
    monkeypatch.setattr(ch, "_maybe_alert_telegram", lambda n: None)

    def fake(model, msg, sys):
        if model == "qwen3:4b":
            return "I can't access the internet."
        raise RuntimeError("ollama down for fallback")

    monkeypatch.setattr(ch, "_ollama_quick_chat", fake)

    out = ch.quick_chat("what's the news")
    assert "can't access" in out  # graceful degradation


# --- 5. Denial counter rolls over 24h ------------------------------------
def test_denials_in_last_24h_counts_only_recent(monkeypatch, tmp_path) -> None:
    from workers import conversation_handler as ch
    from datetime import datetime, timedelta, timezone

    log_path = tmp_path / "denials.jsonl"
    monkeypatch.setattr(ch, "_DENIAL_LOG", log_path)

    now = datetime.now(timezone.utc)
    rows = [
        {"ts": (now - timedelta(hours=2)).isoformat(timespec="seconds"),  "msg": "fresh1", "model": "qwen3:4b"},
        {"ts": (now - timedelta(hours=10)).isoformat(timespec="seconds"), "msg": "fresh2", "model": "qwen3:4b"},
        {"ts": (now - timedelta(hours=23)).isoformat(timespec="seconds"), "msg": "fresh3", "model": "qwen3:4b"},
        {"ts": (now - timedelta(hours=30)).isoformat(timespec="seconds"), "msg": "stale1", "model": "qwen3:4b"},
        {"ts": (now - timedelta(days=7)).isoformat(timespec="seconds"),   "msg": "stale2", "model": "qwen3:4b"},
    ]
    log_path.write_text("\n".join(json.dumps(r) for r in rows))

    assert ch._denials_in_last_24h() == 3


# --- 6. Telegram alert fires above threshold + respects cooldown ---------
def test_telegram_alert_fires_above_threshold(monkeypatch, tmp_path) -> None:
    from workers import conversation_handler as ch

    monkeypatch.setattr(ch, "_DENIAL_LAST_ALERT", tmp_path / "last_alert")

    sent: list[str] = []

    class _StubTool:
        def invoke(self, args):
            sent.append(args["message"])

    # Patch the dynamic import inside _maybe_alert_telegram.
    fake_module = type("M", (), {"telegram_notify": _StubTool()})()
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "tools.telegram_tool", fake_module)

    ch._maybe_alert_telegram(7)
    assert len(sent) == 1
    assert "denial spike" in sent[0]

    # Second call within cooldown — no new send.
    ch._maybe_alert_telegram(7)
    assert len(sent) == 1


def test_telegram_alert_skipped_below_threshold(monkeypatch, tmp_path) -> None:
    from workers import conversation_handler as ch

    monkeypatch.setattr(ch, "_DENIAL_LAST_ALERT", tmp_path / "last_alert")
    sent = []

    class _StubTool:
        def invoke(self, args):
            sent.append(args["message"])

    import sys as _sys
    fake_module = type("M", (), {"telegram_notify": _StubTool()})()
    monkeypatch.setitem(_sys.modules, "tools.telegram_tool", fake_module)

    ch._maybe_alert_telegram(2)
    assert sent == []


# --- 8. Thinking-leak detection -----------------------------------------
@pytest.mark.parametrize("text,expected_leak", [
    # The literal failing example from this morning's bug report.
    (
        "I need to banter back lightly as per the rules. Keep it 2-3 sentences, "
        "conversational tone. No preamble, no tags. Since they're greeting me, "
        "I'll respond with a friendly, quick reply that matches their vibe.",
        True,
    ),
    # Common qwen3:4b leak shapes
    ("Okay, the user asked what's 7+8.", True),
    ("Let me count: 1, 2, 3...", True),
    ("Make sure to mention the timezone.", True),
    ("Double-checking the format.", True),
    ("Maybe something like 'You're welcome'.", True),
    ("As Nexus, I should reply tersely.", True),
    ("We are in 2026, Thursday morning.", True),
    # Clean answers — must NOT trip the detector
    ("Hey. WattBott still running smooth. What's on your mind?", False),
    ("15.", False),
    ("Transmission Control Protocol.", False),
    ("It's 8:30 AM.", False),
    ("Why don't scientists trust atoms? Because they make up everything!", False),
])
def test_looks_like_thinking_leak(text: str, expected_leak: bool) -> None:
    from workers.conversation_handler import looks_like_thinking_leak
    assert looks_like_thinking_leak(text) is expected_leak, f"misclassified: {text!r}"


# --- 9. </think> separator stripping ------------------------------------
def test_split_unbalanced_close_think() -> None:
    from workers.conversation_handler import _split_unbalanced_close_think
    raw = (
        "Okay, user said hi. Let me craft a warm reply. *checks notes*\n"
        "</think>\n"
        "Hey. WattBott still running smooth. What's on your mind?"
    )
    out = _split_unbalanced_close_think(raw)
    assert out.startswith("Hey.")
    assert "Let me craft" not in out
    assert "</think>" not in out


def test_split_unbalanced_close_think_passes_through_when_no_close() -> None:
    from workers.conversation_handler import _split_unbalanced_close_think
    raw = "It's 8:30 AM."
    assert _split_unbalanced_close_think(raw) == raw


# --- 10. quick_chat falls back on thinking_leak (not just denial) -------
def test_quick_chat_falls_back_on_thinking_leak(monkeypatch, tmp_path) -> None:
    """The actual bug: qwen3:4b emits CoT prose without any denial
    keywords. The new fallback trigger is `looks_like_thinking_leak`."""
    from workers import conversation_handler as ch

    monkeypatch.setattr(ch, "_DENIAL_LOG", tmp_path / "denials.jsonl")
    monkeypatch.setattr(ch, "_DENIAL_LAST_ALERT", tmp_path / "last_alert")
    monkeypatch.setattr(ch, "_CLEANLINESS_LOG", tmp_path / "cleanliness.jsonl")
    monkeypatch.setattr(ch, "_maybe_alert_telegram", lambda n: None)

    def fake(model, msg, sys):
        if model == "qwen3:4b":
            return ("I need to banter back lightly as per the rules. "
                    "Keep it 2-3 sentences. Since they're greeting me, "
                    "I'll respond with a friendly reply.")
        return "Hey, what's up. WattBott's running smooth."

    monkeypatch.setattr(ch, "_ollama_quick_chat", fake)

    out = ch.quick_chat("Hey what's up brotha")
    assert "running smooth" in out
    assert "I need to banter" not in out
    # Denial log should record this as a "thinking" leak, not a "denial".
    denials = (tmp_path / "denials.jsonl").read_text().strip().splitlines()
    assert len(denials) == 1
    entry = json.loads(denials[0])
    assert entry["kind"] == "thinking"


def test_quick_chat_records_cleanliness_when_clean(monkeypatch, tmp_path) -> None:
    from workers import conversation_handler as ch

    monkeypatch.setattr(ch, "_CLEANLINESS_LOG", tmp_path / "clean.jsonl")
    monkeypatch.setattr(ch, "_ollama_quick_chat",
                        lambda m, msg, s: "It's 15.")

    out = ch.quick_chat("what's 7+8")
    assert out == "It's 15."

    rec = json.loads((tmp_path / "clean.jsonl").read_text().strip())
    assert rec["clean"] is True
    assert rec["fallback_used"] is False
    assert rec["model"] == "qwen3:4b"

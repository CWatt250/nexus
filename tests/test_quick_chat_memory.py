"""Phase 38 — quick_chat conversation buffer.

Verifies the persistent telegram_chats store + the conversation-history
plumbing into quick_chat. All DB writes go to tmp_path; the DeepSeek
and Ollama paths are stubbed so these run offline.
"""
from __future__ import annotations

import time

import pytest


# ── core/telegram_chats.py — pure store ──────────────────────────────

def test_fetch_returns_inserted_turns_in_chronological_order(tmp_path) -> None:
    from core import telegram_chats as tcs

    db = tmp_path / "chats.db"
    base_ts = int(time.time()) - 60
    pairs = [
        ("user", "hi"),
        ("assistant", "hey"),
        ("user", "what's up"),
        ("assistant", "not much"),
        ("user", "cool"),
    ]
    for offset, (role, content) in enumerate(pairs):
        tcs.write_turn(123, role, content, db_path=db, ts=base_ts + offset)

    history = tcs.fetch_recent_turns(123, db_path=db)
    assert history == [{"role": r, "content": c} for r, c in pairs]


def test_fetch_caps_at_max_turns(tmp_path) -> None:
    from core import telegram_chats as tcs

    db = tmp_path / "chats.db"
    base_ts = int(time.time()) - 600
    for i in range(25):
        tcs.write_turn(7, "user", f"msg {i}", db_path=db, ts=base_ts + i)

    history = tcs.fetch_recent_turns(7, max_turns=20, db_path=db)
    assert len(history) == 20
    # Most recent 20 = msgs 5..24, in chronological order
    assert history[0]["content"] == "msg 5"
    assert history[-1]["content"] == "msg 24"


def test_fetch_excludes_turns_older_than_max_age(tmp_path) -> None:
    from core import telegram_chats as tcs

    db = tmp_path / "chats.db"
    now = int(time.time())
    # Stale: 3h old — should be excluded with max_age_hours=2
    tcs.write_turn(42, "user", "stale", db_path=db, ts=now - 3 * 3600)
    # Fresh: 30min old — included
    tcs.write_turn(42, "user", "fresh", db_path=db, ts=now - 30 * 60)

    history = tcs.fetch_recent_turns(42, max_age_hours=2, db_path=db, now=now)
    assert len(history) == 1
    assert history[0]["content"] == "fresh"


def test_roundtrip_matches_openai_schema(tmp_path) -> None:
    from core import telegram_chats as tcs

    db = tmp_path / "chats.db"
    tcs.write_turn(99, "user", "my favorite color is blue", db_path=db)
    tcs.write_turn(99, "assistant", "got it — blue.", db_path=db)

    history = tcs.fetch_recent_turns(99, db_path=db)
    assert history == [
        {"role": "user", "content": "my favorite color is blue"},
        {"role": "assistant", "content": "got it — blue."},
    ]
    # Keys are exactly the OpenAI shape — no extras that would confuse
    # the DeepSeek API.
    for turn in history:
        assert set(turn.keys()) == {"role", "content"}


def test_per_chat_isolation(tmp_path) -> None:
    from core import telegram_chats as tcs

    db = tmp_path / "chats.db"
    tcs.write_turn(1, "user", "from chat 1", db_path=db)
    tcs.write_turn(2, "user", "from chat 2", db_path=db)

    assert tcs.fetch_recent_turns(1, db_path=db) == [
        {"role": "user", "content": "from chat 1"}
    ]
    assert tcs.fetch_recent_turns(2, db_path=db) == [
        {"role": "user", "content": "from chat 2"}
    ]


# ── _trim_history_to_budget — pure logic ─────────────────────────────

def test_trim_drops_oldest_turns_when_over_budget() -> None:
    from workers.conversation_handler import _trim_history_to_budget

    # 100-token context window, 80% cap = 80 tokens budget.
    # _approx_tokens = len // 4. system=40 chars→10 toks, message=40→10
    # toks → fixed=20, remaining=60 toks = 240 chars worth of history.
    system = "x" * 40
    message = "y" * 40
    # 6 turns × 100 chars each → 600 chars → ~150 tokens. Budget allows
    # ~60 tokens → only the most recent ~2 turns survive (240 chars).
    history = [{"role": "user", "content": "a" * 100}] * 6
    trimmed = _trim_history_to_budget(
        system, history, message,
        context_window_tokens=100, max_context_pct=80,
    )
    assert len(trimmed) <= 3  # generous upper bound; drops oldest first
    assert len(trimmed) < len(history)


def test_trim_keeps_full_history_when_under_budget() -> None:
    from workers.conversation_handler import _trim_history_to_budget

    system = "system prompt"
    message = "current question"
    history = [
        {"role": "user", "content": "earlier turn 1"},
        {"role": "assistant", "content": "earlier reply 1"},
        {"role": "user", "content": "earlier turn 2"},
    ]
    # 64K window @ 80% leaves ~51K tokens — these tiny turns all fit.
    trimmed = _trim_history_to_budget(
        system, history, message,
        context_window_tokens=64000, max_context_pct=80,
    )
    assert trimmed == history


def test_trim_returns_empty_when_system_plus_message_already_over_budget() -> None:
    from workers.conversation_handler import _trim_history_to_budget

    system = "x" * 1000
    message = "y" * 1000
    history = [{"role": "user", "content": "z" * 100}]
    # 100 tokens window @ 80% = 80 budget. System+msg alone = ~500 toks.
    trimmed = _trim_history_to_budget(
        system, history, message,
        context_window_tokens=100, max_context_pct=80,
    )
    assert trimmed == []


# ── quick_chat integration — chat_id=None stays stateless ────────────

def _patch_deepseek(monkeypatch, *, captured=None):
    """Stub the DeepSeek path so quick_chat returns deterministically.
    When `captured` is a list, the kwargs of each call are appended to
    it so the test can inspect what was passed."""
    from workers import quick_chat_providers as qcp

    def fake_deepseek(message, system_prompt, **kwargs):
        if captured is not None:
            captured.append({"message": message, "kwargs": kwargs})
        return "stub reply", {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(qcp, "deepseek_chat", fake_deepseek)
    monkeypatch.setattr(qcp, "get_configured_provider", lambda: "deepseek")


def test_quick_chat_with_chat_id_none_is_stateless(monkeypatch, tmp_path) -> None:
    """No chat_id → no history fetch, no DB hit. Existing pre-Phase-38
    behavior preserved for the dashboard /chat path and other callers."""
    from workers import conversation_handler as ch

    # Spy on the history builder. If chat_id is None, it must NOT be called.
    calls = []
    monkeypatch.setattr(
        ch, "_build_quick_chat_history",
        lambda chat_id, system, message: (calls.append(chat_id), [])[1],
    )

    captured: list[dict] = []
    _patch_deepseek(monkeypatch, captured=captured)

    out = ch.quick_chat("hello")
    assert out == "stub reply"
    assert calls == []  # builder never invoked
    # DeepSeek receives no history (None or absent).
    assert not captured[0]["kwargs"].get("history")


def test_quick_chat_with_chat_id_passes_history_to_deepseek(monkeypatch, tmp_path) -> None:
    """With chat_id, the prior turns from the store are passed through
    as the `history` kwarg to deepseek_chat."""
    from core import telegram_chats as tcs
    from workers import conversation_handler as ch

    db = tmp_path / "chats.db"
    # Pre-populate two turns in the buffer.
    tcs.write_turn(555, "user", "my favorite color is blue", db_path=db)
    tcs.write_turn(555, "assistant", "got it — blue", db_path=db)

    # Point the module's config loader at the tmp DB.
    monkeypatch.setattr(
        ch, "_load_quick_chat_memory_config",
        lambda: {**ch._MEMORY_DEFAULTS, "db_path": str(db)},
    )

    captured: list[dict] = []
    _patch_deepseek(monkeypatch, captured=captured)

    out = ch.quick_chat("what's my favorite color?", chat_id=555)
    assert out == "stub reply"
    history = captured[0]["kwargs"].get("history")
    assert history == [
        {"role": "user", "content": "my favorite color is blue"},
        {"role": "assistant", "content": "got it — blue"},
    ]

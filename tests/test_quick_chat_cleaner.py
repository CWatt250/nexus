"""Tests for the qwen3:4b reasoning-preamble stripper."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("inp,expected_substr,not_in", [
    # Standard "Okay, the user asked..." three-sentence preamble
    (
        'Okay, the user asked "what\'s 7+8". That\'s a simple math question. Let me think. 7 plus 8 equals 15.',
        "7 plus 8 equals 15",
        "the user asked",
    ),
    # "We are given..." style
    (
        "We are given a straightforward question. TCP stands for Transmission Control Protocol.",
        "TCP stands for Transmission Control Protocol",
        "We are given",
    ),
    # "Hmm" preamble
    (
        "Hmm, this is interesting. The answer is 42.",
        "The answer is 42",
        "Hmm",
    ),
    # "Let me check the current time" before the actual answer
    (
        "Okay, the user asked. Let me check the current time. The current date is 2026-04-29.",
        "current date is 2026-04-29",
        "the user asked",
    ),
    # Already clean — should pass through unchanged
    ("15.", "15.", "Okay"),
    ("7 + 8 = 15.", "7 + 8 = 15", "Okay"),
    # Conversational answer that happens to begin with "I" — should NOT
    # be stripped (only "I should/need/will/can [reply/answer/respond]"
    # patterns get dropped, not generic "I" sentences).
    ("I am Nexus, your local agent.", "I am Nexus", "stripped"),
])
def test_strip_reasoning_preamble(inp: str, expected_substr: str, not_in: str) -> None:
    from workers.conversation_handler import _strip_reasoning_preamble
    out = _strip_reasoning_preamble(inp)
    assert expected_substr in out, f"expected {expected_substr!r} in {out!r}"
    assert not_in not in out, f"unexpected {not_in!r} in {out!r}"


def test_strip_reasoning_preamble_caps_at_6_sentences() -> None:
    """Don't infinite-loop or strip the entire response."""
    from workers.conversation_handler import _strip_reasoning_preamble
    s = "Okay, this. " * 50 + "Final answer."
    out = _strip_reasoning_preamble(s)
    # We cap at 6 strip iterations; some preambles will remain. The
    # safety property is just "doesn't hang and doesn't return empty".
    assert out
    assert len(out) > 0


def test_strip_reasoning_preamble_handles_ellipses() -> None:
    """qwen3:4b uses '...' instead of '.' between sentences."""
    from workers.conversation_handler import _strip_reasoning_preamble
    s = "Let me think... 7 plus 8 equals 15."
    out = _strip_reasoning_preamble(s)
    assert "7 plus 8 equals 15" in out
    assert "Let me think" not in out


def test_clean_quick_chat_strips_think_tags_and_preamble() -> None:
    """End-to-end: <think> tags + reasoning preamble + clean answer."""
    from workers.conversation_handler import _clean_quick_chat
    raw = (
        "<think>internal CoT</think>"
        "Okay, the user asked a simple question. The answer is 42."
    )
    out = _clean_quick_chat(raw)
    assert "<think>" not in out
    assert "the user asked" not in out
    assert "The answer is 42" in out

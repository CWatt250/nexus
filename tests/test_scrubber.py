"""Phase 32.1 regression tests for `_strip_think_final`.

Diagnosis in `cc_logs/cc_2e01e270.log`: the global scrubber handled
matched `<think>...</think>` pairs and open-only `<think>...` (no
closer), but did NOT handle bare orphan `</think>` closers (qwen3:4b
emits CoT prose, then a `</think>` with no opener, then the actual
reply). The orphan-close stripper existed (`_split_unbalanced_close_think`)
but was wired only into `_clean_quick_chat`, so tool-using ReAct,
classifier, and dispatch-result paths leaked the orphan tag.

The fix routes `_strip_think_final` through the orphan-close stripper
first so all four shapes — matched, open-only, orphan close, no tags —
are handled by the one final scrubber that wraps every reply at
`route_message`.
"""
from __future__ import annotations


def test_matched_pair() -> None:
    """Case 1: `<think>X</think>reply` → `reply`. Existing behavior."""
    from workers.conversation_handler import _strip_think_final
    out = _strip_think_final("<think>X</think>reply")
    assert out == "reply", repr(out)


def test_open_only() -> None:
    """Case 2: `<think>X` (truncated, no closer) → empty.

    Open-only is treated as runaway reasoning that ate the rest of the
    output — there is no clean reply to recover. The scrubber must not
    leak the `<think>` tag or the reasoning prose."""
    from workers.conversation_handler import _strip_think_final
    out = _strip_think_final("<think>X reply")
    assert "<think>" not in out
    assert "X reply" not in out
    assert out == "", repr(out)


def test_orphan_closer() -> None:
    """Case 3 (THE FIX): `X</think>reply` → `reply`.

    Before Phase 32.1 this leaked through `_strip_think_final` because
    only `_clean_quick_chat` knew how to split on a bare orphan closer."""
    from workers.conversation_handler import _strip_think_final
    out = _strip_think_final("X</think>reply")
    assert out == "reply", repr(out)


def test_no_tags() -> None:
    """Case 4: `reply` → `reply`. Pass-through, no false positives."""
    from workers.conversation_handler import _strip_think_final
    out = _strip_think_final("reply")
    assert out == "reply", repr(out)

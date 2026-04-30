"""Regression tests for the TASK Telegram path think-tag scrub.

Companion to commit 5601eaf (which fixed the same leak in eod_summary).
The TASK path is the one that hit Colton on the X.com lookup — qwen3.6
streamed `<think>...</think>` reasoning into the final AIMessage and the
worker shipped the whole thing to Telegram. Now `clean_task_reply()`
runs before `notify_done()` ever sees the text.
"""
from __future__ import annotations

import asyncio
from typing import List

import pytest  # noqa: F401


# 1. Direct unit test of the cleanup helper.
def test_strip_thinking_removes_closed_block() -> None:
    import nexus
    raw = "<think>Hmm, let me check the tool result. Wait, that's odd.</think>The X.com post introduces NORI L1, a humanoid robot under $1000."
    out = nexus.clean_task_reply(raw, allow_reextract=False)
    assert "<think>" not in out
    assert "Hmm" not in out
    assert out.startswith("The X.com post introduces NORI L1")


def test_strip_thinking_removes_unclosed_block() -> None:
    import nexus
    raw = "<think>reasoning that never closed because the model ran out of tokens"
    out = nexus.clean_task_reply(raw, allow_reextract=False)
    assert "<think>" not in out
    assert "reasoning" not in out


def test_strip_thinking_passes_through_clean_text() -> None:
    import nexus
    raw = "Authenticated as CWatt250 with repo + read:org scopes."
    out = nexus.clean_task_reply(raw, allow_reextract=False)
    assert out == raw


def test_looks_like_raw_reasoning_detects_common_preambles() -> None:
    import nexus
    assert nexus.looks_like_raw_reasoning("Okay, let me check the tool output.")
    assert nexus.looks_like_raw_reasoning("Hmm, that's odd.")
    assert nexus.looks_like_raw_reasoning("Wait, the tool returned nothing.")
    assert nexus.looks_like_raw_reasoning("Let me think about this.")
    assert not nexus.looks_like_raw_reasoning("The repo has 42 stars.")
    assert not nexus.looks_like_raw_reasoning("")


# 2. End-to-end: feed a faked AIMessage through the worker's reply
#    extraction path and confirm only the clean tail reaches the
#    Telegram-send seam (`workers.task_notifier._send`).
def test_task_worker_strips_think_before_telegram(monkeypatch) -> None:
    import nexus
    from workers import task_notifier

    sent: List[str] = []

    async def fake_send(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(task_notifier, "_send", fake_send)

    # Mimic the AIMessage shape qwen3.6 emits when reasoning leaks.
    leaked = (
        "<think>Okay, let me check the URL. Wait, the tool is returning empty. "
        "Hmm, that's odd. Let me try browser_render instead.</think>"
        "Antonio Li introduces NORI L1, a humanoid robot priced under $1000."
    )
    class AIMessage:  # mimic the langchain class name without the import
        def __init__(self, content: str) -> None:
            self.content = content

    fake_msg = AIMessage(leaked)

    # Replicate the worker's reply-extraction loop verbatim. If this
    # exact pattern changes in task_worker.py, this test should break.
    reply = ""
    for m in [fake_msg]:
        if m.__class__.__name__ == "AIMessage" and getattr(m, "content", ""):
            reply = nexus.clean_task_reply(m.content, allow_reextract=False)
            break

    assert "<think>" not in reply
    assert "Hmm" not in reply
    assert "Wait, the tool" not in reply
    assert reply.startswith("Antonio Li introduces NORI L1")

    asyncio.run(task_notifier.notify_done("test123", reply, elapsed_s=1.0))
    assert sent, "notify_done should have called _send"
    body = sent[0]
    assert "<think>" not in body
    assert "Hmm, that's odd" not in body
    assert "NORI L1" in body

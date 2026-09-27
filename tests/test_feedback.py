"""👍/👎 capture → KTO export (tools/telegram_feedback, training/export_feedback)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from tools.telegram_feedback import score

_spec = importlib.util.spec_from_file_location(
    "export_feedback", Path(__file__).resolve().parent.parent / "training" / "export_feedback.py")
export_feedback = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_feedback)
build = export_feedback.build


def turn(uid, prompt, reply, chat=1):
    return {"ts": uid, "chat_id": chat, "user_msg_id": uid, "prompt": prompt, "reply": reply}


def rate(mid, rating, ts, chat=1):
    return {"ts": ts, "chat_id": chat, "message_id": mid, "rating": rating, "emoji": ""}


def test_score():
    assert score(["👍"]) == 1
    assert score(["🔥"]) == 1
    assert score(["👎"]) == -1
    assert score(["👍", "👎"]) == -1      # any thumbs-down wins
    assert score([]) == 0                 # reaction removed
    assert score(["🦄"]) == 0             # unknown emoji is no signal


def test_reaction_maps_to_preceding_turn():
    turns = [turn(10, "hi", "hey!"), turn(20, "weather?", "72F sunny")]
    rows = build(turns, [rate(21, -1, 1), rate(11, 1, 2)])
    by_prompt = {r["prompt"][0]["content"]: r["label"] for r in rows}
    assert by_prompt == {"hi": True, "weather?": False}


def test_chunk_of_long_reply_counts_for_its_turn():
    rows = build([turn(10, "essay", "part1..."), turn(30, "next", "x")], [rate(14, 1, 1)])
    assert [r["prompt"][0]["content"] for r in rows] == ["essay"]


def test_latest_reaction_wins_and_removal_unrates():
    turns = [turn(10, "a", "A"), turn(20, "b", "B")]
    rows = build(turns, [rate(11, 1, 1), rate(11, -1, 2), rate(21, 1, 3), rate(21, 0, 4)])
    assert [(r["prompt"][0]["content"], r["label"]) for r in rows] == [("a", False)]


def test_chats_are_independent_and_orphans_ignored():
    turns = [turn(10, "a", "A", chat=1), turn(10, "b", "B", chat=2)]
    rows = build(turns, [rate(11, 1, 1, chat=2), rate(5, 1, 2, chat=1)])
    assert [r["prompt"][0]["content"] for r in rows] == ["b"]


def test_kto_row_shape():
    (row,) = build([turn(10, "q", "a")], [rate(11, 1, 1)])
    assert row == {"prompt": [{"role": "user", "content": "q"}],
                   "completion": [{"role": "assistant", "content": "a"}], "label": True}

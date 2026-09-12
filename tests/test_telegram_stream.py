"""Telegram progress-bubble streaming tests.

Exercises tools/telegram_listener._stream_quick_chat_reply with a mocked
Telegram bot/update and a fake quick_chat_stream generator — no network,
no Ollama. The reply streams INTO one bubble (edit_message_text) and the
final replaces it; long finals become bubble = chunk 1 + follow-ups.
"""
import asyncio
import re

import pytest

from tools import telegram_listener as tl
from tools import telegram_progress as tp
from workers import conversation_handler as ch


class FakeChat:
    id = 123

    async def send_action(self, action):
        pass


class SentMsg:
    def __init__(self, message_id):
        self.message_id = message_id


class FakeMsg:
    def __init__(self):
        self.message_id = 42
        self.chat = FakeChat()
        self.sent: list[str] = []

    async def reply_text(self, text):
        self.sent.append(text)
        return SentMsg(1000 + len(self.sent))


class FakeBot:
    def __init__(self, fail=False):
        self.edits: list[tuple] = []
        self.deleted: list[int] = []
        self.fail = fail

    async def edit_message_text(self, chat_id, message_id, text):
        if self.fail:
            raise RuntimeError("edit unsupported")
        self.edits.append((chat_id, message_id, text))

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


class FakeUpdate:
    def __init__(self, bot):
        self.message = FakeMsg()
        self.effective_chat = FakeChat()
        self._bot = bot

    def get_bot(self):
        return self._bot


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(tp, "EDIT_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(tp, "CHUNK_SEND_DELAY_S", 0.0)
    monkeypatch.setattr(tp, "TYPING_INTERVAL_S", 0.01)
    monkeypatch.setattr(tl, "CHUNK_SEND_DELAY_S", 0.0)
    # The false-promise guard must never touch the real queue here.
    monkeypatch.setattr(ch, "guard_quick_chat_reply", lambda m, r: None)


def _words(s):
    return re.findall(r"\S+", s)


def test_streams_into_bubble_then_final_replaces_it(monkeypatch):
    def fake_stream(message, chat_id):
        yield {"partial": "Hello."}
        yield {"partial": "Hello. World."}
        yield {"final": "Hello. World."}
    monkeypatch.setattr(ch, "quick_chat_stream", fake_stream)

    bot = FakeBot()
    upd = FakeUpdate(bot)
    out = asyncio.run(tl._stream_quick_chat_reply(upd, "hi", 123))

    assert out == "Hello. World."
    assert upd.message.sent == [tp.PLACEHOLDER]         # ONE bubble sent
    assert bot.edits[-1][1] == 1001                     # edits target the bubble
    assert bot.edits[-1][2] == "Hello. World."          # final replaced it
    assert any(e[2] == "Hello." for e in bot.edits)     # partial animated


def test_edit_failure_degrades_but_still_sends_final(monkeypatch):
    def fake_stream(message, chat_id):
        yield {"partial": "Hel"}
        yield {"partial": "Hello"}
        yield {"final": "Hello."}
    monkeypatch.setattr(ch, "quick_chat_stream", fake_stream)

    bot = FakeBot(fail=True)            # every edit raises
    upd = FakeUpdate(bot)
    out = asyncio.run(tl._stream_quick_chat_reply(upd, "hi", 123))

    assert out == "Hello."
    assert upd.message.sent[-1] == "Hello."   # reply NOT dropped
    assert bot.edits == []                    # no edit succeeded


def test_generation_error_raises_for_caller_fallback(monkeypatch):
    def boom(message, chat_id):
        yield {"partial": "Hel"}
        raise RuntimeError("ollama down")
    monkeypatch.setattr(ch, "quick_chat_stream", boom)

    with pytest.raises(RuntimeError):
        asyncio.run(tl._stream_quick_chat_reply(FakeUpdate(FakeBot()), "hi", 9))


def test_long_final_is_chunked_not_truncated(monkeypatch):
    long = "word " * 2000  # ~10k chars, one line
    def fake_stream(message, chat_id):
        yield {"partial": "word"}
        yield {"final": long}
    monkeypatch.setattr(ch, "quick_chat_stream", fake_stream)

    bot = FakeBot()
    upd = FakeUpdate(bot)
    asyncio.run(tl._stream_quick_chat_reply(upd, "hi", 1))

    first = bot.edits[-1][2]                                # bubble = chunk 1
    rest = upd.message.sent[1:]                             # follow-ups
    assert rest                                             # split across msgs
    assert all(len(c) <= 4096 for c in [first, *rest])      # under hard cap
    assert _words(" ".join([first, *rest])) == _words(long)  # nothing lost


def test_false_promise_final_is_recovered(monkeypatch):
    def fake_stream(message, chat_id):
        yield {"final": "Let me check the process list real quick."}
    monkeypatch.setattr(ch, "quick_chat_stream", fake_stream)
    monkeypatch.setattr(ch, "guard_quick_chat_reply",
                        lambda m, r: {"reply": ch.RECOVERY_REPLY, "task_id": "abc", "why": "false_promise"})

    bot = FakeBot()
    out = asyncio.run(tl._stream_quick_chat_reply(FakeUpdate(bot), "ps aux?", 1))
    assert out == ch.RECOVERY_REPLY
    assert bot.edits[-1][2] == ch.RECOVERY_REPLY


def test_quick_chat_stream_partials_stop_at_sentence_boundary(monkeypatch):
    """Partials are sentence-buffered + scrubbed, never the raw accumulator."""
    toks = ["<think>", "hmm", "</think>", "Sure", ".", " Two", " words", ".", " tail"]

    class FakeClient:
        def __init__(self, host=None, timeout=None):
            pass

        def chat(self, **kw):
            for t in toks:
                yield {"message": {"content": t}}

    monkeypatch.setattr(ch.ollama, "Client", FakeClient)
    monkeypatch.setattr(ch, "_build_chat_context_block", lambda m: "ctx")
    evs = list(ch.quick_chat_stream("hi", None, show_thinking=False))
    partials = [e["partial"] for e in evs if "partial" in e]
    assert partials and all("<think>" not in p and "hmm" not in p for p in partials)
    assert partials[0] == "Sure."
    assert evs[-1]["final"] == "Sure. Two words. tail"

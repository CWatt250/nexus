"""Phase 41 — Telegram live-draft streaming tests (Part B).

Exercises tools/telegram_listener._stream_quick_chat_reply with a mocked
Telegram bot/update and a fake quick_chat_stream generator — no network,
no Ollama.
"""
import asyncio
import re

import pytest

from tools import telegram_listener as tl
from workers import conversation_handler as ch


class FakeMsg:
    def __init__(self):
        self.message_id = 42
        self.sent: list[str] = []

    async def reply_text(self, text):
        self.sent.append(text)


class FakeBot:
    def __init__(self, fail=False):
        self.drafts: list[tuple] = []
        self.fail = fail

    async def send_message_draft(self, chat_id, draft_id, text):
        if self.fail:
            raise RuntimeError("draft unsupported (account API version)")
        self.drafts.append((chat_id, draft_id, text))


class FakeUpdate:
    def __init__(self, bot):
        self.message = FakeMsg()
        self._bot = bot

    def get_bot(self):
        return self._bot


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    # Push a draft on every partial and don't sleep between chunks.
    monkeypatch.setattr(tl, "DRAFT_THROTTLE_S", 0.0)
    monkeypatch.setattr(tl, "CHUNK_SEND_DELAY_S", 0.0)


def _words(s):
    return re.findall(r"\S+", s)


def test_streams_drafts_then_finalizes(monkeypatch):
    def fake_stream(message, chat_id):
        yield {"partial": "Hello"}
        yield {"partial": "Hello world"}
        yield {"final": "Hello world."}
    monkeypatch.setattr(ch, "quick_chat_stream", fake_stream)

    bot = FakeBot()
    upd = FakeUpdate(bot)
    out = asyncio.run(tl._stream_quick_chat_reply(upd, "hi", 123))

    assert out == "Hello world."
    assert upd.message.sent == ["Hello world."]      # finalized real message
    assert len(bot.drafts) >= 1                       # drafts animated
    assert bot.drafts[-1][1] == 42                    # draft_id == message_id


def test_draft_failure_degrades_but_still_sends_final(monkeypatch):
    def fake_stream(message, chat_id):
        yield {"partial": "Hel"}
        yield {"partial": "Hello"}
        yield {"final": "Hello."}
    monkeypatch.setattr(ch, "quick_chat_stream", fake_stream)

    bot = FakeBot(fail=True)            # every draft raises
    upd = FakeUpdate(bot)
    out = asyncio.run(tl._stream_quick_chat_reply(upd, "hi", 123))

    assert out == "Hello."
    assert upd.message.sent == ["Hello."]   # reply NOT dropped
    assert bot.drafts == []                 # no draft succeeded


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

    upd = FakeUpdate(FakeBot())
    asyncio.run(tl._stream_quick_chat_reply(upd, "hi", 1))

    assert len(upd.message.sent) > 1                       # split across msgs
    assert all(len(c) <= 4096 for c in upd.message.sent)   # under hard cap
    assert _words(" ".join(upd.message.sent)) == _words(long)  # nothing lost

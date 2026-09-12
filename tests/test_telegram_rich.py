"""Phase 42 — Telegram Rich Messages (Bot API 10.1) tests."""
import asyncio

import pytest

from tools import telegram_listener as tl
from workers import conversation_handler as ch


class FakeMsg:
    def __init__(self):
        self.message_id = 7
        self.sent: list[str] = []

    async def reply_text(self, text):
        self.sent.append(text)


class FakeChat:
    id = 555


class FakeUpdate:
    def __init__(self, bot=None):
        self.message = FakeMsg()
        self.effective_chat = FakeChat()
        self._bot = bot

    def get_bot(self):
        return self._bot


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(tl, "CHUNK_SEND_DELAY_S", 0.0)


# ── construct detection ──────────────────────────────────────────────
def test_detects_rich_constructs():
    assert tl._has_rich_constructs("| A | B |\n|---|---|\n| 1 | 2 |")
    assert tl._has_rich_constructs("## Heading\nbody")
    assert tl._has_rich_constructs("```\ncode\n```")
    assert tl._has_rich_constructs("- one\n- two")
    assert tl._has_rich_constructs("1. one\n2. two")


def test_plain_prose_is_not_rich():
    assert not tl._has_rich_constructs("just a normal sentence, no formatting.")
    assert not tl._has_rich_constructs("hey what's up")
    assert not tl._has_rich_constructs("")


# ── _reply_smart routing ─────────────────────────────────────────────
def test_reply_smart_uses_rich_for_tables(monkeypatch):
    calls = {}

    async def fake_rich(chat_id, md):
        calls["rich"] = (chat_id, md)
    monkeypatch.setattr(tl, "_send_rich_message", fake_rich)

    upd = FakeUpdate()
    asyncio.run(tl._reply_smart(upd, "## H\n\n| A | B |\n|---|---|\n| 1 | 2 |"))
    assert calls["rich"][0] == 555      # sent rich to the chat
    assert upd.message.sent == []       # did NOT use the plain path


def test_reply_smart_plain_for_prose(monkeypatch):
    async def fake_rich(chat_id, md):
        raise AssertionError("must not rich-send plain prose")
    monkeypatch.setattr(tl, "_send_rich_message", fake_rich)

    upd = FakeUpdate()
    asyncio.run(tl._reply_smart(upd, "just a short answer"))
    assert upd.message.sent == ["just a short answer"]


def test_reply_smart_falls_back_on_rich_failure(monkeypatch):
    async def boom(chat_id, md):
        raise RuntimeError("rich unsupported on this tier")
    monkeypatch.setattr(tl, "_send_rich_message", boom)

    upd = FakeUpdate()
    body = "## H\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    asyncio.run(tl._reply_smart(upd, body))
    assert upd.message.sent == [body]   # cleanly fell back to plain chunked


def test_reply_smart_oversized_skips_rich_uses_chunker(monkeypatch):
    async def boom(chat_id, md):
        raise AssertionError("must not rich-send content over 32k")
    monkeypatch.setattr(tl, "_send_rich_message", boom)

    upd = FakeUpdate()
    big = "## H\n\n" + ("word " * 8000)          # >32768 chars, has heading
    assert len(big) > tl.TELEGRAM_RICH_LIMIT
    asyncio.run(tl._reply_smart(upd, big))
    assert len(upd.message.sent) > 1             # chunked across messages
    assert all(len(c) <= 4096 for c in upd.message.sent)


# ── raw send helper error handling ───────────────────────────────────
def test_send_rich_message_raises_on_api_error(monkeypatch):
    class FakeResp:
        def json(self):
            return {"ok": False, "description": "can't parse rich message"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            return FakeResp()

    monkeypatch.setattr(tl.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(tl, "TELEGRAM_BOT_TOKEN", "tok")
    with pytest.raises(RuntimeError):
        asyncio.run(tl._send_rich_message(1, "## h"))


# ── Part B — streamed finals go rich when they carry markdown ────────
class _FakeChat:
    id = 555

    async def send_action(self, action):
        pass


class _Bubble:
    """Minimal stand-in for tools.telegram_progress.ProgressBubble."""
    def __init__(self):
        self.finished: list[str] = []
        self.deleted = False
        self.stages: list[str] = []

    async def stage(self, text):
        self.stages.append(text)

    async def finish(self, text):
        self.finished.append(text)

    async def replace_with(self, sender, text):
        await sender(text)
        self.deleted = True


def test_rich_final_replaces_bubble_via_rich_send(monkeypatch):
    captured = []

    async def fake_rich_final(chat_id, md):
        captured.append((chat_id, md))
    monkeypatch.setattr(tl, "_send_rich_message", fake_rich_final)
    monkeypatch.setattr(ch, "guard_quick_chat_reply", lambda m, r: None)

    def fake_stream(message, chat_id):
        yield {"partial": "## par"}
        yield {"final": "## Done\n\n- a\n- b"}
    monkeypatch.setattr(ch, "quick_chat_stream", fake_stream)

    bubble = _Bubble()
    out = asyncio.run(tl._stream_quick_chat_reply(FakeUpdate(bot=None), "hi", 9, bubble))

    assert out.startswith("## Done")
    assert captured and captured[0][0] == 555        # finalized via rich send
    assert bubble.deleted is True                     # bubble removed
    assert bubble.finished == []                      # NOT also edited plain
    assert bubble.stages == ["## par"]                # partial streamed into bubble


def test_table_final_takes_bubble_finish_not_rich(monkeypatch):
    """Pipe tables skip the rich send: bubble.finish() renders them as
    PNG photos (raw pipes are unreadable on a phone)."""
    captured = []

    async def fake_rich_final(chat_id, md):
        captured.append((chat_id, md))
    monkeypatch.setattr(tl, "_send_rich_message", fake_rich_final)
    monkeypatch.setattr(ch, "guard_quick_chat_reply", lambda m, r: None)

    def fake_stream(message, chat_id):
        yield {"final": "## Done\n\n| a | b |\n|---|---|\n| 1 | 2 |"}
    monkeypatch.setattr(ch, "quick_chat_stream", fake_stream)

    bubble = _Bubble()
    asyncio.run(tl._stream_quick_chat_reply(FakeUpdate(bot=None), "hi", 9, bubble))
    assert captured == []
    assert bubble.finished and "| a | b |" in bubble.finished[0]


def test_plain_final_edits_bubble_when_rich_fails(monkeypatch):
    async def rich_boom(chat_id, md):
        raise RuntimeError("rich rejected")
    monkeypatch.setattr(tl, "_send_rich_message", rich_boom)
    monkeypatch.setattr(ch, "guard_quick_chat_reply", lambda m, r: None)

    def fake_stream(message, chat_id):
        yield {"partial": "hello"}
        yield {"final": "## Done\n\n| a | b |\n|---|---|\n| 1 | 2 |"}
    monkeypatch.setattr(ch, "quick_chat_stream", fake_stream)

    bubble = _Bubble()
    asyncio.run(tl._stream_quick_chat_reply(FakeUpdate(bot=None), "hi", 9, bubble))
    assert bubble.deleted is False
    assert bubble.finished and bubble.finished[0].startswith("## Done")   # edited in place

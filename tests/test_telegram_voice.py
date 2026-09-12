"""Phase 4 voice — pure-python tests for tools/telegram_voice.

No network, no whisper/Kokoro models: the TTS + whisper calls are
monkeypatched, Telegram objects are tiny fakes.
"""
import asyncio
from pathlib import Path

import pytest

from tools import telegram_voice as tv
from tools import telegram_listener as tl


# ── markdown / emoji stripping ────────────────────────────────────────

def test_strip_removes_code_fences_and_inline_code():
    text = "Run this:\n```bash\nls -la\n```\nthen check `foo.py` again."
    out = tv.strip_for_tts(text)
    assert "```" not in out and "ls -la" not in out
    assert "foo.py" in out and "`" not in out


def test_strip_markdown_marks_links_headings_bullets():
    text = ("# Title\n"
            "**bold** and _italic_ and ~~gone~~\n"
            "- first item\n"
            "1. second [link text](https://example.com) plus https://x.y/z\n"
            "| a | b |\n|---|---|\n| 1 | 2 |")
    out = tv.strip_for_tts(text)
    for bad in ("#", "*", "_", "~", "|", "](", "https://", "---"):
        assert bad not in out, f"{bad!r} survived: {out!r}"
    assert "Title" in out and "bold" in out and "italic" in out
    assert "link text" in out and "first item" in out


def test_strip_emoji_but_keeps_punctuation_and_accents():
    out = tv.strip_for_tts("🎙️ heard: café — it's 5°? 👍🏽 yes! 🔥🔥")
    assert "🎙" not in out and "👍" not in out and "🔥" not in out
    assert "café" in out and "it's" in out and "yes!" in out and "—" in out


def test_strip_empty_and_whitespace():
    assert tv.strip_for_tts("") == ""
    assert tv.strip_for_tts("   \n\n  ") == ""
    assert tv.strip_for_tts("a  \n\n\n b") == "a\nb"


# ── 600-char cap ──────────────────────────────────────────────────────

def test_cap_short_text_untouched():
    spoken, truncated = tv.cap_for_tts("short reply.")
    assert spoken == "short reply." and truncated is False


def test_cap_cuts_on_sentence_boundary():
    sentence = "This is a sentence that is exactly fifty chars ok. "
    text = sentence * 20  # 1020 chars
    spoken, truncated = tv.cap_for_tts(text)
    assert truncated is True
    assert len(spoken) <= tv.TTS_MAX_CHARS
    assert spoken.endswith(".")
    assert spoken.count("sentence") == len(spoken) // len(sentence) + (1 if len(spoken) % len(sentence) else 0)


def test_cap_falls_back_to_word_boundary_without_sentences():
    text = "word " * 300
    spoken, truncated = tv.cap_for_tts(text)
    assert truncated and len(spoken) <= tv.TTS_MAX_CHARS and spoken.endswith("word")


def test_prepare_appends_more_suffix_only_when_truncated():
    assert tv.prepare_tts_text("hi **there**") == "hi there"
    long = "Nice sentence here. " * 60
    out = tv.prepare_tts_text(long)
    assert out.endswith(tv.TTS_MORE_SUFFIX)
    assert len(out) <= tv.TTS_MAX_CHARS + len(tv.TTS_MORE_SUFFIX)
    assert tv.prepare_tts_text("```\ncode only\n```") == ""


# ── prefs toggle ──────────────────────────────────────────────────────

def test_voice_pref_roundtrip(tmp_path):
    p = tmp_path / "voice_prefs.json"
    assert tv.get_voice_pref(123, p) is False
    tv.set_voice_pref(123, True, p)
    assert tv.get_voice_pref(123, p) is True
    assert tv.get_voice_pref(456, p) is False
    tv.set_voice_pref(123, False, p)
    assert tv.get_voice_pref(123, p) is False
    assert tv.get_voice_pref(None, p) is False


def test_voice_pref_survives_corrupt_file(tmp_path):
    p = tmp_path / "voice_prefs.json"
    p.write_text("{not json", encoding="utf-8")
    assert tv.get_voice_pref(1, p) is False
    tv.set_voice_pref(1, True, p)
    assert tv.get_voice_pref(1, p) is True


# ── path naming ───────────────────────────────────────────────────────

def test_voice_in_path_naming():
    now = 1_700_000_000.0
    p = tv.voice_in_path("ogg", now)
    assert p.parent == Path.home() / "AI_Agent" / "memory" / "voice_in"
    assert p.name == f"{tv._ts(now)}.ogg"
    assert len(p.stem) == 15 and p.stem[8] == "-"  # YYYYmmdd-HHMMSS
    assert tv.voice_in_path(".MP3", now).suffix == ".mp3"
    assert tv.voice_in_path("", now).suffix == ".ogg"
    assert tv.voice_out_path(now).name == f"{tv._ts(now)}.ogg"
    assert tv.voice_out_path(now).parent.name == "voice_out"


class _Media:
    def __init__(self, mime_type=None, file_name=None):
        self.mime_type = mime_type
        self.file_name = file_name


def test_ext_for_media():
    assert tv.ext_for_media(_Media("audio/ogg")) == "ogg"
    assert tv.ext_for_media(_Media("audio/mpeg")) == "mp3"
    assert tv.ext_for_media(_Media("audio/mpeg", "song.M4A")) == "m4a"
    assert tv.ext_for_media(_Media(None)) == "ogg"


# ── fakes for handler-level tests ─────────────────────────────────────

class FakeChat:
    def __init__(self, cid=123):
        self.id = cid

    async def send_action(self, action):
        pass


class FakeFile:
    def __init__(self, payload=b"OggS"):
        self.payload = payload

    async def download_to_drive(self, path):
        Path(path).write_bytes(self.payload)


class FakeVoice:
    mime_type = "audio/ogg"
    file_name = None

    async def get_file(self):
        return FakeFile()


class FakeMsg:
    def __init__(self, chat, voice=None, text=None):
        self.chat = chat
        self.voice = voice
        self.audio = None
        self.text = text
        self.sent: list[str] = []
        self.voices: list[bytes] = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)

    async def reply_voice(self, fh, **kw):
        self.voices.append(fh.read())


class FakeUpdate:
    def __init__(self, msg, chat):
        self.message = msg
        self.effective_chat = chat


class FakeCtx:
    def __init__(self, args=None):
        self.args = args or []


class FakeApp:
    def __init__(self):
        self.handlers = []

    def add_handler(self, h):
        self.handlers.append(h)


def test_register_adds_handlers_and_hook_once():
    app = FakeApp()
    before = list(tl.REPLY_HOOKS)
    try:
        tv.register(app)
        tv.register(app)
        assert tl.REPLY_HOOKS.count(tv.voice_pref_hook) == 1
        assert len(app.handlers) == 4  # /voice + voice/audio, twice
    finally:
        tl.REPLY_HOOKS[:] = before


def test_voice_command_toggles(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "PREFS_PATH", tmp_path / "prefs.json")
    monkeypatch.setattr(tv, "_authorized", lambda u: True)
    chat = FakeChat(77)
    msg = FakeMsg(chat)
    upd = FakeUpdate(msg, chat)

    asyncio.run(tv.voice_command(upd, FakeCtx()))
    assert "off" in msg.sent[-1]
    asyncio.run(tv.voice_command(upd, FakeCtx(["on"])))
    assert tv.get_voice_pref(77, tmp_path / "prefs.json") is True
    asyncio.run(tv.voice_command(upd, FakeCtx()))
    assert "Voice replies are on" in msg.sent[-1]
    asyncio.run(tv.voice_command(upd, FakeCtx(["OFF"])))
    assert tv.get_voice_pref(77, tmp_path / "prefs.json") is False


def test_send_voice_reply_runs_tts_and_replies(tmp_path, monkeypatch):
    calls = []

    def fake_tts_to_ogg(text, out_path):
        calls.append(text)
        Path(out_path).write_bytes(b"OGGDATA")
        return out_path

    from tools import tts_tool
    monkeypatch.setattr(tts_tool, "tts_to_ogg", fake_tts_to_ogg)
    monkeypatch.setattr(tv, "VOICE_OUT_DIR", tmp_path)
    monkeypatch.setattr(tv, "voice_out_path", lambda now=None: tmp_path / "out.ogg")
    chat = FakeChat()
    msg = FakeMsg(chat)
    upd = FakeUpdate(msg, chat)

    ok = asyncio.run(tv.send_voice_reply(upd, "**Yep**, done 🔥"))
    assert ok is True
    assert calls == ["Yep, done"]
    assert msg.voices == [b"OGGDATA"]
    assert not (tmp_path / "out.ogg").exists()  # cleaned up after send


def test_send_voice_reply_skips_empty_and_errors(tmp_path, monkeypatch):
    from tools import tts_tool
    chat = FakeChat()
    msg = FakeMsg(chat)
    upd = FakeUpdate(msg, chat)
    assert asyncio.run(tv.send_voice_reply(upd, "```\nonly code\n```")) is False
    monkeypatch.setattr(tts_tool, "tts_to_ogg", lambda t, p: "ERROR: no engine")
    monkeypatch.setattr(tv, "voice_out_path", lambda now=None: tmp_path / "x.ogg")
    assert asyncio.run(tv.send_voice_reply(upd, "hello")) is False
    assert msg.voices == []


def test_pref_hook_only_fires_when_on(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "PREFS_PATH", tmp_path / "prefs.json")
    fired = []

    async def fake_send(update, reply):
        fired.append(reply)
        return True

    monkeypatch.setattr(tv, "send_voice_reply", fake_send)
    chat = FakeChat(5)
    upd = FakeUpdate(FakeMsg(chat), chat)
    asyncio.run(tv.voice_pref_hook(upd, "a"))
    assert fired == []
    tv.set_voice_pref(5, True, tmp_path / "prefs.json")
    asyncio.run(tv.voice_pref_hook(upd, "b"))
    assert fired == ["b"]


def test_emit_reply_on_reply_overrides_hooks():
    seen = []

    async def hook(update, reply):
        seen.append(("hook", reply))

    async def override(update, reply):
        seen.append(("override", reply))

    async def boom(update, reply):
        raise RuntimeError("nope")

    before = list(tl.REPLY_HOOKS)
    try:
        tl.REPLY_HOOKS[:] = [boom, hook]
        asyncio.run(tl._emit_reply(None, "r1"))
        asyncio.run(tl._emit_reply(None, "r2", override))
    finally:
        tl.REPLY_HOOKS[:] = before
    assert seen == [("hook", "r1"), ("override", "r2")]


def test_handle_voice_transcribes_and_routes_through_text_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "VOICE_IN_DIR", tmp_path)
    monkeypatch.setattr(tv, "voice_in_path", lambda ext="ogg", now=None: tmp_path / f"note.{ext}")
    monkeypatch.setattr(tv, "_authorized", lambda u: True)

    from tools import whisper_tool
    got = {}

    def fake_transcribe(path, model_size="base"):
        got["path"] = path
        got["model"] = model_size
        return "what's the weather in pasco"

    monkeypatch.setattr(whisper_tool, "transcribe_file", fake_transcribe)

    routed = {}

    async def fake_handle_text(update, context, text, on_reply=None):
        routed["text"] = text
        routed["on_reply"] = on_reply

    monkeypatch.setattr(tl, "_handle_text", fake_handle_text)

    chat = FakeChat()
    msg = FakeMsg(chat, voice=FakeVoice())
    upd = FakeUpdate(msg, chat)
    asyncio.run(tv.handle_voice(upd, FakeCtx()))

    assert Path(got["path"]).read_bytes() == b"OggS"
    assert got["path"].endswith("note.ogg")
    assert got["model"] == tv.WHISPER_MODEL == "small.en"
    assert msg.sent and msg.sent[0].startswith("🎙️ heard:")
    assert "what's the weather in pasco" in msg.sent[0]
    assert routed["text"] == "what's the weather in pasco"
    assert routed["on_reply"] is tv.send_voice_reply


def test_handle_voice_reports_failed_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "voice_in_path", lambda ext="ogg", now=None: tmp_path / "n.ogg")
    monkeypatch.setattr(tv, "_authorized", lambda u: True)
    from tools import whisper_tool
    monkeypatch.setattr(whisper_tool, "transcribe_file", lambda p, m="base": "ERROR: boom")

    async def never(*a, **k):
        raise AssertionError("text path must not run on a failed transcript")

    monkeypatch.setattr(tl, "_handle_text", never)
    chat = FakeChat()
    msg = FakeMsg(chat, voice=FakeVoice())
    asyncio.run(tv.handle_voice(FakeUpdate(msg, chat), FakeCtx()))
    assert len(msg.sent) == 1 and "Couldn't make that out" in msg.sent[0]


def test_handle_voice_unauthorized_is_silent(monkeypatch):
    monkeypatch.setattr(tv, "_authorized", lambda u: False)
    chat = FakeChat()
    msg = FakeMsg(chat, voice=FakeVoice())
    asyncio.run(tv.handle_voice(FakeUpdate(msg, chat), FakeCtx()))
    assert msg.sent == []


def test_help_mentions_voice():
    assert "/voice on|off" in tl._help_text()

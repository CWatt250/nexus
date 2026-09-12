"""Phase 4 — Telegram voice: voice notes in (whisper) + spoken replies (Kokoro).

    voice note / audio  → saved to memory/voice_in/<ts>.<ext>, transcribed
                          with faster-whisper (small.en, int8 CPU), echoed
                          as "🎙️ heard: …", then routed through the SAME
                          text path as a typed message
                          (telegram_listener._handle_text). The reply is
                          always sent as text AND as a voice note.
    /voice on|off       → per-chat toggle (memory/voice_prefs.json): when
                          on, typed messages get a voice-note reply too.

Voice replies: text → strip markdown/emoji/code → cap at TTS_MAX_CHARS →
Kokoro → ffmpeg OGG/Opus 48k mono 32k → reply_voice. Whisper, TTS and
ffmpeg all run in asyncio.to_thread so the bot loop never blocks.

Wire with `tools.telegram_voice.register(app)` from the listener (after
the TEXT handler). Authorization reuses telegram_listener.is_authorized,
imported lazily to avoid a circular import at module load.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from pathlib import Path

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

log = logging.getLogger("nexus.telegram_voice")

MEMORY_DIR = Path.home() / "AI_Agent" / "memory"
VOICE_IN_DIR = MEMORY_DIR / "voice_in"
VOICE_OUT_DIR = MEMORY_DIR / "voice_out"
PREFS_PATH = MEMORY_DIR / "voice_prefs.json"

WHISPER_MODEL = "small.en"
TTS_MAX_CHARS = 600
TTS_MORE_SUFFIX = " More in the text."

_MIME_EXT = {"audio/ogg": "ogg", "audio/opus": "ogg", "audio/mpeg": "mp3",
             "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/wav": "wav",
             "audio/x-wav": "wav", "audio/flac": "flac"}


# ── prefs (/voice on|off) ─────────────────────────────────────────────

def _load_prefs(path: Path | None = None) -> dict:
    path = path or PREFS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get_voice_pref(chat_id: int | None, path: Path | None = None) -> bool:
    if chat_id is None:
        return False
    return bool(_load_prefs(path).get(str(chat_id), False))


def set_voice_pref(chat_id: int, on: bool, path: Path | None = None) -> None:
    path = path or PREFS_PATH
    prefs = _load_prefs(path)
    prefs[str(chat_id)] = bool(on)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prefs, indent=1), encoding="utf-8")
    except OSError as exc:
        log.warning("voice prefs write failed: %s", exc)


# ── text → speakable text ─────────────────────────────────────────────

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL_RE = re.compile(r"https?://\S+")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.MULTILINE)
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", re.MULTILINE)
_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3}|~~)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_STRAY_MARKS_RE = re.compile(r"[*_~#|>]+")
_WS_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n\s*\n+")


def _is_emoji_char(ch: str) -> bool:
    cp = ord(ch)
    if cp == 0x200D or 0xFE00 <= cp <= 0xFE0F or 0x1F3FB <= cp <= 0x1F3FF:
        return True  # ZWJ, variation selectors, skin-tone modifiers
    return unicodedata.category(ch) in ("So", "Sk", "Cs", "Co", "Cn")


def strip_for_tts(text: str) -> str:
    """Markdown/emoji/code → plain prose Kokoro can read aloud."""
    if not text:
        return ""
    t = _FENCE_RE.sub(" ", text)
    t = _INLINE_CODE_RE.sub(r"\1", t)
    t = _LINK_RE.sub(r"\1", t)
    t = _URL_RE.sub("", t)
    t = _TABLE_SEP_RE.sub("", t)
    t = _HEADING_RE.sub("", t)
    t = _BULLET_RE.sub("", t)
    for _ in range(2):  # nested **_x_**
        t = _EMPHASIS_RE.sub(r"\2", t)
    t = _STRAY_MARKS_RE.sub(" ", t)
    t = "".join(ch for ch in t if not _is_emoji_char(ch))
    t = _WS_RE.sub(" ", t)
    t = _BLANK_RE.sub("\n", t)
    return "\n".join(line.strip() for line in t.splitlines()).strip()


def cap_for_tts(text: str, limit: int = TTS_MAX_CHARS) -> tuple[str, bool]:
    """Cap `text` at ~`limit` chars on a sentence boundary when possible.
    Returns (spoken_text, truncated)."""
    if len(text) <= limit:
        return text, False
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "), head.rfind("\n"))
    if cut < limit // 2:
        cut = head.rfind(" ")
    if cut <= 0:
        cut = limit
    return head[:cut + 1].strip(), True


def prepare_tts_text(reply: str, limit: int = TTS_MAX_CHARS) -> str:
    """Full pipeline: strip → cap → 'More in the text.' when truncated.
    Empty string means nothing worth speaking."""
    spoken, truncated = cap_for_tts(strip_for_tts(reply), limit)
    if not spoken:
        return ""
    return spoken + TTS_MORE_SUFFIX if truncated else spoken


# ── paths ─────────────────────────────────────────────────────────────

def _ts(now: float | None = None) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


def voice_in_path(ext: str = "ogg", now: float | None = None) -> Path:
    return VOICE_IN_DIR / f"{_ts(now)}.{ext.lstrip('.').lower() or 'ogg'}"


def voice_out_path(now: float | None = None) -> Path:
    return VOICE_OUT_DIR / f"{_ts(now)}.ogg"


def ext_for_media(media) -> str:
    """File extension for a telegram Voice/Audio object (ogg default)."""
    name = getattr(media, "file_name", None) or ""
    if "." in name:
        return name.rsplit(".", 1)[1].lower()
    mime = (getattr(media, "mime_type", None) or "").lower()
    return _MIME_EXT.get(mime, "ogg")


# ── handlers ──────────────────────────────────────────────────────────

def _authorized(update: Update) -> bool:
    try:
        from tools.telegram_listener import is_authorized  # noqa: PLC0415
        return is_authorized(update)
    except Exception as exc:  # noqa: BLE001 — listener not importable (tests)
        log.warning("is_authorized unavailable (%s) — allowing", exc)
        return True


async def send_voice_reply(update: Update, reply: str) -> bool:
    """Speak `reply` back as a voice note. Returns True if one was sent.
    Never raises — the text reply has already landed."""
    spoken = prepare_tts_text(reply)
    if not spoken:
        return False
    from tools import tts_tool  # noqa: PLC0415
    out = voice_out_path()
    t0 = time.monotonic()
    try:
        res = await asyncio.to_thread(tts_tool.tts_to_ogg, spoken, str(out))
    except Exception as exc:  # noqa: BLE001
        res = f"ERROR: {type(exc).__name__}: {exc}"
    if res.startswith("ERROR"):
        log.warning("voice reply synth failed: %s", res)
        return False
    log.info("voice reply: %d chars → ogg in %.1fs", len(spoken), time.monotonic() - t0)
    try:
        with open(res, "rb") as fh:
            await update.message.reply_voice(fh)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("reply_voice failed: %s", exc)
        return False
    finally:
        try:
            Path(res).unlink()
        except OSError:
            pass


async def voice_pref_hook(update: Update, reply: str) -> None:
    """REPLY_HOOK for typed messages: voice the reply only if /voice is on."""
    if get_voice_pref(update.effective_chat.id):
        await send_voice_reply(update, reply)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    msg = update.message
    media = msg.voice or msg.audio
    if media is None:
        return
    dest = voice_in_path(ext_for_media(media))
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        tg_file = await media.get_file()
        await tg_file.download_to_drive(str(dest))
    except Exception as exc:  # noqa: BLE001
        await msg.reply_text(f"Couldn't download that voice note ({type(exc).__name__}).")
        return

    await msg.chat.send_action("typing")
    from tools import whisper_tool  # noqa: PLC0415
    t0 = time.monotonic()
    try:
        transcript = await asyncio.to_thread(whisper_tool.transcribe_file, str(dest), WHISPER_MODEL)
    except Exception as exc:  # noqa: BLE001
        transcript = f"ERROR: {type(exc).__name__}: {exc}"
    log.info("whisper %s: %.1fs for %s → %r", WHISPER_MODEL, time.monotonic() - t0,
             dest.name, transcript[:80])
    if not transcript or transcript.startswith("ERROR"):
        await msg.reply_text("🎙️ Couldn't make that out — try again or type it.")
        return

    await msg.reply_text(f"🎙️ heard: “{transcript}”")
    from tools import telegram_listener as tl  # noqa: PLC0415
    await tl._handle_text(update, context, transcript, on_reply=send_voice_reply)


async def voice_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/voice on|off — per-chat spoken-reply toggle."""
    if not _authorized(update):
        return
    chat_id = update.effective_chat.id
    arg = (context.args[0].lower() if context.args else "").strip()
    if arg in ("on", "off"):
        set_voice_pref(chat_id, arg == "on")
        await update.message.reply_text(
            "Voice replies on — you'll get a voice note with each answer." if arg == "on"
            else "Voice replies off. Voice notes you send still get a spoken answer.")
        return
    state = "on" if get_voice_pref(chat_id) else "off"
    await update.message.reply_text(f"Voice replies are {state}. /voice on | /voice off")


def register(app) -> None:
    """Add the voice handlers to a python-telegram-bot Application and hook
    spoken replies into the listener's text path."""
    app.add_handler(CommandHandler("voice", voice_command))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    from tools import telegram_listener as tl  # noqa: PLC0415
    if voice_pref_hook not in tl.REPLY_HOOKS:
        tl.REPLY_HOOKS.append(voice_pref_hook)
    log.info("telegram_voice: registered /voice + voice/audio intake (whisper %s)", WHISPER_MODEL)

#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Telegram Listener — receives commands from Telegram and routes to Nexus API."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Setup
load_dotenv(Path.home() / "AI_Agent" / ".env")
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
NEXUS_API_URL = "http://localhost:11435"

# Authorized chat IDs (only respond to these)
AUTHORIZED_CHATS = set()
if TELEGRAM_CHAT_ID:
    AUTHORIZED_CHATS.add(int(TELEGRAM_CHAT_ID))


def is_authorized(update: Update) -> bool:
    """Check if the message is from an authorized chat."""
    if not AUTHORIZED_CHATS:
        return True  # No restriction if not configured
    return update.effective_chat.id in AUTHORIZED_CHATS


# ── Phase 41 — chunked send + live-draft streaming ──────────────────────
import threading  # noqa: E402

CHUNK_SEND_DELAY_S = 0.4   # spacing between sequential message chunks
# Route wall clock. 60 s (was 25): the brain re-prefills the whole prompt
# every turn, and a lite_agent tool + formatter can legitimately take
# 30-40 s. On timeout we say so and STILL deliver when the thread lands.
ROUTE_TIMEOUT_S = 60


async def _reply_chunked(update: Update, text: str) -> None:
    """Send `text` via update.message.reply_text, split into ≤4096-char
    messages (the Telegram hard limit) using the shared chunker. Long
    replies arrive complete across multiple messages instead of being
    truncated. A small delay between sends stays clear of rate limits."""
    from core.telegram_chunk import chunk_text  # noqa: PLC0415
    chunks = chunk_text(text) or ["(empty reply)"]
    for i, chunk in enumerate(chunks):
        await update.message.reply_text(chunk)
        if i < len(chunks) - 1:
            await asyncio.sleep(CHUNK_SEND_DELAY_S)


async def _stream_quick_chat_reply(update: Update, message: str,
                                   chat_id: int, bubble=None) -> str:
    """Stream a quick_chat reply INTO the progress bubble.

    `quick_chat_stream` yields scrubbed, sentence-buffered partials (never
    the raw accumulator); each one edits the bubble (throttled inside
    ProgressBubble). The final runs through the false-promise guard, then
    replaces the bubble. Returns the finalized reply text.

    Raises on a generation error so the caller falls back to the blocking
    path (reply never dropped) — the bubble is left in place for it."""
    from workers import conversation_handler as _ch  # noqa: PLC0415
    from tools.telegram_progress import ProgressBubble  # noqa: PLC0415
    if bubble is None:
        bubble = await ProgressBubble(update).start()
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def _producer() -> None:
        try:
            for ev in _ch.quick_chat_stream(message, chat_id):
                loop.call_soon_threadsafe(q.put_nowait, ev)
            loop.call_soon_threadsafe(q.put_nowait, {"_done": True})
        except Exception as exc:  # noqa: BLE001 — surfaced to caller below
            loop.call_soon_threadsafe(q.put_nowait, {"_error": exc})

    threading.Thread(target=_producer, daemon=True).start()

    final_text: str | None = None
    err: Exception | None = None
    while True:
        ev = await q.get()
        if "_error" in ev:
            err = ev["_error"]
            break
        if "_done" in ev:
            break
        if "final" in ev:
            final_text = ev["final"]
            continue
        partial = ev.get("partial", "")
        if partial.strip():
            await bubble.stage(partial)

    if err is not None:
        raise err
    if not final_text:
        final_text = "Came back empty — say that again?"
    # False-promise guard (same one the blocking path uses): a tool-less
    # "let me check…" becomes a real queued task + the recovery line.
    try:
        rec = _ch.guard_quick_chat_reply(message, final_text)
        if rec is not None:
            final_text = rec["reply"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("stream guard failed: %s", exc)
    await _finish_bubble(update, bubble, final_text)
    return final_text


async def _finish_bubble(update: Update, bubble, text: str) -> None:
    """Final delivery: rich markdown (tables/headings) replaces the bubble
    with a native rich message; everything else edits the bubble in
    place, chunked if long. Pipe tables take the bubble.finish() route:
    text edits the bubble, each table lands as a PNG photo (raw pipes are
    unreadable on a phone)."""
    from tools.telegram_render import has_table  # noqa: PLC0415
    if _has_rich_constructs(text) and len(text) <= TELEGRAM_RICH_LIMIT and not has_table(text):
        try:
            await bubble.replace_with(
                lambda md: _send_rich_message(update.effective_chat.id, md), text)
            return
        except Exception as exc:  # noqa: BLE001
            logger.info("rich send failed (%s) — editing bubble instead", exc)
    await bubble.finish(text)


# ── Phase 42 — Telegram Rich Messages (Bot API 10.1) ────────────────────
import json  # noqa: E402
import re  # noqa: E402

# Rich messages allow up to 32,768 chars (vs 4096 plain) and render
# markdown (headings, tables, code, lists) natively instead of as literal
# `#`/`|`/``` characters. PTB 22.7 has no typed support, so these go
# through a raw HTTP POST. rich_message is a JSON object: {"markdown": ...}.
TELEGRAM_RICH_LIMIT = 32768

# Markdown constructs worth rendering natively: headings, table rows, code
# fences, bullet/numbered lists. A reply with none of these is plain prose
# and stays on the existing path (no point rich-ifying a one-liner).
_RICH_CONSTRUCT_RE = re.compile(
    r"(^#{1,6}\s)"            # heading
    r"|(^\s*\|.+\|\s*$)"      # table row
    r"|(```)"                 # code fence
    r"|(^\s*[-*]\s+\S)"       # bullet list item
    r"|(^\s*\d+\.\s+\S)",     # numbered list item
    re.MULTILINE,
)


def _has_rich_constructs(text: str) -> bool:
    return bool(text) and bool(_RICH_CONSTRUCT_RE.search(text))


async def _send_rich_message(chat_id: int, markdown_text: str) -> None:
    """Send one rich message via the raw sendRichMessage endpoint (Bot API
    10.1; PTB 22.7 lacks a typed method). Renders `markdown_text` natively.
    Raises on any Telegram rejection / network error so the caller falls
    back to the plain chunked path — a rich failure never drops the reply.
    Isolated here so a future PTB typed method is a one-function swap."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendRichMessage"
    payload = {
        "chat_id": chat_id,
        "rich_message": json.dumps({"markdown": markdown_text}),
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, data=payload)
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"sendRichMessage rejected: {body.get('description')}")


async def _reply_smart(update: Update, text: str) -> None:
    """Reply with a native rich message when the text has formatting worth
    preserving (tables/headings/code/lists) and fits the 32k rich limit;
    otherwise — or on ANY rich-send failure — fall back to the plain
    chunked path. Plain one-line replies skip rich entirely."""
    if _has_rich_constructs(text) and len(text) <= TELEGRAM_RICH_LIMIT:
        try:
            await _send_rich_message(update.effective_chat.id, text)
            return
        except Exception as exc:  # noqa: BLE001
            logger.info("rich send failed (%s) — falling back to plain chunked",
                        exc)
    await _reply_chunked(update, text)


def _help_text() -> str:
    """Built from conversation_handler.SLASH_COMMANDS so the ladder can't
    drift from what the parser actually accepts."""
    from workers.conversation_handler import SLASH_COMMANDS  # noqa: PLC0415
    ladder = []
    for cmd, spec in SLASH_COMMANDS.items():
        if spec.get("deprecated_alias_for"):
            continue  # /real: still works, no longer advertised
        star = "  ★ default for builds" if cmd == "/local" else ""
        ladder.append(f"  {cmd} <prompt> — {spec['blurb']}{star}")
    return (
        "Nexus commands\n\n"
        "Just talk — plain messages get routed (chat, lookup, task, build).\n\n"
        "Builds (explicit tier — cloud tiers only fire when you name them):\n"
        + "\n".join(ladder) + "\n\n"
        "Me:\n"
        "  /think on|off — show my reasoning under each reply (💭). "
        "  /new — fresh conversation (forgets the recent thread).\n"
        "\"show your work\" in a message does it once.\n"
        "  /voice on|off — reply with a voice note too (🎙️). "
        "Voice notes you send are transcribed and always answered in voice.\n"
        "  /creds [service] — credential status, or setup steps for one service\n"
        "  /computer <task> — drive the :99 browser (caps: 30 min, $5; --unsafe skips stops)\n"
        "  /image [flux|qwen21|sdxl|sd15] <prompt> — local image gen\n"
        "  /screenshot, /desktop — see the desktop (if the desktop bridge is loaded)\n"
        "  /status — is Nexus up\n"
        "  /tasks — recent task queue\n\n"
        "Text shortcuts:\n"
        "  wiki <query> · ingest <text|url> · queue: <task> · queue\n"
        "  dispatch: <prompt> · go cc_xxx · cancel cc_xxx · retry cc_xxx · extend cc_xxx <min>\n"
        "  restart nexus-* · script <topic> · create video <topic>"
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "Nexus here. Say what you need — I'll chat, look things up, or build it.\n\n"
        "/help lists the commands."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    if not is_authorized(update):
        return
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{NEXUS_API_URL}/health", timeout=10)
            if response.status_code == 200:
                data = response.json()
                status = data.get("status", "unknown")
                await update.message.reply_text(
                    "Up and healthy." if status == "ok" else f"API says: {status}.")
            else:
                await update.message.reply_text(
                    f"The API answered but not happily (HTTP {response.status_code}).")
    except Exception as e:
        logger.warning("status_command: %s", e)
        await update.message.reply_text(
            "Can't reach the Nexus API right now — the service may be down or restarting.")


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/new — fresh conversation: wipe this chat's rolling history."""
    if not is_authorized(update):
        return
    from core import telegram_chats as _tcs  # noqa: PLC0415
    n = _tcs.clear_chat(update.effective_chat.id)
    await update.message.reply_text("Clean slate." if n else "Already a clean slate.")


async def think_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/think on|off — per-chat show-your-work toggle."""
    if not is_authorized(update):
        return
    from workers import conversation_handler as ch  # noqa: PLC0415
    chat_id = update.effective_chat.id
    arg = (context.args[0].lower() if context.args else "").strip()
    if arg in ("on", "off"):
        ch.set_think_pref(chat_id, arg == "on")
        await update.message.reply_text(
            "Showing my work from here on — 💭 under each reply." if arg == "on"
            else "Back to answers only.")
        return
    state = "on" if ch.get_think_pref(chat_id) else "off"
    await update.message.reply_text(f"Show-your-work is {state}. /think on | /think off")


async def creds_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/creds → status table; /creds <service> → setup steps for that service."""
    if not is_authorized(update):
        return
    from tools import credentials_helper as _cred  # noqa: PLC0415
    service = (context.args[0].lower() if context.args else "").strip()
    try:
        text = _cred.telegram_instructions(service) if service else _cred.telegram_status()
    except Exception as e:
        logger.warning("creds_command: %s", e)
        await update.message.reply_text("Couldn't read the credentials registry — check the log.")
        return
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception:
        await _reply_chunked(update, text)


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /tasks command — read queue directly (no API hop)."""
    if not is_authorized(update):
        return
    try:
        from core import task_queue
        rows = task_queue.list_tasks(limit=10)
        if not rows:
            await update.message.reply_text("Queue is empty.")
            return
        lines = []
        for r in rows:
            preview = (r.get("input") or "")[:60]
            lines.append(f"- {r['task_id']}  [{r['status']}]  {preview}")
        await update.message.reply_text("Recent tasks:\n" + "\n".join(lines))
    except Exception as e:
        logger.exception("tasks_command failed: %s", e)
        await update.message.reply_text("Couldn't read the queue just now — check the log.")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop command."""
    if not is_authorized(update):
        return
    await update.message.reply_text("Stop command received. (Not yet implemented)")


async def _content_create_in_background(update: Update, topic: str, duration: int) -> None:
    """Phase 21 — long-running content pipeline. Runs the orchestrator
    in a worker thread so the listener event loop stays responsive,
    then sends the final mp4 back to the same chat. Best-effort —
    exceptions surface as a Telegram error reply."""
    chat_id = update.effective_chat.id
    try:
        from tools import content_create as _cc  # noqa: PLC0415
        info = await asyncio.to_thread(
            _cc.content_create_core, topic, duration, "energetic",
            True,  # prefer_real_visuals
            True,  # add_music
            ("9x16", "1x1", "16x9"),
        )
        final_path = info["final_video_path"]
        bot = update.get_bot()
        variants = info.get("aspect_variants", {})
        variant_line = (
            "variants: " + ", ".join(sorted(variants.keys())) + "\n"
            if len(variants) > 1 else ""
        )
        music_line = (
            f"music: {Path(info['music_track']).stem}\n"
            if info.get("music_used") else "music: (none)\n"
        )
        try:
            with open(final_path, "rb") as fh:
                await bot.send_video(
                    chat_id=chat_id,
                    video=fh,
                    caption=(
                        f"🎬 {Path(final_path).name}\n"
                        f"scenes: {info['scene_clips_built']} | "
                        f"actual: {info['duration_actual_seconds']:.1f}s\n"
                        f"{music_line}"
                        f"{variant_line}"
                        f"backend: {info['script_backend']} | "
                        f"cost: ${info['cost_usd']:.4f}"
                    ),
                )
        except Exception as send_exc:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ Video built at {final_path} but send failed: "
                    f"{type(send_exc).__name__}: {send_exc}"
                ),
            )
    except Exception as exc:
        try:
            await update.get_bot().send_message(
                chat_id=chat_id,
                text=f"⚠️ create-video failed: {type(exc).__name__}: {exc}",
            )
        except Exception:
            logger.exception("background video send error")


async def _build_in_background(update: Update, description: str, target_path: str, tech: str,
                               *, model: str | None = None) -> None:
    """Phase 27 + 28 — long-running local build. Mirrors
    _content_create_in_background: runs in a worker thread so the
    listener event loop stays responsive, then sends the result back
    to the same chat AND auto-attaches the generated file (Phase 28
    fix for the missing-file bug). `model` overrides the default
    qwen3.6 → /local + SIMPLE_BUILD pass qwen3-coder:30b for code."""
    chat_id = update.effective_chat.id
    bot = update.get_bot()
    try:
        from tools import local_builder  # noqa: PLC0415
        result = await asyncio.to_thread(
            local_builder.build_thing_core, description, target_path, tech, model,
        )
    except Exception as exc:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ build failed: {type(exc).__name__}: {exc}",
            )
        except Exception:
            logger.exception("background build error-send failed")
        return

    notes_line = "" if result.notes == "ok" else f"\n  ⚠ {result.notes}"
    msg = (
        f"🛠️ built {result.path}\n"
        f"  tech    : {result.tech_stack}\n"
        f"  size    : {result.bytes_written} bytes / {result.lines} lines\n"
        f"  wall    : {result.wall_seconds}s on {result.backend}{notes_line}"
    )
    try:
        await bot.send_message(chat_id=chat_id, text=msg)
    except Exception:
        logger.exception("background build success-send failed")
    # Phase 28 — auto-attach the generated file so the user can play
    # with it without ssh-ing into the box. Best-effort: skipped on
    # files >10MB, errors swallowed.
    try:
        from pathlib import Path as _Path  # noqa: PLC0415
        artifact = _Path(result.path).expanduser()
        if artifact.exists() and artifact.is_file() and artifact.stat().st_size <= 10 * 1024 * 1024:
            with open(artifact, "rb") as fh:
                await bot.send_document(
                    chat_id=chat_id, document=fh,
                    caption=f"{artifact.name} — {tech} build via {result.backend}",
                )
    except Exception:
        logger.exception("background build attach failed")
    # Phase 28 — visual verify HTML builds and warn if the page looks
    # broken. Heavyweight (Playwright + qwen2.5vl), but only fires for
    # html outputs and never blocks the success message above.
    if (result.tech_stack or "").lower() in ("html", "htm"):
        try:
            from tools import visual_verify  # noqa: PLC0415
            verdict = await asyncio.to_thread(
                visual_verify.verify_html_artifact_safe, result.path,
            )
            if verdict.get("needs_review"):
                await bot.send_message(
                    chat_id=chat_id,
                    text=("⚠️ visual verify flagged this build for review — "
                          + verdict.get("notes", "")[:240]),
                )
            shot = verdict.get("screenshot_path") or ""
            if shot:
                from pathlib import Path as _Path  # noqa: PLC0415
                shot_p = _Path(shot)
                if shot_p.exists():
                    with open(shot_p, "rb") as fh:
                        await bot.send_photo(
                            chat_id=chat_id, photo=fh,
                            caption="visual verify screenshot",
                        )
        except Exception:
            logger.exception("background build visual verify failed")


# Phase 27 build-arg helpers — still used by the /local slash handler.
# (Phase 39 removed the listener's build-intent regex interception;
# no-slash "build me X" messages now flow to route_message → LLM router.)
import re as _re_p27  # noqa: E402  — local alias to avoid clashing with module-level re imports
_TG_BUILD_AT_PATH_RE = _re_p27.compile(
    r"^(.+?)\s+at\s+(\S+)\s*$", _re_p27.IGNORECASE | _re_p27.DOTALL,
)
_TG_BUILD_TECH_RE = _re_p27.compile(
    r"\b(?:in|as|using)\s+(html|python|markdown|md|shell|bash)\b",
    _re_p27.IGNORECASE,
)


def _extract_build_args(body: str) -> tuple[str, str, str]:
    """Pull (description, target_path, tech) out of a build-intent body.
    Same logic as conversation_handler._route_message_inner. Defaults:
    target_path = ~/AI_Agent/games/<slug>.html, tech = html."""
    at_m = _TG_BUILD_AT_PATH_RE.match(body)
    if at_m:
        description = at_m.group(1).strip()
        target_path = at_m.group(2).strip()
    else:
        description = body
        slug_words = _re_p27.findall(r"[a-zA-Z0-9]+", description.lower())[:5]
        slug = "-".join(slug_words) or "build"
        target_path = f"~/AI_Agent/games/{slug}.html"
    tech_m = _TG_BUILD_TECH_RE.search(description)
    tech = tech_m.group(1).lower() if tech_m else "html"
    if tech == "md":
        tech = "markdown"
    if tech == "bash":
        tech = "shell"
    return description, target_path, tech


# ─── Phase 28 — slash command handlers ────────────────────────────────
# /code, /pro, /real → enqueue tier-aware Claude Code dispatch.
# /local → background qwen3-coder:30b build with auto-attach.
# /quick → synchronous quick_chat.
# All five ack within 2 seconds; the cc_dispatcher daemon + reporter
# handle long-running cloud dispatches out-of-band.

_TIER_TO_USER_FACING_CMD = {
    "max": "max", "flash": "code", "pro": "pro",
    "api": "api", "real": "real",
}


async def _handle_slash_dispatch(update: Update, tier: str, prompt: str) -> None:
    """Shared body for /max /code /pro /api /real. Routes through the
    tier-aware cc_dispatcher inbox. Acks immediately; the reporter
    daemon posts the completion + auto-attaches artifacts."""
    if not is_authorized(update):
        return
    if not prompt.strip():
        cmd = _TIER_TO_USER_FACING_CMD.get(tier, tier)
        await update.message.reply_text(f"/{cmd}: needs a prompt.")
        return
    try:
        from workers import conversation_handler as ch  # noqa: PLC0415
        result = await asyncio.to_thread(ch._enqueue_tiered_dispatch, prompt, tier)
    except Exception as exc:
        logger.exception("slash dispatch failed: %s", exc)
        await update.message.reply_text(
            "Couldn't hand that to the dispatcher — it's logged. Try once more?")
        return
    await _reply_chunked(update, result.get("reply", "(no reply)"))


async def max_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/max <prompt> — Claude Sonnet via Max plan ($0 marginal). Phase 29 default."""
    prompt = " ".join(context.args).strip() if context.args else ""
    await _handle_slash_dispatch(update, "max", prompt)


async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/code <prompt> — DeepSeek V4-Flash cloud build (saves Max quota)."""
    prompt = " ".join(context.args).strip() if context.args else ""
    await _handle_slash_dispatch(update, "flash", prompt)


async def pro_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pro <prompt> — DeepSeek V4-Pro cloud build (smarter, ~$0.05)."""
    prompt = " ".join(context.args).strip() if context.args else ""
    await _handle_slash_dispatch(update, "pro", prompt)


async def api_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/api <prompt> — Anthropic Sonnet 4.6 via API key (paid fallback)."""
    prompt = " ".join(context.args).strip() if context.args else ""
    await _handle_slash_dispatch(update, "api", prompt)


async def real_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/real <prompt> — DEPRECATED alias for /api. Logs to cc_logs/_deprecation.log."""
    from workers.conversation_handler import _log_deprecation  # noqa: PLC0415
    _log_deprecation("[DEPRECATED] /real is now /api — please update muscle memory")
    prompt = " ".join(context.args).strip() if context.args else ""
    await _handle_slash_dispatch(update, "api", prompt)


async def local_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/local <prompt> — local build on models.json "code", no API cost."""
    if not is_authorized(update):
        return
    prompt = " ".join(context.args).strip() if context.args else ""
    if not prompt:
        await update.message.reply_text("/local: needs a description.")
        return
    description, target_path, tech = _extract_build_args(prompt)
    short_desc = description if len(description) < 80 else description[:77] + "…"
    from tools.local_builder import _live_model  # noqa: PLC0415
    model = _live_model("code")
    await update.message.reply_text(
        f"🛠️ /local Building: {short_desc}\n"
        f"  tech: {tech} | target: {target_path}\n"
        f"  on the resident brain — typically 30-90s. I'll ping when done."
    )
    asyncio.create_task(
        _build_in_background(update, description, target_path, tech, model=model)
    )


async def quick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/quick <prompt> — qwen3:4b one-shot quick chat (no thinking)."""
    if not is_authorized(update):
        return
    prompt = " ".join(context.args).strip() if context.args else ""
    if not prompt:
        await update.message.reply_text("/quick: needs a question.")
        return
    await update.message.chat.send_action("typing")
    try:
        from workers import conversation_handler as ch  # noqa: PLC0415
        reply = await asyncio.wait_for(
            asyncio.to_thread(ch.quick_chat, prompt), timeout=30,
        )
    except asyncio.TimeoutError:
        await update.message.reply_text(
            "That took over 30 s — the brain's busy. Send it again as a plain message.")
        return
    except Exception as exc:
        logger.exception("/quick failed: %s", exc)
        await update.message.reply_text("That one tripped on the way out — it's logged. Try again?")
        return
    if not reply:
        reply = "(no reply)"
    await _reply_smart(update, reply)


async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/image <prompt> — generate an image locally (sd.cpp on the Vulkan iGPU,
    ~10s) and send it back as a photo."""
    if not is_authorized(update):
        return
    args = list(context.args or [])
    model = "flux"  # default — best quality + real text (~40s)
    if args and args[0].lower() in ("flux", "qwen21", "sdxl", "sd15"):
        model = args.pop(0).lower()
    prompt = " ".join(args).strip()
    if not prompt:
        await update.message.reply_text(
            "/image [flux|qwen21|sdxl|sd15] <prompt>\n"
            "e.g. /image a husky in a santa hat, watercolor  (flux, default)\n"
            "     /image sd15 a quick doodle of a fox        (faster)\n"
            "     /image qwen21 a desk with 6 specific items (~2min, best at\n"
            "                                                busy scenes)")
        return
    await update.message.chat.send_action("upload_photo")
    # qwen21 starts ComfyUI on demand, so it pays a weight load (~2 min all-in).
    wait_s = 420 if model == "qwen21" else 300
    try:
        from tools.image_gen_tool import generate_image_core  # noqa: PLC0415
        res = await asyncio.wait_for(
            asyncio.to_thread(generate_image_core, prompt, model=model), timeout=wait_s)
    except asyncio.TimeoutError:
        await update.message.reply_text(
            f"The image run blew past {wait_s // 60} min — the GPU's probably busy. Try again in a bit.")
        return
    except Exception as exc:
        logger.exception("/image failed: %s", exc)
        await update.message.reply_text("Image gen fell over — it's logged. Try again?")
        return
    if not res.get("ok"):
        await update.message.reply_text(f"/image failed: {res.get('error')}")
        return
    try:
        with open(res["path"], "rb") as fh:
            await update.message.reply_photo(
                fh, caption=f"{prompt[:170]} "
                            f"({res.get('model', model)}, {res['seconds']}s, "
                            f"seed={res['seed']})")
    except Exception as exc:
        await update.message.reply_text(
            f"/image: saved {res['path']} but the send failed: {exc}")


async def computer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/computer <task> — Phase 36 Computer Use agent. Drives the browser
    on Xvfb :99 to do dashboard tasks (Supabase, Vercel, Stripe, ...).
    Hard caps: 30 min, $5. Telegram updates every 30s with screenshots."""
    if not is_authorized(update):
        return
    task = " ".join(context.args).strip() if context.args else ""
    if not task:
        await update.message.reply_text(
            "/computer: needs a task. e.g. /computer resume the bidwatt supabase project"
        )
        return
    unsafe = "--unsafe" in task
    if unsafe:
        task = task.replace("--unsafe", "").strip()
    short = task if len(task) < 80 else task[:77] + "…"
    await update.message.reply_text(
        f"🖥️ /computer running: {short}\n"
        f"  display: :99 | safety: {'OFF (unsafe)' if unsafe else 'on'}\n"
        f"  caps: 30min, $5. I'll ping every 30s with a screenshot."
    )
    asyncio.create_task(_run_computer_in_background(update, task, unsafe))


async def _run_computer_in_background(update: Update, task: str, unsafe: bool) -> None:
    import time as _t  # noqa: PLC0415
    from tools import computer_agent, cu_browser, cu_recorder  # noqa: PLC0415

    bot = update.get_bot()
    chat_id = update.effective_chat.id
    last_update_at = [_t.monotonic()]
    last_iter = [-1]

    def _on_iter(iter_num: int, screenshot_path, model_text: str) -> None:
        # Throttle to ~one update per 30s; always ship the latest screenshot.
        now = _t.monotonic()
        if now - last_update_at[0] < 30 and iter_num != 1:
            return
        last_update_at[0] = now
        last_iter[0] = iter_num
        caption = (model_text or "(working)")[:900]
        try:
            with open(screenshot_path, "rb") as fh:
                asyncio.run_coroutine_threadsafe(
                    bot.send_photo(chat_id=chat_id, photo=fh,
                                   caption=f"iter {iter_num}: {caption}"),
                    asyncio.get_event_loop(),
                )
        except Exception:
            logger.exception("/computer iter update failed")

    try:
        cu_browser.launch("about:blank")
    except Exception as exc:
        await update.message.reply_text(f"⚠️ /computer browser launch failed: {exc}")
        return

    task_id = f"cu_{int(_t.time())}"
    rec = cu_recorder.Recorder((Path.home() / "AI_Agent" / "cu_logs" / task_id / "session.mp4"))
    try:
        rec.start()
        result = await asyncio.to_thread(
            computer_agent.run_task, task,
            task_id=task_id, unsafe=unsafe, on_iteration=_on_iter,
        )
    except Exception as exc:
        logger.exception("/computer crashed")
        await update.message.reply_text(f"⚠️ /computer crashed: {type(exc).__name__}: {exc}")
        return
    finally:
        rec.stop()

    summary = (
        f"🖥️ /computer {result.status}\n"
        f"  iters: {result.iterations} | elapsed: {result.elapsed_seconds:.0f}s | "
        f"cost: ${result.cost_usd:.3f}\n"
        f"  reason: {(result.reason or '')[:300]}\n"
        f"  log: {result.log_dir}"
    )
    if result.halt_reason:
        summary += f"\n  HALT: {result.halt_reason[:200]}"
    await update.message.reply_text(summary)
    if result.final_screenshot and Path(result.final_screenshot).exists():
        try:
            with open(result.final_screenshot, "rb") as fh:
                await bot.send_photo(chat_id=chat_id, photo=fh, caption="final screen")
        except Exception:
            logger.exception("final screenshot send failed")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — every command, generated from SLASH_COMMANDS + the real ladder."""
    if not is_authorized(update):
        return
    await update.message.reply_text(_help_text())


async def _handle_content_command(update: Update, text: str) -> bool:
    """Phase 21 + 27 — short-form content + local build commands.
    Returns True if consumed.

    Shapes:
        script <topic>           — generate script only (fast, ~10-30s)
        create video <topic>     — full pipeline, video sent when done

    Phase 39 — the Phase 27 build-intent regex interception is removed.
    No-slash "build me X / create X" messages flow to route_message,
    where the LLM router dispatches them with the prompt verbatim.
    """
    low = text.strip().lower()

    if low.startswith("script "):
        topic = text.split(None, 1)[1].strip() if " " in text else ""
        if not topic:
            await update.message.reply_text("script: needs a topic.")
            return True
        await update.message.chat.send_action("typing")
        try:
            from tools import script_writer  # noqa: PLC0415
            result = await asyncio.wait_for(
                asyncio.to_thread(script_writer.script_write_core, topic, 30, "energetic"),
                timeout=120,
            )
        except asyncio.TimeoutError:
            await update.message.reply_text("Script generation took >120s. Try again.")
            return True
        except Exception as exc:
            await update.message.reply_text(f"⚠️ script: {type(exc).__name__}: {exc}")
            return True
        body = result.raw_text
        if len(body) > 3500:
            body = body[:3500] + "\n... [truncated, full at " + result.path + "]"
        cost_str = f" | cost ${result.cost_usd:.4f}" if result.cost_usd else " | free (local)"
        await update.message.reply_text(
            f"📝 {result.scene_count} scenes | backend {result.backend}{cost_str}\n\n{body}"
        )
        return True

    if low.startswith("create video ") or low.startswith("video: "):
        if low.startswith("video: "):
            topic = text.split(":", 1)[1].strip()
        else:
            topic = text.split(None, 2)[2].strip() if len(text.split()) >= 3 else ""
        if not topic:
            await update.message.reply_text("create video: needs a topic.")
            return True
        await update.message.reply_text(
            "🎬 Generating script + voiceovers + visuals + final mp4. "
            "Will send the file here when done (~2-5 min)."
        )
        # Run in background so the listener stays responsive.
        asyncio.create_task(_content_create_in_background(update, topic, 30))
        return True

    return False


async def _handle_dispatch_command(update: Update, text: str) -> bool:
    """Phase 22 — handle dispatch-control prefixes BEFORE conversation
    routing. Returns True if the message was consumed.

    Supported shapes (case-insensitive on the leading verb):
        dispatch: <prompt>           — queue a new CC dispatch
        force dispatch: <prompt>     — bypass monthly budget cap
        go cc_xxx                    — release a pending-approval prompt
        cancel cc_xxx                — drop a pending-approval prompt
        queue status                 — current queue snapshot
        restart cc_xxx | nexus-*     — restart services after a dispatch
        retry cc_xxx                 — re-dispatch the original prompt
        extend cc_xxx <minutes>      — re-dispatch with bigger budget
    """
    from core import cc_dispatch as _ccd  # local import: keep listener fast
    low = text.strip().lower()

    if low.startswith("dispatch:") or low.startswith("force dispatch:"):
        forced = low.startswith("force dispatch:")
        prompt = text.split(":", 1)[1].strip()
        if not prompt:
            await update.message.reply_text("dispatch: needs a prompt.")
            return True
        level, spend, budget = _ccd.budget_status()
        if level == "over" and not forced:
            await update.message.reply_text(
                f"Blocked: monthly Claude Code budget exhausted "
                f"(${spend:.2f}/${budget:.2f}). "
                f"Reply with 'force dispatch: ...' to override."
            )
            return True
        risky = _ccd.is_risky(prompt)
        from workers import llm_router as _lr  # noqa: PLC0415
        meta = _ccd.DispatchMeta.new(
            # Phase 39 — token-safe label, no mid-token cuts.
            label=_ccd.safe_label(prompt),
            time_budget_minutes=120,
            risky_match=risky,
            recon_mode=_lr.is_recon(prompt),
        )
        _ccd.write_prompt(meta, prompt, pending=bool(risky))
        snap = _ccd.queue_summary()
        ahead = snap["queued_count"]
        eta = f" — {ahead} ahead" if ahead else ""
        if risky:
            await update.message.reply_text(
                f"🚨 Risky prompt held (matched: {risky}). "
                f"Reply `go {meta.dispatch_id}` to dispatch."
            )
        else:
            await update.message.reply_text(
                f"🚀 Dispatched. id `{meta.dispatch_id}`{eta} "
                f"(budget {meta.time_budget_minutes}m). "
                f"I'll ping when it's done.",
                parse_mode="Markdown",
            )
        return True

    if low.startswith("go cc_"):
        did = text.split(None, 1)[1].strip()
        if _ccd.approve(did):
            await update.message.reply_text(f"✅ Released `{did}` — dispatching now.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"No pending dispatch with id `{did}`.", parse_mode="Markdown")
        return True

    if low.startswith("cancel cc_"):
        did = text.split(None, 1)[1].strip()
        if _ccd.cancel(did):
            await update.message.reply_text(f"🛑 Cancelled `{did}`.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"No dispatch to cancel for `{did}`.", parse_mode="Markdown")
        return True

    if low in ("queue status", "queue", "queue?", "/queue"):
        snap = _ccd.queue_summary()
        lines = []
        if snap["running"]:
            r = snap["running"]
            mins = r["elapsed_seconds"] / 60
            lines.append(f"▶︎ Running: `{r['dispatch_id']}` ({mins:.1f}m elapsed)")
        else:
            lines.append("▶︎ Running: (none)")
        lines.append(f"⏳ Queued: {snap['queued_count']}")
        for q in snap["queued"][:5]:
            lines.append(f"  - `{q['dispatch_id']}`")
        if snap["pending_approval"]:
            lines.append(f"🚨 Pending approval: {len(snap['pending_approval'])}")
            for p in snap["pending_approval"][:5]:
                lines.append(f"  - `{p['dispatch_id']}` (reply `go {p['dispatch_id']}`)")
        level, spend, budget = _ccd.budget_status()
        lines.append(f"💰 Budget: ${spend:.2f}/${budget:.2f} ({level})")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return True

    if low.startswith("restart "):
        target = text.split(None, 1)[1].strip()
        from tools import restart_services_tool  # noqa: PLC0415
        # `restart cc_xxx` → restart the default service set after a dispatch.
        # `restart nexus-foo` (or comma list) → restart specific services.
        if target.startswith("cc_"):
            services = None
        else:
            services = [s for s in (x.strip() for x in target.split(",")) if s]
        out = restart_services_tool.restart_services_sync(services)
        body = "\n".join(f"{'✓' if r['ok'] else '✗'} {r['message']}" for r in out["results"])
        await update.message.reply_text(
            f"Restarted {out['ok']}/{out['total']}:\n{body}"
        )
        return True

    if low.startswith("wiki ") or low == "wiki":
        query = text[len("wiki"):].strip()
        if not query:
            await update.message.reply_text("usage: `wiki <question>`", parse_mode="Markdown")
            return True
        try:
            from tools import wiki_tool  # noqa: PLC0415
            hits = wiki_tool.wiki_query.invoke({"question": query, "k": 3})
        except Exception as e:
            await update.message.reply_text(f"wiki_query error: {type(e).__name__}: {e}")
            return True
        # Telegram caps at 4096 chars; trim long bodies.
        if len(hits) > 3500:
            hits = hits[:3500] + "\n\n…(truncated)"
        await update.message.reply_text(hits)
        return True

    if low.startswith("ingest ") or low == "ingest":
        payload = text[len("ingest"):].strip()
        if not payload:
            await update.message.reply_text(
                "usage: `ingest <url or note>`", parse_mode="Markdown"
            )
            return True
        try:
            from tools import wiki_tool  # noqa: PLC0415
            msg = wiki_tool.wiki_ingest.invoke({
                "source": payload,
                "source_type": "manual",
            })
        except Exception as e:
            await update.message.reply_text(f"wiki_ingest error: {type(e).__name__}: {e}")
            return True
        await update.message.reply_text(f"📥 {msg}")
        return True

    if low.startswith("retry cc_") or low.startswith("extend cc_"):
        is_extend = low.startswith("extend cc_")
        parts = text.split()
        did = parts[1] if len(parts) >= 2 else ""
        new_budget = 240
        if is_extend and len(parts) >= 3:
            try:
                new_budget = max(5, min(int(parts[2]), 480))
            except ValueError:
                pass
        archive_path = _ccd.ARCHIVE / f"{did}.md"
        if not archive_path.exists():
            await update.message.reply_text(f"No archived dispatch `{did}`.", parse_mode="Markdown")
            return True
        meta, body = _ccd.read_prompt(archive_path)
        if not meta or not body:
            await update.message.reply_text(f"Could not parse archived dispatch `{did}`.", parse_mode="Markdown")
            return True
        new_meta = _ccd.DispatchMeta.new(
            label=("re-run: " if not is_extend else f"extend({new_budget}m): ") + meta.label,
            time_budget_minutes=new_budget if is_extend else meta.time_budget_minutes,
        )
        _ccd.write_prompt(new_meta, body, pending=False)
        await update.message.reply_text(
            f"🔁 Re-dispatched as `{new_meta.dispatch_id}` "
            f"(budget {new_meta.time_budget_minutes}m).",
            parse_mode="Markdown",
        )
        return True

    return False


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phase E — vision intake. A photo (or image document) sent to the bot
    is downloaded and run through the local VLM (qwen2.5vl). The caption, if
    any, is treated as the question; otherwise Nexus just describes it. Both
    turns are persisted so follow-up questions have context.

    Before this, the bot had NO photo handler — images were silently dropped.
    """
    if not is_authorized(update):
        return
    msg = update.message
    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id          # largest rendition
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        file_id = msg.document.file_id
    if not file_id:
        return

    caption = (msg.caption or "").strip()
    await msg.chat.send_action("typing")

    import os  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from tools.vision_tool import (  # noqa: PLC0415
        ask_about_image_core, describe_image_core,
    )

    tmp_path = None
    try:
        tg_file = await context.bot.get_file(file_id)
        fd, tmp_path = tempfile.mkstemp(suffix=".jpg", prefix="nexus-tg-img-")
        os.close(fd)
        await tg_file.download_to_drive(tmp_path)
        if caption:
            reply = await asyncio.to_thread(ask_about_image_core, tmp_path, caption)
        else:
            reply = await asyncio.to_thread(describe_image_core, tmp_path)
    except Exception as e:
        logger.warning("handle_photo failed: %s", e)
        reply = f"⚠️ couldn't process that image: {type(e).__name__}: {e}"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    chat_id = update.effective_chat.id
    try:
        from core import telegram_chats as _tcs  # noqa: PLC0415
        _tcs.write_turn(chat_id, "user", (f"[photo] {caption}").strip())
        _tcs.write_turn(chat_id, "assistant", reply)
    except Exception as e:
        logger.warning("telegram_chats photo-write failed: %s", e)

    await _reply_chunked(update, reply)


# Phase 4 (voice) — reply hooks. Each is `async (update, reply_text)`;
# they fire after the final text reply lands (tools/telegram_voice
# appends one that voices the reply when the chat has /voice on).
REPLY_HOOKS: list = []


async def _emit_reply(update: Update, reply: str, on_reply=None) -> None:
    """Fire the per-call `on_reply` (if given) else every REPLY_HOOK.
    Best-effort: a hook failure never loses the text reply."""
    hooks = [on_reply] if on_reply is not None else list(REPLY_HOOKS)
    for hook in hooks:
        try:
            await hook(update, reply)
        except Exception as e:  # noqa: BLE001
            logger.warning("reply hook %s failed: %s", getattr(hook, "__name__", hook), e)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """TEXT messages → the shared text path."""
    await _handle_text(update, context, update.message.text)


async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       user_message: str, on_reply=None) -> None:
    """Route user message through the conversation handler (Phase 15.5).

    The handler runs on qwen3:4b only and decides — via its own tool calls
    — whether to answer from queue state, modify a running task, or
    enqueue a new heavy task for the task_worker. Heavy turns NEVER run
    in this request; the bot replies fast (<10s) and the worker streams
    progress to memory/active_tasks.jsonl independently.

    `user_message` is passed explicitly so non-text intakes (voice notes
    transcribed by tools/telegram_voice) share this exact path. `on_reply`
    overrides REPLY_HOOKS for this one turn (see _emit_reply)."""
    if not is_authorized(update):
        return

    logger.info("Received message: %s", user_message[:100])
    await update.message.chat.send_action("typing")

    # Phase 22 dispatch shortcuts run BEFORE the LLM router so they're
    # deterministic and never blocked on Ollama.
    if await _handle_content_command(update, user_message):
        return

    if await _handle_dispatch_command(update, user_message):
        return

    chat_id = update.effective_chat.id
    # Phase 38: log every inbound user turn before routing. write_turn
    # is best-effort and never raises — a DB hiccup can't block routing.
    try:
        from core import telegram_chats as _tcs
        _tcs.write_turn(chat_id, "user", user_message)
    except Exception as e:
        logger.warning("telegram_chats user-write failed: %s", e)

    from workers import conversation_handler
    from tools.telegram_progress import ProgressBubble  # noqa: PLC0415

    # ONE bubble per message, sent immediately; every stage edits it and
    # the final reply replaces it.
    bubble = await ProgressBubble(update).start()
    loop = asyncio.get_running_loop()

    def _progress_cb(text: str) -> None:  # called from the worker thread
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(bubble.stage(text)))

    # Streaming for pure chat turns. classify_intent is the cheap
    # deterministic gate (queue:/status/cancel/list bypass it); the LLM
    # router then confirms quick_chat (not a build/task) before we stream
    # sentences into the bubble. Any failure falls through to the blocking
    # router path so the reply is never dropped or delayed.
    streamed_reply: str | None = None
    decision: dict | None = None  # router result, reused below (route ONCE)
    try:
        intent = conversation_handler.classify_intent(user_message)
        if intent.get("kind") == "chat":
            if conversation_handler._is_obvious_chat(user_message):
                decision = {"route": "quick_chat", "tier": None,
                            "recon_mode": False, "router_skipped": True}
            else:
                await bubble.stage("🧭 routing")
                from workers import llm_router
                decision = await asyncio.to_thread(llm_router.route_llm, user_message)
            if decision.get("route") == "quick_chat":
                await bubble.stage("✍️ writing")
                streamed_reply = await _stream_quick_chat_reply(
                    update, user_message, chat_id, bubble)
    except Exception as e:
        logger.warning("stream path failed (%s) — falling back to router", e)
        streamed_reply = None

    if streamed_reply is not None:
        logger.info("route kind=chat chat_id=%s (streamed)", chat_id)
        _after_chat_turn(chat_id, user_message, streamed_reply)
        await _emit_reply(update, streamed_reply, on_reply)
        return

    # Blocking router off the event loop. The thread can't be cancelled,
    # so on timeout we say so, free this handler, and deliver when it lands.
    fut = asyncio.ensure_future(asyncio.to_thread(
        conversation_handler.route_message, user_message, chat_id, decision,
        _progress_cb))
    try:
        result = await asyncio.wait_for(asyncio.shield(fut), timeout=ROUTE_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning("route_message >%ss for chat_id=%s — delivering late",
                       ROUTE_TIMEOUT_S, chat_id)
        await bubble.stage(
            "⏳ still on it — this one's taking longer than a minute. "
            "I'll drop the answer here when it lands.")
        asyncio.create_task(_deliver_late(update, bubble, fut, chat_id, user_message,
                                          on_reply))
        return
    except Exception as e:
        logger.exception("conversation handler error: %s", e)
        await bubble.finish("That one tripped on the way out — it's logged. Try again?")
        return

    await _deliver_result(update, bubble, result, chat_id, user_message, on_reply)


async def _deliver_late(update: Update, bubble, fut: "asyncio.Future",
                        chat_id: int, user_message: str, on_reply=None) -> None:
    try:
        result = await fut
    except Exception as e:
        logger.exception("late route_message failed: %s", e)
        await bubble.finish("That one died on the way back — it's logged. Try again?")
        return
    await _deliver_result(update, bubble, result, chat_id, user_message, on_reply)


async def _deliver_result(update: Update, bubble, result: dict,
                          chat_id: int, user_message: str, on_reply=None) -> None:
    reply = result.get("reply", "") or "Came back empty — say that again?"
    logger.info("route kind=%s chat_id=%s", result.get("kind"), chat_id)
    task_id = (result.get("meta") or {}).get("task_id")
    if result.get("kind") == "task" and task_id:
        # Hand the bubble to the task worker: it keeps editing this same
        # message (tool lines, heartbeats) and replaces it with the result.
        from workers import task_progress  # noqa: PLC0415
        title = task_progress.derive_title(user_message)
        handoff = await bubble.handoff(title)
        if handoff:
            try:
                task_progress.write_handoff(task_id, title=title, **handoff)
                reply = title
            except OSError as e:
                logger.warning("task handoff write failed: %s", e)
                await bubble.finish(reply)
        else:
            await bubble.finish(reply)
        try:
            from core import telegram_chats as _tcs
            # History must read as STATE, not as a standing promise: the
            # brain otherwise re-reads "On it — I'll ping you" forever and
            # keeps promising (observed 2026-09-12). The worker appends a
            # matching "[task … finished]" turn when it completes.
            _tcs.write_turn(chat_id, "assistant",
                            f"[queued task {task_id}: {user_message[:140]} — "
                            "the result is posted separately when it finishes]")
        except Exception as e:
            logger.warning("telegram_chats assistant-write failed: %s", e)
        await _emit_reply(update, reply, on_reply)
        return
    if result.get("kind") == "chat":
        _after_chat_turn(chat_id, user_message, reply)
    else:
        try:
            from core import telegram_chats as _tcs
            _tcs.write_turn(chat_id, "assistant", reply)
        except Exception as e:
            logger.warning("telegram_chats assistant-write failed: %s", e)
    await _finish_bubble(update, bubble, reply)
    await _emit_reply(update, reply, on_reply)


def _after_chat_turn(chat_id: int, user_message: str, reply: str) -> None:
    """Persist the assistant turn + rate-limited background reflection."""
    try:
        from core import telegram_chats as _tcs
        _tcs.write_turn(chat_id, "assistant", reply)
    except Exception as e:
        logger.warning("telegram_chats assistant-write failed: %s", e)
    try:
        from workers import conversation_handler
        conversation_handler.maybe_reflect(chat_id, user_message, reply)
    except Exception as e:
        logger.debug("reflection spawn failed: %s", e)



# Menu shown by Telegram when the user types "/". Ordered by how often
# Colton reaches for them; descriptions are what the popup displays.
COMMAND_MENU: list[tuple[str, str]] = [
    ("screenshot", "What Nexus sees on its desktop right now"),
    ("desktop", "Do something on the desktop: /desktop open x.com and …"),
    ("open", "Open a URL on the desktop and screenshot it"),
    ("watch", "VNC connect string to watch the desktop live"),
    ("local", "Build it on the resident brain (default for builds)"),
    ("quick", "One-shot fast answer, no tools"),
    ("new", "Fresh conversation — forget the recent thread"),
    ("think", "on|off — show my reasoning under replies"),
    ("voice", "on|off — reply with a voice note too"),
    ("status", "Queue + system status"),
    ("tasks", "Recent tasks"),
    ("stop", "Cancel the running task"),
    ("image", "Generate an image locally"),
    ("creds", "SaaS credential status / setup"),
    ("help", "Every command with details"),
    ("max", "Cloud build via Claude Max (explicit only)"),
    ("code", "Cloud build via DeepSeek Flash (explicit only, ~$0.005)"),
    ("pro", "Cloud build via DeepSeek Pro (explicit only)"),
    ("api", "Cloud build via Claude API (explicit only, real $$)"),
    ("computer", "Cloud computer-use agent on the desktop (explicit only)"),
]


async def _register_command_menu(application) -> None:
    try:
        await application.bot.set_my_commands(
            [BotCommand(c, d[:256]) for c, d in COMMAND_MENU])
        logger.info("command menu registered: %d commands", len(COMMAND_MENU))
    except Exception as e:  # never block startup on this
        logger.warning("set_my_commands failed: %s", e)


def main() -> None:
    """Start the Telegram bot."""
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        print("Add TELEGRAM_BOT_TOKEN=your_token to ~/AI_Agent/.env")
        sys.exit(1)

    # Phase 38: eager-init the conversation buffer so memory/telegram_chats.db
    # exists immediately on startup (idempotent). Without this the file is
    # lazy-created on first message, which makes ops verification harder.
    try:
        from core import telegram_chats as _tcs
        _tcs.init()
    except Exception as e:
        logger.warning("telegram_chats init failed: %s", e)

    # Create the Application. post_init registers the command menu with
    # Telegram (setMyCommands) so typing "/" pops the list on the phone —
    # without it the client shows nothing.
    application = (Application.builder().token(TELEGRAM_BOT_TOKEN)
                   .post_init(_register_command_menu).build())

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("tasks", tasks_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("help", help_command))
    # Phase 28 — slash commands for tier-aware Claude Code routing.
    # Phase 29 added /max (default) + /api (renamed from /real).
    application.add_handler(CommandHandler("max", max_command))
    application.add_handler(CommandHandler("code", code_command))
    application.add_handler(CommandHandler("pro", pro_command))
    application.add_handler(CommandHandler("api", api_command))
    application.add_handler(CommandHandler("real", real_command))  # deprecated alias
    application.add_handler(CommandHandler("local", local_command))
    application.add_handler(CommandHandler("quick", quick_command))
    # Phase 36 — Computer Use agent (Anthropic native, drives :99 browser).
    application.add_handler(CommandHandler("computer", computer_command))
    application.add_handler(CommandHandler("image", image_command))  # local SD gen
    application.add_handler(CommandHandler("think", think_command))  # show-your-work toggle
    application.add_handler(CommandHandler("new", new_command))  # fresh conversation
    application.add_handler(CommandHandler("creds", creds_command))  # Phase 33 helper
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Phase E — vision intake: photos + image documents → local VLM describe.
    application.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))
    # /screenshot + /desktop live in tools/telegram_desktop (owned elsewhere).
    try:
        from tools import telegram_desktop  # noqa: PLC0415
        telegram_desktop.register(application)
    except Exception as e:
        logger.warning("telegram_desktop not registered: %s", e)
    # Phase 4 — voice notes in (whisper) + /voice on|off spoken replies
    # (Kokoro). Registered AFTER the TEXT handler so text keeps priority.
    try:
        from tools import telegram_voice  # noqa: PLC0415
        telegram_voice.register(application)
    except Exception as e:
        logger.warning("telegram_voice not registered: %s", e)

    # Start the bot
    logger.info("Starting Telegram listener...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

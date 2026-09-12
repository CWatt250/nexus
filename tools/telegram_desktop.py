"""Phase 3 — Telegram handlers for Nexus's headless desktop (:99).

    /screenshot        fresh downscaled screenshot + active window title
    /watch             VNC connect string (password lives in ~/.vnc, never posted)
    /open <url>        navigate Chrome on :99, then screenshot
    /desktop <task>    run tools.desktop_agent.run_desktop_task with live
                       per-step photos (≤1 photo / 2 s); add --approved to
                       allow typing into password/billing fields

Wire with `tools.telegram_desktop.register(app)` from the listener.
Authorization reuses telegram_listener.is_authorized (imported lazily to
avoid a circular import at module load).
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

log = logging.getLogger("nexus.telegram_desktop")

PROGRESS_MIN_INTERVAL_S = 2.0


def _authorized(update: Update) -> bool:
    try:
        from tools.telegram_listener import is_authorized  # noqa: PLC0415
        return is_authorized(update)
    except Exception as exc:  # noqa: BLE001 — listener not importable (tests)
        log.warning("is_authorized unavailable (%s) — allowing", exc)
        return True


async def _send_shot(update: Update, caption: str) -> None:
    from tools import desktop  # noqa: PLC0415
    try:
        _, small = await asyncio.to_thread(desktop.screenshot)
        title = await asyncio.to_thread(desktop.active_window_title)
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"screenshot failed: {type(exc).__name__}: {exc}")
        return
    cap = f"{caption}\n{title or '(no active window)'} · {time.strftime('%H:%M:%S')}"
    with open(small, "rb") as fh:
        await update.message.reply_photo(fh, caption=cap[:1000])


async def screenshot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await _send_shot(update, "🖥️ :99")


async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    from tools import desktop  # noqa: PLC0415
    await update.message.reply_text(
        f"👀 VNC: {desktop.VNC_CONNECT} (tailnet only)\n"
        f"password: `cat {desktop.VNC_PASSWORD_FILE}` on WattBott — not posted here.\n"
        "Needs nexus-vnc restarted with the Phase 3 drop-in (scripts/sudo_phase3.sh)."
    )


async def open_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    url = " ".join(context.args).strip() if context.args else ""
    if not url:
        await update.message.reply_text("/open: needs a URL. e.g. /open github.com")
        return
    from tools import desktop  # noqa: PLC0415
    try:
        status = await asyncio.to_thread(desktop.open_url, url)
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"open failed: {type(exc).__name__}: {exc}")
        return
    await asyncio.sleep(2.5)  # let the page paint
    await _send_shot(update, f"🌐 {status}")


async def desktop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    task = " ".join(context.args).strip() if context.args else ""
    approved = "--approved" in task
    task = task.replace("--approved", "").strip()
    if not task:
        await update.message.reply_text(
            "/desktop: needs a task. e.g. /desktop open github.com and search for langgraph"
        )
        return
    short = task if len(task) < 80 else task[:77] + "…"
    await update.message.reply_text(
        f"🖥️ /desktop: {short}\n  local brain on :99 · max 15 steps"
        f"{' · approved for sensitive fields' if approved else ''}"
    )
    asyncio.create_task(_run_desktop_in_background(update, task, approved))


async def _run_desktop_in_background(update: Update, task: str, approved: bool) -> None:
    from tools.desktop_agent import run_desktop_task  # noqa: PLC0415

    loop = asyncio.get_running_loop()
    last_sent = [0.0]

    def _progress(step: int, thought: str, action: str, small_jpg: str) -> None:
        now = time.monotonic()
        if step != 1 and now - last_sent[0] < PROGRESS_MIN_INTERVAL_S:
            return
        last_sent[0] = now
        cap = f"step {step}: {action}\n💭 {thought}"[:1000]

        async def _send() -> None:
            try:
                with open(small_jpg, "rb") as fh:
                    await update.message.reply_photo(fh, caption=cap)
            except Exception as exc:  # noqa: BLE001
                log.warning("progress photo failed: %s", exc)

        asyncio.run_coroutine_threadsafe(_send(), loop)

    try:
        res = await asyncio.to_thread(run_desktop_task, task, 15, _progress, False, approved)
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"/desktop crashed: {type(exc).__name__}: {exc}")
        return

    if res.get("needs_confirm"):
        text = (f"⏸️ paused: {res['needs_confirm']}\n"
                f"re-run with: /desktop {task} --approved")
    else:
        text = (f"{'✅' if res['status'] == 'done' else '⚠️'} {res['status']} "
                f"in {len(res.get('steps', []))} steps / {res.get('elapsed_s', '?')}s\n{res.get('summary', '')}")
    final = res.get("final_screenshot")
    if final and Path(final).exists():
        with open(final, "rb") as fh:
            await update.message.reply_photo(fh, caption=text[:1000])
    else:
        await update.message.reply_text(text[:4000])


def register(app) -> None:
    """Add the desktop command handlers to a python-telegram-bot Application."""
    app.add_handler(CommandHandler("screenshot", screenshot_command))
    app.add_handler(CommandHandler("watch", watch_command))
    app.add_handler(CommandHandler("open", open_command))
    app.add_handler(CommandHandler("desktop", desktop_command))
    log.info("telegram_desktop: registered /screenshot /watch /open /desktop")

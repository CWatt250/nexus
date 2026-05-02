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
from telegram import Update
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


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "Hey Colton! Sparky here.\n\n"
        "Send me any message and I'll route it to Nexus.\n\n"
        "Commands:\n"
        "/status - Check Nexus status\n"
        "/tasks - List current tasks\n"
        "/stop - Stop current task\n"
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
                await update.message.reply_text(f"Nexus Status: {data.get('status', 'unknown')}")
            else:
                await update.message.reply_text(f"Nexus returned {response.status_code}")
    except Exception as e:
        await update.message.reply_text(f"Could not reach Nexus API: {e}")


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
        await update.message.reply_text(f"Error: {type(e).__name__}: {e}")


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
        )
        final_path = info["final_video_path"]
        # Send the final mp4 as a Telegram video. python-telegram-bot's
        # send_video accepts a file path via open().
        bot = update.get_bot()
        try:
            with open(final_path, "rb") as fh:
                await bot.send_video(
                    chat_id=chat_id,
                    video=fh,
                    caption=(
                        f"🎬 {Path(final_path).name}\n"
                        f"scenes: {info['scene_clips_built']} | "
                        f"actual: {info['duration_actual_seconds']:.1f}s | "
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


async def _handle_content_command(update: Update, text: str) -> bool:
    """Phase 21 — short-form content commands. Returns True if consumed.

    Shapes:
        script <topic>           — generate script only (fast, ~10-30s)
        create video <topic>     — full pipeline, video sent when done
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
        meta = _ccd.DispatchMeta.new(
            label=prompt.splitlines()[0][:60],
            time_budget_minutes=120,
            risky_match=risky,
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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route user message through the conversation handler (Phase 15.5).

    The handler runs on qwen3:4b only and decides — via its own tool calls
    — whether to answer from queue state, modify a running task, or
    enqueue a new heavy task for the task_worker. Heavy turns NEVER run
    in this request; the bot replies fast (<10s) and the worker streams
    progress to memory/active_tasks.jsonl independently."""
    if not is_authorized(update):
        return

    user_message = update.message.text
    logger.info("Received message: %s", user_message[:100])
    await update.message.chat.send_action("typing")

    # Phase 22 dispatch shortcuts run BEFORE the LLM router so they're
    # deterministic and never blocked on Ollama.
    if await _handle_content_command(update, user_message):
        return

    if await _handle_dispatch_command(update, user_message):
        return

    chat_id = update.effective_chat.id
    try:
        from workers import conversation_handler
        # New 4-way LLM router: CHAT/QUERY -> qwen3.6 inline reply,
        # TASK -> enqueue, STATUS -> task lookup. "queue: <text>" remains
        # a power-user prefix that bypasses classification. Run the
        # blocking router off the event loop so the bot stays responsive.
        result = await asyncio.wait_for(
            asyncio.to_thread(conversation_handler.route_message, user_message),
            timeout=25,
        )
        reply = result.get("reply", "")
        logger.info("route kind=%s chat_id=%s", result.get("kind"), chat_id)
    except asyncio.TimeoutError:
        await update.message.reply_text(
            "Took >25s to route — Ollama may be busy. Try again, or send "
            "'queue: <task>' to bypass classification."
        )
        return
    except Exception as e:
        logger.exception("conversation handler error: %s", e)
        await update.message.reply_text(f"handler error: {type(e).__name__}: {e}")
        return

    if not reply:
        reply = "(handler returned no text — try again)"
    if len(reply) > 4000:
        reply = reply[:4000] + "\n... [truncated]"
    await update.message.reply_text(reply)


def main() -> None:
    """Start the Telegram bot."""
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        print("Add TELEGRAM_BOT_TOKEN=your_token to ~/AI_Agent/.env")
        sys.exit(1)

    # Create the Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("tasks", tasks_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the bot
    logger.info("Starting Telegram listener...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

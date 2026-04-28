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

    chat_id = update.effective_chat.id
    thread_id = f"handler:tg:{chat_id}"
    try:
        from workers import conversation_handler
        reply = await asyncio.wait_for(
            conversation_handler.handle_async(user_message, thread_id=thread_id),
            timeout=20,
        )
    except asyncio.TimeoutError:
        await update.message.reply_text(
            "Took >20s — the handler is supposed to be fast. Falling back to queue:\n"
            "use /tasks to inspect or send 'queue: <task>' to enqueue."
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

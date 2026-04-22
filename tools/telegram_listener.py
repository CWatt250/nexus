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
    """Handle /tasks command."""
    if not is_authorized(update):
        return
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{NEXUS_API_URL}/tasks", timeout=10)
            if response.status_code == 200:
                data = response.json()
                tasks = data.get("tasks", [])
                if tasks:
                    task_list = "\n".join(f"- {t}" for t in tasks[:10])
                    await update.message.reply_text(f"Current tasks:\n{task_list}")
                else:
                    await update.message.reply_text("No active tasks.")
            else:
                await update.message.reply_text("Could not fetch tasks.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop command."""
    if not is_authorized(update):
        return
    await update.message.reply_text("Stop command received. (Not yet implemented)")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular text messages — route to Nexus."""
    if not is_authorized(update):
        return

    user_message = update.message.text
    logger.info(f"Received message: {user_message[:100]}")

    # Send typing indicator
    await update.message.chat.send_action("typing")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{NEXUS_API_URL}/chat",
                json={"message": user_message},
                timeout=120,
            )
            if response.status_code == 200:
                data = response.json()
                reply = data.get("response", "No response from Nexus")
                # Truncate if too long for Telegram (4096 chars max)
                if len(reply) > 4000:
                    reply = reply[:4000] + "\n... [truncated]"
                await update.message.reply_text(reply)
            else:
                await update.message.reply_text(f"Nexus error: {response.status_code}")
    except httpx.TimeoutException:
        await update.message.reply_text("Request timed out. Nexus might be processing a long task.")
    except Exception as e:
        logger.error(f"Error routing to Nexus: {e}")
        await update.message.reply_text(f"Error: {type(e).__name__}: {e}")


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

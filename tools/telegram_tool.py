"""Telegram Bot Tool for Nexus agent notifications and commands."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langchain_core.tools import tool

# Load environment variables
load_dotenv(Path.home() / "AI_Agent" / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _get_bot():
    """Lazy load telegram bot to avoid import errors when not configured."""
    if not TELEGRAM_BOT_TOKEN:
        return None
    from telegram import Bot
    return Bot(token=TELEGRAM_BOT_TOKEN)


async def _send_message_async(message: str) -> str:
    """Send message asynchronously."""
    bot = _get_bot()
    if bot is None:
        return "Error: TELEGRAM_BOT_TOKEN not configured. Add it to ~/AI_Agent/.env"
    if not TELEGRAM_CHAT_ID:
        return "Error: TELEGRAM_CHAT_ID not configured. Add it to ~/AI_Agent/.env"

    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="Markdown")
        return f"Message sent to Telegram successfully"
    except Exception as e:
        return f"Error sending Telegram message: {type(e).__name__}: {e}"


async def _send_file_async(file_path: str, caption: str = "") -> str:
    """Send file asynchronously."""
    bot = _get_bot()
    if bot is None:
        return "Error: TELEGRAM_BOT_TOKEN not configured. Add it to ~/AI_Agent/.env"
    if not TELEGRAM_CHAT_ID:
        return "Error: TELEGRAM_CHAT_ID not configured. Add it to ~/AI_Agent/.env"

    path = Path(file_path)
    if not path.exists():
        return f"Error: File not found: {file_path}"

    try:
        with open(path, "rb") as f:
            await bot.send_document(chat_id=TELEGRAM_CHAT_ID, document=f, caption=caption)
        return f"File sent to Telegram successfully: {path.name}"
    except Exception as e:
        return f"Error sending Telegram file: {type(e).__name__}: {e}"


def _run_async(coro):
    """Run async function in sync context."""
    try:
        loop = asyncio.get_running_loop()
        # If we're in an async context, create a task
        return asyncio.ensure_future(coro)
    except RuntimeError:
        # No running loop, create one
        return asyncio.run(coro)


async def proactive_send(message: str) -> str:
    """Async-safe proactive Telegram send for use from workers / agents.

    Returns the same kinds of strings as the @tool wrappers but never
    raises, so callers can fire-and-forget without try/except. Skips
    silently when the bot isn't configured (Phase 16.1 expectation:
    proactive notifications are best-effort)."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return "telegram not configured — skipped"
    try:
        return await _send_message_async(message)
    except Exception as exc:
        return f"telegram send failed: {type(exc).__name__}: {exc}"


@tool
def telegram_notify(message: str) -> str:
    """Send a notification message to Colton's Telegram.

    Args:
        message: The message to send (supports Markdown formatting)

    Returns:
        Success or error message
    """
    return _run_async(_send_message_async(message))


@tool
def telegram_send_file(file_path: str, caption: str = "") -> str:
    """Send a file to Colton's Telegram with an optional caption.

    Args:
        file_path: Path to the file to send
        caption: Optional caption for the file

    Returns:
        Success or error message
    """
    return _run_async(_send_file_async(file_path, caption))


# Synchronous versions for direct use (not as LangGraph tools)
def notify_sync(message: str) -> str:
    """Synchronous version of telegram_notify for direct use."""
    return asyncio.run(_send_message_async(message))


def send_file_sync(file_path: str, caption: str = "") -> str:
    """Synchronous version of telegram_send_file for direct use."""
    return asyncio.run(_send_file_async(file_path, caption))


# Event notification helpers
def notify_task_complete(task_name: str, duration_seconds: Optional[float] = None) -> str:
    """Notify when a long task completes."""
    msg = f"Task completed: *{task_name}*"
    if duration_seconds:
        minutes = duration_seconds / 60
        msg += f" (took {minutes:.1f} min)"
    return notify_sync(msg)


def notify_service_crash(service_name: str, error: str = "") -> str:
    """Notify when a service crashes and restarts."""
    msg = f"Service crashed: *{service_name}*"
    if error:
        msg += f"\nError: `{error[:200]}`"
    return notify_sync(msg)


def notify_github_pr(repo: str, pr_number: int, title: str) -> str:
    """Notify when a new GitHub PR is opened."""
    return notify_sync(f"New PR opened: *{repo}* #{pr_number}\n{title}")


def notify_error(context: str, error: str) -> str:
    """Notify when an error occurs that needs attention."""
    return notify_sync(f"Error in *{context}*:\n`{error[:500]}`")


def notify_sudo_needed(commands: list[str]) -> str:
    """Notify when sudo commands need to be run manually."""
    cmd_list = "\n".join(f"• `{cmd}`" for cmd in commands[:10])
    return notify_sync(f"Sudo commands need to be run:\n{cmd_list}")


# Export for easy import
TELEGRAM_TOOLS = [telegram_notify, telegram_send_file]

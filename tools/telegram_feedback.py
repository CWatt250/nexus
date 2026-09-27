"""👍/👎 feedback capture — the training data for the brain (step 6).

Every finished Telegram reply is logged as a turn; a reaction on any Nexus
message is logged as a rating. No buttons: long-press a reply, tap 👍 or 👎.

Matching a reaction to its turn: message ids in a private chat are one
sequence shared by both sides, so a reaction on bot message M belongs to
the latest turn whose *user* message id is below M (the reply — or every
chunk of a long reply — comes after the question that caused it).

Files (gitignored, local only):
  memory/feedback/turns.jsonl    {ts, chat_id, user_msg_id, prompt, reply}
  memory/feedback/ratings.jsonl  {ts, chat_id, message_id, rating: +1|-1|0, emoji}

`training/export_feedback.py` joins them into a KTO dataset.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from telegram import ReactionTypeEmoji, Update
from telegram.ext import ContextTypes, MessageReactionHandler

log = logging.getLogger("nexus.telegram_feedback")

FEEDBACK_DIR = Path.home() / "AI_Agent" / "memory" / "feedback"
TURNS = FEEDBACK_DIR / "turns.jsonl"
RATINGS = FEEDBACK_DIR / "ratings.jsonl"

GOOD = {"👍", "❤", "❤️", "🔥", "🥰", "👏", "💯", "🎉", "🤩", "⚡", "🏆", "😍"}
BAD = {"👎", "💩", "🤮", "😡", "🤬", "🥱", "🤡", "😢", "🤔"}


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def score(emojis: list[str]) -> int:
    """+1 / -1 / 0 for a set of reactions. Empty (reaction removed) → 0."""
    if any(e in BAD for e in emojis):
        return -1
    if any(e in GOOD for e in emojis):
        return 1
    return 0


async def log_turn_hook(update: Update, reply: str) -> None:
    """REPLY_HOOK: record the question and the reply Nexus gave."""
    msg = update.effective_message
    if msg is None or not reply:
        return
    _append(TURNS, {
        "ts": time.time(), "chat_id": update.effective_chat.id,
        "user_msg_id": msg.message_id, "prompt": msg.text or msg.caption or "",
        "reply": reply,
    })


async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    r = update.message_reaction
    if r is None:
        return
    from tools.telegram_listener import is_authorized  # noqa: PLC0415
    if not is_authorized(update):
        return
    emojis = [x.emoji for x in r.new_reaction if isinstance(x, ReactionTypeEmoji)]
    _append(RATINGS, {
        "ts": time.time(), "chat_id": r.chat.id, "message_id": r.message_id,
        "rating": score(emojis), "emoji": "".join(emojis),
    })


def register(app) -> None:
    app.add_handler(MessageReactionHandler(handle_reaction))
    from tools import telegram_listener as tl  # noqa: PLC0415
    if log_turn_hook not in tl.REPLY_HOOKS:
        tl.REPLY_HOOKS.append(log_turn_hook)
    log.info("telegram_feedback: logging turns + reactions to %s", FEEDBACK_DIR)

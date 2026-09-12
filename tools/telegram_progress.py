"""Telegram progress bubble — ONE placeholder message, edited in place.

Every non-slash message gets a placeholder within ~300 ms ("🧠 …"), the
placeholder is edited as stages happen ("🔎 looking that up", "🔧
web_search", streamed sentences…), and the final reply REPLACES the
bubble via edit_message_text. Long finals edit the bubble to the first
chunk and send the rest as follow-ups.

Rules baked in:
  - edits are throttled to ≥ EDIT_MIN_INTERVAL_S apart (Telegram rate
    limits); a stage that arrives too early is queued and flushed once
    the window opens — only the latest text is kept.
  - "message is not modified" errors are swallowed.
  - send_action("typing") is re-sent every TYPING_INTERVAL_S while work
    is in flight.
Everything is best-effort: a bubble that failed to send degrades to plain
replies so the answer is never lost.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

log = logging.getLogger("nexus.telegram_progress")

PLACEHOLDER = "🧠 …"
EDIT_MIN_INTERVAL_S = 1.0
TYPING_INTERVAL_S = 4.0
CHUNK_SEND_DELAY_S = 0.4
STAGE_MAX_CHARS = 4000


def _chunk(text: str) -> list[str]:
    try:
        from core.telegram_chunk import chunk_text  # noqa: PLC0415
        return chunk_text(text) or ["(empty reply)"]
    except Exception:  # pragma: no cover — chunker missing
        return [text[i:i + STAGE_MAX_CHARS] for i in range(0, max(len(text), 1), STAGE_MAX_CHARS)]


class ProgressBubble:
    def __init__(self, update, *, placeholder: str = PLACEHOLDER):
        self._update = update
        self._bot = update.get_bot()
        self._chat_id = update.effective_chat.id
        self._placeholder = placeholder
        self._msg = None
        self._last_edit = 0.0
        self._last_text = ""
        self._pending: Optional[str] = None
        self._flusher: Optional[asyncio.Task] = None
        self._typing: Optional[asyncio.Task] = None
        self._done = False

    # ── lifecycle ────────────────────────────────────────────────────
    async def start(self) -> "ProgressBubble":
        try:
            self._msg = await self._update.message.reply_text(self._placeholder)
            self._last_text = self._placeholder
            self._last_edit = time.monotonic()
        except Exception as exc:
            log.warning("progress bubble send failed: %s", exc)
            self._msg = None
        self._typing = asyncio.create_task(self._typing_loop())
        return self

    async def _typing_loop(self) -> None:
        try:
            while not self._done:
                try:
                    await self._update.message.chat.send_action("typing")
                except Exception:
                    pass
                await asyncio.sleep(TYPING_INTERVAL_S)
        except asyncio.CancelledError:
            pass

    def _stop_background(self) -> None:
        self._done = True
        for t in (self._flusher, self._typing):
            if t is not None and not t.done():
                t.cancel()

    # ── stages ───────────────────────────────────────────────────────
    async def stage(self, text: str) -> None:
        """Edit the bubble to `text` (throttled). Silent no-op if the
        bubble never sent or is already finished."""
        if self._done or self._msg is None or not text:
            return
        self._pending = text[:STAGE_MAX_CHARS]
        wait = EDIT_MIN_INTERVAL_S - (time.monotonic() - self._last_edit)
        if wait <= 0:
            await self._flush()
        elif self._flusher is None or self._flusher.done():
            self._flusher = asyncio.create_task(self._delayed_flush(wait))

    async def _delayed_flush(self, wait: float) -> None:
        try:
            await asyncio.sleep(wait)
            if not self._done:
                await self._flush()
        except asyncio.CancelledError:
            pass

    async def _flush(self) -> None:
        text, self._pending = self._pending, None
        if not text or text == self._last_text:
            return
        await self._edit(text)

    async def _edit(self, text: str) -> bool:
        """edit_message_text with not-modified swallowed. False on a real
        failure so finish() can fall back to a fresh send."""
        self._last_edit = time.monotonic()
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id, message_id=self._msg.message_id, text=text)
            self._last_text = text
            return True
        except Exception as exc:
            if "not modified" in str(exc).lower():
                self._last_text = text
                return True
            log.info("progress edit failed: %s", exc)
            return False

    # ── finish ───────────────────────────────────────────────────────
    async def finish(self, final_text: str) -> None:
        """Replace the bubble with the final reply. If the final needs
        chunking, the bubble becomes chunk 1 and the rest follow."""
        self._stop_background()
        chunks = _chunk(final_text or "")
        first, rest = chunks[0], chunks[1:]
        edited = self._msg is not None and await self._edit(first)
        if not edited:
            await self._send(first)
        for c in rest:
            await asyncio.sleep(CHUNK_SEND_DELAY_S)
            await self._send(c)

    async def replace_with(self, sender: Callable[[str], Awaitable[None]],
                           final_text: str) -> None:
        """Send the final through `sender` (e.g. a rich-markdown send) and
        delete the bubble. Raises if `sender` raises — the caller falls
        back to finish()."""
        await sender(final_text)
        self._stop_background()
        await self.delete()

    async def delete(self) -> None:
        if self._msg is None:
            return
        try:
            await self._bot.delete_message(chat_id=self._chat_id,
                                           message_id=self._msg.message_id)
        except Exception as exc:
            log.info("progress bubble delete failed: %s", exc)
        self._msg = None

    async def _send(self, text: str) -> None:
        try:
            await self._update.message.reply_text(text)
        except Exception as exc:
            log.warning("progress final send failed: %s", exc)

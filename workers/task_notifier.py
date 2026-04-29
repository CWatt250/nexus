"""Task lifecycle notifier — every TASK enqueue ends with a Telegram message.

Single source of truth for the notification format the user actually
sees. The worker calls into this on every terminal lifecycle event
(done / failed / cancelled / timed_out) and on heartbeats for
long-running tasks. Formatting + chunking + retry-hint live here so the
worker stays focused on agent execution.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("nexus.task_notifier")

# Telegram's hard limit is 4096 chars. We split bodies at 3000 to leave
# room for the header + safety margin and to match user spec.
CHUNK_BODY_CHARS = 3000


def _chunks(body: str, size: int = CHUNK_BODY_CHARS) -> list[str]:
    if not body:
        return [""]
    if len(body) <= size:
        return [body]
    out: list[str] = []
    i = 0
    while i < len(body):
        out.append(body[i:i + size])
        i += size
    return out


async def _send(text: str) -> None:
    """Best-effort Telegram send. Tries Markdown first, falls back to
    plain text if Telegram rejects the formatting (which happens when
    agent output contains stray asterisks / underscores)."""
    try:
        from tools.telegram_tool import _send_message_async  # noqa: PLC0415
    except Exception as exc:
        log.warning("telegram import failed: %s", exc)
        return
    result = await _send_message_async(text)
    if result.startswith("Error"):
        # Markdown parse failures: retry without parse_mode by sending
        # via the bot directly. Cheap path — only on first-attempt error.
        try:
            from tools.telegram_tool import _get_bot, TELEGRAM_CHAT_ID  # noqa: PLC0415
            bot = _get_bot()
            if bot and TELEGRAM_CHAT_ID:
                await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
        except Exception as exc:
            log.warning("telegram plain-text retry failed: %s", exc)


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s"


async def notify_done(task_id: str, output: str, *, elapsed_s: float) -> None:
    """`✅ task_id=XXX done.` then the full output, chunked at 3000 chars."""
    chunks = _chunks(output or "(empty output)")
    n = len(chunks)
    suffix = "" if n == 1 else f" (1/{n})"
    header = f"✅ task_id={task_id} done in {_fmt_elapsed(elapsed_s)}.{suffix}"
    await _send(f"{header}\n\n{chunks[0]}")
    for i, c in enumerate(chunks[1:], start=2):
        await _send(f"…task_id={task_id} continued ({i}/{n})\n\n{c}")


async def notify_failed(task_id: str, error: str, *, elapsed_s: float,
                        output: Optional[str] = None) -> None:
    """`❌ task_id=XXX failed: <error>.`"""
    msg = f"❌ task_id={task_id} failed after {_fmt_elapsed(elapsed_s)}: {error or 'unknown error'}"
    if output:
        msg += f"\n\nPartial output:\n{output[:CHUNK_BODY_CHARS]}"
    msg += "\n\nWant me to retry?"
    await _send(msg)


async def notify_cancelled(task_id: str, *, elapsed_s: float, note: str = "") -> None:
    """`🛑 task_id=XXX cancelled.`"""
    bits = [f"🛑 task_id={task_id} cancelled after {_fmt_elapsed(elapsed_s)}."]
    if note:
        bits.append(note)
    await _send("\n".join(bits))


async def notify_timeout(task_id: str, *, elapsed_s: float, last_step: str = "") -> None:
    """`⚠️ task_id=XXX timed out at <time>. Last step: <step>.`"""
    bits = [f"⚠️ task_id={task_id} timed out after {_fmt_elapsed(elapsed_s)}."]
    if last_step:
        bits.append(f"Last step: {last_step}")
    bits.append("Want me to retry with a longer timeout?")
    await _send("\n".join(bits))


async def notify_heartbeat(task_id: str, *, elapsed_s: float, step: str = "",
                           tool_calls: int = 0) -> None:
    """`⏳ task_id=XXX still working. Elapsed: 4m12s.`"""
    bits = [f"⏳ task_id={task_id} still working. Elapsed: {_fmt_elapsed(elapsed_s)}."]
    if step:
        bits.append(f"Last step: {step}")
    if tool_calls:
        bits.append(f"Tool calls so far: {tool_calls}")
    bits.append(f"Send 'cancel {task_id}' to stop.")
    await _send("\n".join(bits))

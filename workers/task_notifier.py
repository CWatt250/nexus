"""Task lifecycle notifier — every TASK enqueue ends with a Telegram message.

Single source of truth for the notification format the user actually
sees. The worker calls into this on every terminal lifecycle event
(done / failed / cancelled / timed_out) and on heartbeats for
long-running tasks. Formatting + chunking + retry-hint live here so the
worker stays focused on agent execution.

Voice rule: never lead with task_id=, never paste exception text — log
it, say one plain sentence. The id shows up once, small, at the end,
because 'cancel <id>' is how the user acts on it.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger("nexus.task_notifier")

# ── deliverable-only backstop ────────────────────────────────────────
# With reasoning=True the model's narration lands in reasoning_content,
# not content. These heuristics catch what still leaks: a leading
# "I have enough to build the table" paragraph and a trailing
# "Want me to…?" offer.
_NARRATION_RE = re.compile(
    r"\b(?:I have enough|I now have|I'?ve got enough|I need|I don'?t need"
    r"|results? (?:are|is|were)|let me|the signal|noise"
    r"|I can (?:now )?(?:build|compile|put together|answer)"
    r"|enough to (?:build|write|answer|give)|I'?ll (?:build|put|compile|give))\b",
    re.IGNORECASE,
)
_OFFER_RE = re.compile(
    r"^\s*(?:want me to|should i|shall i|let me know|would you like|do you want"
    r"|need me to|say the word|just say|happy to|i can also)\b",
    re.IGNORECASE,
)
_STRUCTURAL_RE = re.compile(r"^\s*(?:\||#|[-*•]\s|\d+[.)]\s|```)")


def _is_structural(para: str) -> bool:
    return bool(_STRUCTURAL_RE.match(para))


def _looks_like_narration(para: str) -> bool:
    if _is_structural(para) or len(para) > 900:
        return False
    hits = len(_NARRATION_RE.findall(para))
    return hits >= 2 or bool(re.search(r"\bI have enough\b", para, re.IGNORECASE))


_LAST_QUESTION_RE = re.compile(r"(?:^|(?<=[.!?])\s+)([^.!?\n]+\?)\s*$")


def _drop_trailing_offer(para: str) -> str:
    """Strip offer/question sentences off the END of a paragraph."""
    para = para.strip()
    if _is_structural(para):
        return para
    while True:
        m = _LAST_QUESTION_RE.search(para)
        if not m or not _OFFER_RE.match(m.group(1)):
            return para
        para = para[:m.start()].rstrip()


def strip_narration(text: str) -> str:
    """Deliverable only: drop a leading process-narration paragraph (only
    when something follows it) and any trailing question/offer."""
    if not text or not text.strip():
        return text or ""
    paras = re.split(r"\n\s*\n", text.strip())
    # Narration glued to the table with a single newline: split it off.
    first_lines = paras[0].splitlines()
    cut = next((i for i, ln in enumerate(first_lines) if i and _is_structural(ln)), None)
    if cut:
        paras[0:1] = ["\n".join(first_lines[:cut]), "\n".join(first_lines[cut:])]
    if len(paras) >= 2 and _looks_like_narration(paras[0]):
        paras = paras[1:]
    while paras:
        kept = _drop_trailing_offer(paras[-1])
        if kept == paras[-1].strip():
            break
        if kept:
            paras[-1] = kept
            break
        paras.pop()
    return "\n\n".join(p.strip() for p in paras).strip()

# Telegram's hard limit is 4096 chars. We split bodies at 3000 to leave
# room for the header + safety margin and to match user spec.
CHUNK_BODY_CHARS = 3000

_ACTIVE_LOG = Path.home() / "AI_Agent" / "memory" / "active_tasks.jsonl"


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


def _humanize_error(error: str) -> str:
    """One plain sentence for the phone. The raw error goes to the log."""
    low = (error or "").lower()
    if not low:
        return "it died without saying why"
    if "timeout" in low or "timed out" in low:
        return "it ran out of time"
    if "memory" in low or "vram" in low or "devicelost" in low:
        return "the model choked on memory"
    if "connection" in low or "refused" in low or "unreachable" in low:
        return "it couldn't reach something it needed (Ollama or the network)"
    if "load" in low and "model" in low:
        return "that one choked loading the model"
    if "permission" in low or "blocked" in low:
        return "a guardrail blocked a step"
    return "it hit an error partway through"


def _think_on() -> bool:
    """Show-your-work toggle for the notification chat (TELEGRAM_CHAT_ID)."""
    try:
        from workers.conversation_handler import get_think_pref  # noqa: PLC0415
        cid = os.getenv("TELEGRAM_CHAT_ID", "")
        return bool(cid) and get_think_pref(int(cid))
    except Exception:
        return False


def _work_trail(task_id: str) -> str:
    """Best-effort 'what I did and why' for the done message when /think
    is on: last tool calls from active_tasks.jsonl + the original ask."""
    bits: list[str] = []
    try:
        from core import task_queue  # noqa: PLC0415
        row = task_queue.get_task(task_id) or {}
        ask = (row.get("input") or "").strip().splitlines()
        if ask:
            bits.append(f"why: you asked — \"{ask[0][:100]}\"")
    except Exception:
        pass
    try:
        last: dict = {}
        if _ACTIVE_LOG.exists():
            for raw in _ACTIVE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if entry.get("task_id") == task_id:
                    last = entry
        steps = last.get("steps") or last.get("tools") or []
        if isinstance(steps, list) and steps:
            bits.append("tools: " + " → ".join(str(s) for s in steps[-5:]))
        elif last.get("tool_calls"):
            step = last.get("step") or last.get("last_tool") or ""
            bits.append(f"tools: {last['tool_calls']} call(s)"
                        + (f", last {step}" if step else ""))
    except Exception:
        pass
    return "\n".join(f"> {b}" for b in bits)


def done_header(elapsed_s: float) -> str:
    return f"✅ done in {_fmt_elapsed(elapsed_s)}"


def _render_tables(text: str) -> tuple[str, list[tuple[str, str]]]:
    try:
        from tools.telegram_render import prepare_with_captions  # noqa: PLC0415
        return prepare_with_captions(text)
    except Exception as exc:  # noqa: BLE001
        log.info("table render skipped: %s", exc)
        return text, []


async def notify_done(task_id: str, output: str, *, elapsed_s: float) -> None:
    """Fallback path (no live bubble — CLI/API enqueue): completion header,
    the deliverable chunked at 3000 chars, tables as photos. When /think
    is on, the 💭 trail goes out as a SEPARATE trailing message so the
    deliverable stays clean."""
    body = strip_narration(output or "") or "(empty output)"
    body, pngs = _render_tables(body)
    chunks = _chunks(body)
    n = len(chunks)
    suffix = "" if n == 1 else f" (1/{n})"
    await _send(f"{done_header(elapsed_s)}{suffix}\n\n{chunks[0]}")
    for i, c in enumerate(chunks[1:], start=2):
        await _send(f"…continued ({i}/{n})\n\n{c}")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    for path, caption in pngs:
        try:
            from workers.task_progress import send_photo  # noqa: PLC0415
            await send_photo(chat_id, path, caption)
        except Exception as exc:  # noqa: BLE001
            log.warning("table photo send failed: %s", exc)
    if _think_on():
        trail = _work_trail(task_id)
        if trail:
            await _send(f"💭\n{trail}")


async def notify_failed(task_id: str, error: str, *, elapsed_s: float,
                        output: Optional[str] = None) -> None:
    """One plain sentence about what went wrong; the raw error is logged."""
    log.warning("task %s failed after %.1fs: %s", task_id, elapsed_s, error)
    msg = f"❌ That one didn't make it — {_humanize_error(error)} after {_fmt_elapsed(elapsed_s)}."
    if output:
        msg += f"\n\nWhat I had so far:\n{output[:CHUNK_BODY_CHARS]}"
    msg += f"\n\nSay 'retry {task_id}' and I'll take another run at it."
    await _send(msg)


async def notify_cancelled(task_id: str, *, elapsed_s: float, note: str = "") -> None:
    bits = [f"🛑 Stopped that one at {_fmt_elapsed(elapsed_s)}."]
    if note:
        bits.append(note)
    await _send("\n".join(bits))


async def notify_timeout(task_id: str, *, elapsed_s: float, last_step: str = "") -> None:
    bits = [f"⚠️ Ran out of time on that one ({_fmt_elapsed(elapsed_s)})."]
    if last_step:
        bits.append(f"Last thing it was doing: {last_step}.")
    bits.append(f"Say 'retry {task_id}' and I'll give it a longer leash.")
    await _send("\n".join(bits))


async def notify_heartbeat(task_id: str, *, elapsed_s: float, step: str = "",
                           tool_calls: int = 0) -> None:
    bits = [f"⏳ Still on it — {_fmt_elapsed(elapsed_s)} in."]
    if step:
        bits.append(f"Currently: {step}")
    if tool_calls:
        bits.append(f"{tool_calls} tool call{'s' if tool_calls != 1 else ''} so far.")
    bits.append(f"'cancel {task_id}' stops it.")
    await _send("\n".join(bits))

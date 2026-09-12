"""Worker-side owner of a Telegram progress bubble.

The listener sends ONE placeholder bubble per message. When the message
routes to a queued TASK, the listener edits it to an "on it" line and
hands it off by writing `memory/task_progress/<task_id>.json`
({chat_id, message_id, title, created_at}). The task worker loads that
file and keeps editing the SAME bubble from here — tool lines, 💭
reasoning glimpses, heartbeats — and finally replaces it with the
deliverable (chunked, tables as photos).

Talks to Telegram over the HTTP API directly (httpx) because the worker
is a separate process from the python-telegram-bot listener. Same rules
as tools/telegram_progress.ProgressBubble: edits ≥ EDIT_MIN_INTERVAL_S
apart (latest wins), "message is not modified" swallowed, typing action
re-sent every TYPING_INTERVAL_S while active. No JSON → the caller falls
back to task_notifier (new message).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

from tools.telegram_progress import (CHUNK_SEND_DELAY_S, EDIT_MIN_INTERVAL_S,
                                     STAGE_MAX_CHARS, TYPING_INTERVAL_S,
                                     chunk_final, render_tables)
from workers.task_notifier import _fmt_elapsed as fmt_elapsed
from workers.task_notifier import done_header, strip_narration

log = logging.getLogger("nexus.task_progress")

ROOT = Path(__file__).resolve().parent.parent
PROGRESS_DIR = ROOT / "memory" / "task_progress"
WINDOW_LINES = 6
IDLE_HEARTBEAT_S = 30
HANDOFF_WAIT_S = 3.0      # listener may still be writing the JSON when we claim
FRESH_TASK_S = 60         # only wait for a handoff on a just-enqueued task

load_dotenv(Path.home() / "AI_Agent" / ".env")


class TelegramError(RuntimeError):
    pass


# ── Telegram HTTP API ────────────────────────────────────────────────
def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


async def tg_call(method: str, *, json_body: dict | None = None,
                  data: dict | None = None, files: dict | None = None) -> dict:
    """POST one Bot API method. Raises TelegramError with Telegram's
    description on ok=false, httpx errors on transport failure."""
    token = _token()
    if not token:
        raise TelegramError("TELEGRAM_BOT_TOKEN not configured")
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=30) as client:
        if files:
            resp = await client.post(url, data=data or {}, files=files)
        else:
            resp = await client.post(url, json=json_body or data or {})
    try:
        payload = resp.json()
    except ValueError:
        raise TelegramError(f"{method}: HTTP {resp.status_code}") from None
    if not payload.get("ok"):
        raise TelegramError(payload.get("description") or f"{method} failed")
    return payload.get("result") or {}


async def send_photo(chat_id: int | str, path: str, caption: str = "") -> None:
    with open(path, "rb") as fh:
        await tg_call("sendPhoto",
                      data={"chat_id": chat_id, "caption": caption[:1024]},
                      files={"photo": (Path(path).name, fh, "image/png")})


# ── handoff file ─────────────────────────────────────────────────────
_VERBS = (
    (("research", "look up", "lookup", "find", "search", "compare", "investigate", "dig"), "researching…"),
    (("build", "create", "make", "write", "scaffold", "implement", "generate", "code"), "building…"),
    (("fix", "debug", "repair", "patch"), "fixing…"),
    (("analyze", "analyse", "review", "audit", "summarize", "summarise", "explain"), "analyzing…"),
    (("deploy", "ship", "push", "publish"), "shipping…"),
    (("test", "check", "verify", "run"), "checking…"),
)


def derive_title(task_text: str) -> str:
    """'🧠 on it — researching…' from the task's verb."""
    low = (task_text or "").lower()
    verb = "working on it…"
    for keys, label in _VERBS:
        if any(re.search(rf"\b{re.escape(k)}", low) for k in keys):
            verb = label
            break
    return f"🧠 on it — {verb}"


def handoff_path(task_id: str) -> Path:
    return PROGRESS_DIR / f"{task_id}.json"


def write_handoff(task_id: str, *, chat_id: int, message_id: int, title: str) -> Path:
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    p = handoff_path(task_id)
    p.write_text(json.dumps({
        "task_id": task_id, "chat_id": chat_id, "message_id": message_id,
        "title": title, "created_at": time.time(),
    }), encoding="utf-8")
    return p


def load_handoff(task_id: str) -> Optional[dict]:
    try:
        data = json.loads(handoff_path(task_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "chat_id" not in data or "message_id" not in data:
        return None
    return data


def clear_handoff(task_id: str) -> None:
    try:
        handoff_path(task_id).unlink()
    except OSError:
        pass


# ── rolling window ───────────────────────────────────────────────────
def first_sentence(text: str, limit: int = 140) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return ""
    m = re.search(r"[.!?](?:\s|$)", t)
    if m:
        t = t[:m.end()].strip()
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


def arg_preview(inputs, limit: int = 60) -> str:
    if isinstance(inputs, dict):
        s = ", ".join(f"{k}={v}" for k, v in inputs.items())
    else:
        s = str(inputs or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


class ProgressWindow:
    """Title + elapsed header over the last WINDOW_LINES activity lines."""

    def __init__(self, title: str, max_lines: int = WINDOW_LINES):
        self.title = title
        self.lines: deque[str] = deque(maxlen=max_lines)

    def add(self, line: str) -> None:
        self.lines.append(line)

    def mark_done(self) -> None:
        """✓ the most recent 🔧 line that isn't already checked."""
        for i in range(len(self.lines) - 1, -1, -1):
            ln = self.lines[i]
            if ln.startswith("🔧") and not ln.endswith(" ✓"):
                self.lines[i] = ln + " ✓"
                return

    def render(self, elapsed_s: float, *, idle: bool = False) -> str:
        head = (f"🧠 still thinking… {fmt_elapsed(elapsed_s)}" if idle
                else f"{self.title}\n⏱ {fmt_elapsed(elapsed_s)}")
        return "\n".join([head, *self.lines])[:STAGE_MAX_CHARS]


# ── the bubble ───────────────────────────────────────────────────────
class TaskProgress:
    def __init__(self, task_id: str, chat_id: int, message_id: int, title: str):
        self.task_id = task_id
        self.chat_id = chat_id
        self.message_id = message_id
        self.title = title
        self._last_edit = 0.0
        self._last_text = ""
        self._pending: Optional[str] = None
        self._flusher: Optional[asyncio.Task] = None
        self._typing: Optional[asyncio.Task] = None
        self._done = False

    @classmethod
    async def for_task(cls, task_id: str, *, fresh: bool = True) -> Optional["TaskProgress"]:
        """Load the handoff written by the listener. A just-enqueued task
        may be claimed before the listener finishes writing it, so wait
        briefly (only when `fresh`)."""
        deadline = time.monotonic() + (HANDOFF_WAIT_S if fresh else 0)
        while True:
            data = load_handoff(task_id)
            if data:
                return cls(task_id, int(data["chat_id"]), int(data["message_id"]),
                           data.get("title") or "🧠 on it…")
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.25)

    # ── lifecycle ────────────────────────────────────────────────────
    def start(self) -> "TaskProgress":
        if self._typing is None:
            self._typing = asyncio.ensure_future(self._typing_loop())
        return self

    async def _typing_loop(self) -> None:
        try:
            while not self._done:
                try:
                    await tg_call("sendChatAction", json_body={"chat_id": self.chat_id, "action": "typing"})
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(TYPING_INTERVAL_S)
        except asyncio.CancelledError:
            pass

    def _stop(self) -> None:
        self._done = True
        for t in (self._flusher, self._typing):
            if t is not None and not t.done():
                t.cancel()

    # ── stages ───────────────────────────────────────────────────────
    async def stage(self, text: str) -> None:
        if self._done or not text:
            return
        self._pending = text[:STAGE_MAX_CHARS]
        wait = EDIT_MIN_INTERVAL_S - (time.monotonic() - self._last_edit)
        if wait <= 0:
            await self._flush()
        elif self._flusher is None or self._flusher.done():
            self._flusher = asyncio.ensure_future(self._delayed_flush(wait))

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
        self._last_edit = time.monotonic()
        try:
            await tg_call("editMessageText", json_body={
                "chat_id": self.chat_id, "message_id": self.message_id, "text": text})
            self._last_text = text
            return True
        except Exception as exc:  # noqa: BLE001
            if "not modified" in str(exc).lower():
                self._last_text = text
                return True
            log.info("progress edit failed: %s", exc)
            return False

    async def send(self, text: str) -> None:
        try:
            await tg_call("sendMessage", json_body={"chat_id": self.chat_id, "text": text})
        except Exception as exc:  # noqa: BLE001
            log.warning("progress send failed: %s", exc)

    # ── terminal ─────────────────────────────────────────────────────
    async def finish(self, final_text: str, *, elapsed_s: float) -> None:
        """Bubble → deliverable. Narration stripped, tables → photos,
        chunked if long (bubble = chunk 1, rest follow)."""
        self._stop()
        body = strip_narration(final_text or "") or "(empty output)"
        body, pngs = render_tables(body)
        chunks = chunk_final(f"{done_header(elapsed_s)}\n\n{body}")
        if not await self._edit(chunks[0]):
            await self.send(chunks[0])
        for c in chunks[1:]:
            await asyncio.sleep(CHUNK_SEND_DELAY_S)
            await self.send(c)
        for path, caption in pngs:
            try:
                await send_photo(self.chat_id, path, caption)
            except Exception as exc:  # noqa: BLE001
                log.warning("table photo send failed: %s", exc)
        clear_handoff(self.task_id)

    async def fail(self, text: str) -> None:
        """One in-voice line, no exception text."""
        self._stop()
        if not await self._edit(text):
            await self.send(text)
        clear_handoff(self.task_id)

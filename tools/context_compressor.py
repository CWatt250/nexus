"""Nexus context compressor.

Every `COMPRESS_EVERY` turns (default 10), this module:
  1. Loads the LangGraph checkpoint state for the given thread.
  2. Asks qwen3:4b to summarize the conversation so far in roughly
     `TARGET_TOKENS` tokens.
  3. Rewrites the checkpoint by tombstoning prior messages with
     `RemoveMessage(id=...)` and injecting a single SystemMessage that
     carries the summary. Future turns see only (summary + new user msg).
  4. Logs the event to `~/AI_Agent/memory/compression-log.md`.

Turn counters live in `~/AI_Agent/memory/compression-state.json` and are
keyed by thread_id so each session compresses independently."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import RemoveMessage, SystemMessage

STATE_FILE = Path.home() / "AI_Agent" / "memory" / "compression-state.json"
LOG_FILE = Path.home() / "AI_Agent" / "memory" / "compression-log.md"
OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:4b"

COMPRESS_EVERY = 10
TARGET_TOKENS = 500             # we target ~500 tokens in the summary
KEEP_RECENT = 2                 # keep the last N messages verbatim

SYSTEM_PROMPT = (
    "You compress conversations for a long-running assistant. Write a "
    f"dense summary in roughly {TARGET_TOKENS} tokens (~2000 characters) "
    "that captures: the user's goals, key decisions, facts established, "
    "open questions, and anything the assistant must remember to stay "
    "useful. Preserve concrete identifiers (file paths, names, numbers). "
    "Drop chitchat. Output plain prose, no markdown headings."
)

log = logging.getLogger("nexus.compressor")


# ---------------------------------------------------------------------------
# Turn counter state
# ---------------------------------------------------------------------------

def _load_state() -> dict[str, int]:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, int]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def bump_turn(thread_id: str) -> int:
    state = _load_state()
    state[thread_id] = state.get(thread_id, 0) + 1
    _save_state(state)
    return state[thread_id]


def should_compress(turn_n: int) -> bool:
    return turn_n > 0 and turn_n % COMPRESS_EVERY == 0


# ---------------------------------------------------------------------------
# Summarization
# ---------------------------------------------------------------------------

def _format_history(messages) -> str:
    parts: list[str] = []
    for m in messages or []:
        role = getattr(m, "type", m.__class__.__name__.replace("Message", "").lower())
        content = getattr(m, "content", "")
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        parts.append(f"[{role}] {str(content).strip()}")
    return "\n\n".join(parts)


def _summarize(history_text: str) -> str:
    try:
        import ollama
    except ImportError:
        return ""
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": history_text[:60_000]},
            ],
            stream=False,
            think=False,
            options={"temperature": 0.2, "num_predict": TARGET_TOKENS + 100, "num_ctx": 16_384},
        )
    except Exception as exc:
        log.warning("summary call failed: %s", exc)
        return ""
    content = resp["message"]["content"] if isinstance(resp, dict) else getattr(resp.message, "content", "")
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL | re.IGNORECASE).strip()
    return content


def _log_event(thread_id: str, turn_n: int, dropped: int, summary: str, status: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# Nexus context compression log\n\n", encoding="utf-8")
    ts = datetime.now().isoformat(timespec="seconds")
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(
            f"## {ts} — thread {thread_id[:8]} — turn {turn_n} — {status}\n\n"
            f"- dropped messages: {dropped}\n"
            f"- summary length: {len(summary)} chars\n\n"
        )
        if summary:
            f.write("```\n" + summary + "\n```\n\n")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def maybe_compress(agent: Any, thread_id: str) -> dict:
    """Bump the turn counter for `thread_id`; if we've hit a compression
    boundary, rewrite the checkpoint history. Returns a status dict."""
    turn_n = bump_turn(thread_id)
    status = {"thread_id": thread_id, "turn": turn_n, "compressed": False}
    if not should_compress(turn_n):
        return status

    config = {"configurable": {"thread_id": thread_id}}
    try:
        snap = agent.get_state(config)
    except Exception as exc:
        log.warning("could not load state for %s: %s", thread_id, exc)
        return status
    messages = (getattr(snap, "values", {}) or {}).get("messages") or []
    if len(messages) <= KEEP_RECENT:
        return status

    to_summarize = messages[:-KEEP_RECENT] if KEEP_RECENT else list(messages)
    history_text = _format_history(to_summarize)
    summary = _summarize(history_text)
    if not summary:
        _log_event(thread_id, turn_n, 0, "", "skipped — summary failed")
        return status

    # Tombstone every summarized message + inject a SystemMessage carrying
    # the summary. LangGraph's add_messages reducer handles this.
    removals = []
    for m in to_summarize:
        mid = getattr(m, "id", None)
        if mid:
            removals.append(RemoveMessage(id=mid))
    summary_msg = SystemMessage(
        content=f"[compressed history — {turn_n} turns, {len(removals)} messages summarized]\n\n{summary}"
    )
    try:
        agent.update_state(config, {"messages": removals + [summary_msg]})
    except Exception as exc:
        log.warning("update_state failed: %s", exc)
        _log_event(thread_id, turn_n, len(removals), summary, f"failed: {exc}")
        return status

    _log_event(thread_id, turn_n, len(removals), summary, "ok")
    status["compressed"] = True
    status["dropped"] = len(removals)
    return status

"""Human-in-the-loop checkpoints (Phase 14.7).

A long-running task can call `checkpoint(task_id, summary)` to pause and
ask the human to confirm/modify/cancel before proceeding.

Implementation:
  * Write a JSON record to `memory/checkpoints/<task_id>.json` with the
    summary, the allowed options, and a `requested_at` timestamp.
  * Try to fire a Telegram notification (`telegram_notify`) — silently
    skips when the bot service is offline (Rule 10: Telegram is gated
    until Phase 15 verification). The record stays durable either way.
  * Poll for `memory/checkpoints/<task_id>.response.json` containing
    `{"choice": "<option>", "note": "..."}`. The Phase 16.1 Telegram
    handler will write that file; tests/scripts can also write it
    directly.
  * Return the chosen option, or `"cancel"` on timeout.

For tasks expected to exceed 30 min, callers should insert a checkpoint
every 25% — `should_checkpoint(elapsed, expected, last_emitted_pct)`
helps decide.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path.home() / "AI_Agent"
CHECKPOINT_DIR = ROOT / "memory" / "checkpoints"
DEFAULT_OPTIONS = ("go", "modify", "cancel")
DEFAULT_TIMEOUT_SECONDS = 3600  # 1h
POLL_SECONDS = 2.0

log = logging.getLogger("nexus.checkpoints")


def _request_path(task_id: str) -> Path:
    return CHECKPOINT_DIR / f"{task_id}.json"


def _response_path(task_id: str) -> Path:
    return CHECKPOINT_DIR / f"{task_id}.response.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _try_telegram(summary: str, options: tuple[str, ...]) -> None:
    """Best-effort fire to the Telegram tool. The bot service is offline
    until Phase 15 verification; we still log the attempt for audit."""
    try:
        from tools.telegram_tool import telegram_notify  # type: ignore
        msg = (
            f"🛑 Nexus checkpoint\n\n{summary}\n\n"
            f"Reply with one of: {', '.join(options)}"
        )
        telegram_notify.invoke({"message": msg})
    except Exception as exc:
        log.info("checkpoint telegram skipped: %s", exc)


def checkpoint(
    task_id: str,
    summary: str,
    *,
    options: tuple[str, ...] = DEFAULT_OPTIONS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
) -> dict:
    """Pause a long task and wait for a human decision.

    Returns a dict {"choice": str, "note": str, "timed_out": bool}.
    `choice` is `"cancel"` on timeout (fail-safe to avoid runaway work)."""
    if not task_id:
        raise ValueError("checkpoint requires task_id")
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    request = {
        "task_id": task_id,
        "summary": summary[:2000],
        "options": list(options),
        "requested_at": _now(),
        "expires_at_seconds": time.time() + timeout,
    }
    req_path = _request_path(task_id)
    resp_path = _response_path(task_id)
    # Clear any stale response from a previous round before we start.
    if resp_path.exists():
        try:
            resp_path.unlink()
        except OSError:
            pass
    try:
        req_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("checkpoint write failed: %s", exc)
    _try_telegram(summary, options)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if resp_path.exists():
            try:
                payload = json.loads(resp_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            choice = str(payload.get("choice", "")).strip().lower()
            if choice not in options:
                choice = "cancel"
            note = str(payload.get("note", ""))[:1000]
            try:
                resp_path.unlink()
            except OSError:
                pass
            try:
                req_path.unlink()
            except OSError:
                pass
            return {"choice": choice, "note": note, "timed_out": False}
        time.sleep(poll_seconds)

    # Timeout — fail safe.
    try:
        req_path.unlink()
    except OSError:
        pass
    return {"choice": "cancel", "note": "checkpoint timed out", "timed_out": True}


def respond(task_id: str, choice: str, note: str = "") -> Path:
    """Helper to drop the response file. Called by tests, the dashboard
    pause/cancel/modify controls (Phase 17.9), and the Phase 16.1 Telegram
    bot once Phase 15 has unblocked it."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    p = _response_path(task_id)
    p.write_text(
        json.dumps({"choice": choice, "note": note, "answered_at": _now()}, ensure_ascii=False),
        encoding="utf-8",
    )
    return p


def should_checkpoint(elapsed_seconds: float, expected_seconds: float, last_emitted_pct: float) -> bool:
    """For tasks expected to run >30 min, suggest a checkpoint every 25%
    of the projected duration (i.e. at 25/50/75/100%). Returns True the
    first time the elapsed share crosses the next quartile threshold."""
    if expected_seconds < 1800:
        return False
    pct = elapsed_seconds / expected_seconds
    next_band = (int(last_emitted_pct * 4) + 1) / 4  # 0.25, 0.5, 0.75, 1.0
    return pct >= next_band <= 1.0

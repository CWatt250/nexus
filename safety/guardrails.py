"""Nexus guardrails — deny-list for dangerous shell commands, a terminal
execution timeout, a token-usage log, and a sliding-window tool-call rate
limiter.

Every public function is cheap and thread-safe so any tool can call into
this module without worrying about ordering."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

MAX_EXEC_SECONDS = 60           # any terminal command exceeding this is killed
RATE_LIMIT_CALLS = 30           # max tool calls…
RATE_LIMIT_WINDOW = 60          # …per this many seconds
RATE_LIMIT_PAUSE = 60           # pause duration when exceeded

MEMORY_DIR = Path.home() / "AI_Agent" / "memory"
TOKEN_LOG = MEMORY_DIR / "token-usage.log"
BLOCKED_LOG = MEMORY_DIR / "blocked-commands.log"

log = logging.getLogger("nexus.guardrails")


# ---------------------------------------------------------------------------
# Command blacklist
# ---------------------------------------------------------------------------

# Regexes are matched against the normalized (whitespace-collapsed) command.
# Each entry is (pattern, human-readable reason). Order is stable; first hit
# wins so more specific entries should come first.
BLACKLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb"),
    (re.compile(r"\brm\s+(-[a-zA-Z]*[rRf][a-zA-Z]*\s+)+"), "rm -rf / recursive delete"),
    (re.compile(r"\bmkfs(\.[a-z0-9]+)?\b"), "filesystem format (mkfs)"),
    (re.compile(r"\bdd\s+.*\bif\s*="), "dd if= (raw disk write)"),
    (re.compile(r"\bformat\b"), "format command"),
    (re.compile(r"\bfdisk\b"), "fdisk (partition editor)"),
    (re.compile(r"\bshred\b"), "shred (secure delete)"),
    (re.compile(r"\bwipefs\b"), "wipefs (wipe filesystem signatures)"),
    (re.compile(r"\bchmod\s+-R\s+777\s+/"), "chmod -R 777 / (world-writable root)"),
    (re.compile(r"\bchown\s+-R\b"), "chown -R (recursive ownership change)"),
    (re.compile(r"\bmv\s+/\*"), "mv /* (move from filesystem root)"),
    (re.compile(r"(^|[\s;&|`])/boot(/|\b)"), "touches /boot"),
    (re.compile(r"/etc/passwd\b"), "touches /etc/passwd"),
    (re.compile(r"/etc/shadow\b"), "touches /etc/shadow"),
    (re.compile(r"/etc/sudoers(\.d)?\b"), "touches /etc/sudoers"),
]


class CommandBlocked(Exception):
    """Raised when a command fails the guardrails check."""

    def __init__(self, command: str, reason: str) -> None:
        super().__init__(f"blocked: {reason}")
        self.command = command
        self.reason = reason


def _normalize(cmd: str) -> str:
    return re.sub(r"\s+", " ", (cmd or "").strip())


def check_command(cmd: str) -> tuple[bool, str]:
    """Classify a shell command. Returns (safe, reason). `reason` is empty
    on safe commands, non-empty on blocked ones."""
    if not cmd or not cmd.strip():
        return False, "empty command"
    norm = _normalize(cmd)
    for pattern, reason in BLACKLIST:
        if pattern.search(norm):
            return False, reason
    return True, ""


def log_blocked(cmd: str, reason: str, *, source: str = "sandbox") -> None:
    """Append one JSONL entry to blocked-commands.log."""
    BLOCKED_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "command": cmd,
        "reason": reason,
    }
    try:
        with BLOCKED_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("failed to write blocked-commands log: %s", exc)


# ---------------------------------------------------------------------------
# Token budget tracker
# ---------------------------------------------------------------------------

_token_lock = threading.Lock()
_token_totals: dict[str, int] = {}  # session_id → cumulative tokens


def note_tokens(session: str, tokens: int, *, model: str | None = None) -> int:
    """Add `tokens` to the cumulative count for `session`, persist a log
    line, and return the new total."""
    if tokens <= 0:
        return _token_totals.get(session, 0)
    with _token_lock:
        total = _token_totals.get(session, 0) + int(tokens)
        _token_totals[session] = total
    TOKEN_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "delta": int(tokens),
        "total": total,
    }
    if model:
        entry["model"] = model
    try:
        with TOKEN_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("failed to write token-usage log: %s", exc)
    return total


def session_tokens(session: str) -> int:
    with _token_lock:
        return _token_totals.get(session, 0)


# ---------------------------------------------------------------------------
# Rate limiter (sliding window)
# ---------------------------------------------------------------------------

_rl_lock = threading.Lock()
_rl_history: deque[float] = deque()
_rl_paused_until: float = 0.0


def rate_limit(tool_name: str | None = None, *, sleep: bool = True) -> float:
    """Record a tool invocation and enforce the rate limit.

    If the caller has already issued `RATE_LIMIT_CALLS` invocations in the
    last `RATE_LIMIT_WINDOW` seconds, sleep for `RATE_LIMIT_PAUSE`
    seconds (unless `sleep=False`, in which case just return how long the
    caller *should* sleep).

    Returns the pause duration actually applied (or requested)."""
    global _rl_paused_until
    now = time.time()
    with _rl_lock:
        # Drop entries outside the window.
        cutoff = now - RATE_LIMIT_WINDOW
        while _rl_history and _rl_history[0] < cutoff:
            _rl_history.popleft()
        # Still inside a previous pause?
        if now < _rl_paused_until:
            wait = _rl_paused_until - now
        elif len(_rl_history) >= RATE_LIMIT_CALLS:
            _rl_paused_until = now + RATE_LIMIT_PAUSE
            wait = RATE_LIMIT_PAUSE
            log.warning(
                "rate limit hit (%d calls in %ds, tool=%s) — pausing %ds",
                len(_rl_history), RATE_LIMIT_WINDOW, tool_name or "?", RATE_LIMIT_PAUSE,
            )
        else:
            wait = 0.0
        _rl_history.append(now)
    if wait > 0 and sleep:
        time.sleep(wait)
    return wait


def rate_stats() -> dict:
    with _rl_lock:
        return {
            "window_seconds": RATE_LIMIT_WINDOW,
            "limit": RATE_LIMIT_CALLS,
            "recent_calls": len(_rl_history),
            "paused_until": _rl_paused_until,
        }


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

def _self_test(commands: Iterable[str]) -> None:
    for c in commands:
        safe, reason = check_command(c)
        tag = "SAFE " if safe else "BLOCK"
        print(f"[{tag}] {c!r}  reason={reason!r}")


if __name__ == "__main__":
    _self_test([
        "echo hello",
        "rm -rf /tmp/test",
        "dd if=/dev/zero of=/dev/sda",
        "cat /etc/passwd",
        "chmod -R 777 /",
        ":(){ :|:& };:",
        "ls -la",
    ])

"""Nexus sandbox — the single execution path every terminal command must
pass through. Consults guardrails.check_command(), logs blocked attempts,
and enforces the 60-second max execution time."""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from safety import circuit_breaker
from safety.guardrails import (
    MAX_EXEC_SECONDS,
    check_command,
    log_blocked,
    rate_limit,
)

log = logging.getLogger("nexus.sandbox")


def run_guarded(command: str, *, timeout: Optional[int] = None) -> dict:
    """Execute `command` through the guardrails layer. Returns a result
    dict with the same shape as terminal_tool.run_shell, plus a `blocked`
    flag. Never raises for a blocked command — the dict carries the reason."""
    ts = datetime.now(timezone.utc).isoformat()

    # Circuit-breaker & rate-limit gates (may sleep or short-circuit).
    breaker_msg = circuit_breaker.track_tool("terminal")
    if breaker_msg:
        log.warning("circuit breaker: %s", breaker_msg)
        return {
            "ts": ts,
            "tool": "terminal",
            "command": command,
            "returncode": None,
            "stdout": "",
            "stderr": breaker_msg,
            "timed_out": False,
            "blocked": True,
            "reason": breaker_msg,
        }
    rate_limit("terminal")

    safe, reason = check_command(command)
    if not safe:
        log_blocked(command, reason, source="sandbox")
        msg = f"BLOCKED by guardrails: {reason}"
        log.warning("blocked: %s  cmd=%r", reason, command)
        return {
            "ts": ts,
            "tool": "terminal",
            "command": command,
            "returncode": None,
            "stdout": "",
            "stderr": msg,
            "timed_out": False,
            "blocked": True,
            "reason": reason,
        }

    to = int(timeout) if timeout else MAX_EXEC_SECONDS
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=to,
        )
        return {
            "ts": ts,
            "tool": "terminal",
            "command": command,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "timed_out": False,
            "blocked": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ts": ts,
            "tool": "terminal",
            "command": command,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + f"\n[killed — exceeded {to}s limit]",
            "timed_out": True,
            "blocked": False,
        }


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    for c in ("echo hello", "rm -rf /tmp/test"):
        r = run_guarded(c)
        print(c, "→", {k: v for k, v in r.items() if k in ("blocked", "reason", "returncode", "stdout", "stderr")})

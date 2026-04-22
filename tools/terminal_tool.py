"""Terminal execution tool for Nexus.

Every shell command goes through `safety.sandbox.run_guarded`, which
consults the guardrails blacklist, rate limiter, and circuit breaker
before executing with a 60-second hard kill timeout. Each invocation
appends one JSON line to the nexus-core run-log.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from langchain_core.tools import tool

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_log import log_run  # noqa: E402


def run_shell(command: str) -> dict:
    """Execute a shell command through the sandbox. Blocked commands
    return a dict with `blocked=True` instead of running."""
    result = run_guarded(command)
    log_run(
        tool="terminal",
        command=command,
        returncode=result.get("returncode"),
        stdout=result.get("stdout"),
        stderr=result.get("stderr"),
        timed_out=result.get("timed_out", False),
        blocked=result.get("blocked", False),
        reason=result.get("reason"),
        result="ok" if not result.get("blocked") and result.get("returncode", 0) == 0 else ("blocked" if result.get("blocked") else "error"),
        notes=result.get("reason", ""),
    )
    return result


@tool
def terminal(command: str) -> str:
    """Run a shell command on the host (bash, 60s hard timeout).
    Dangerous commands (rm -rf, mkfs, dd if=, /etc/passwd, etc.) are
    blocked by the guardrails layer before execution.
    Returns a short summary of returncode, stdout, stderr."""
    result = run_shell(command)
    if result.get("blocked"):
        return f"BLOCKED: {result.get('reason', 'guardrails')}\n{result.get('stderr', '').rstrip()}"
    parts = [f"returncode={result['returncode']}"]
    if result["stdout"]:
        parts.append(f"stdout:\n{result['stdout'].rstrip()}")
    if result["stderr"]:
        parts.append(f"stderr:\n{result['stderr'].rstrip()}")
    if result["timed_out"]:
        parts.append("[TIMED OUT]")
    return "\n".join(parts)

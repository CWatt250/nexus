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

from safety.sandbox import run_guarded  # noqa: E402

RUN_LOG = Path.home() / "AI_Agent" / "projects" / "nexus-core" / "run-log.jsonl"


def _log(entry: dict) -> None:
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_shell(command: str) -> dict:
    """Execute a shell command through the sandbox. Blocked commands
    return a dict with `blocked=True` instead of running."""
    result = run_guarded(command)
    _log(result)
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

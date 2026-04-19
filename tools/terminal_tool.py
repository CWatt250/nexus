"""Terminal execution tool for Nexus.

Wraps subprocess with a 30s timeout and appends every invocation to the
nexus-core run-log as one JSON line per call.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool

TIMEOUT_SECONDS = 30
RUN_LOG = Path.home() / "AI_Agent" / "projects" / "nexus-core" / "run-log.jsonl"


def _log(entry: dict) -> None:
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_shell(command: str) -> dict:
    """Execute a shell command and return a structured result. Used by both
    the tool wrapper and direct Python callers."""
    ts = datetime.now(timezone.utc).isoformat()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        result = {
            "ts": ts,
            "tool": "terminal",
            "command": command,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        result = {
            "ts": ts,
            "tool": "terminal",
            "command": command,
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + f"\n[timed out after {TIMEOUT_SECONDS}s]",
            "timed_out": True,
        }
    _log(result)
    return result


@tool
def terminal(command: str) -> str:
    """Run a shell command on the host (bash, 30s timeout).
    Returns a short summary of returncode, stdout, stderr."""
    result = run_shell(command)
    parts = [f"returncode={result['returncode']}"]
    if result["stdout"]:
        parts.append(f"stdout:\n{result['stdout'].rstrip()}")
    if result["stderr"]:
        parts.append(f"stderr:\n{result['stderr'].rstrip()}")
    if result["timed_out"]:
        parts.append("[TIMED OUT]")
    return "\n".join(parts)

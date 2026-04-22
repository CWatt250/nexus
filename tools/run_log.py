"""Centralized run-log helper for all Nexus tools.

Every task completion appends a JSONL record to the project's
``run-log.jsonl``. Import and use ``log_run`` instead of writing
to the file directly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

RUN_LOG_DIR = Path.home() / "AI_Agent" / "projects" / "nexus-core"
RUN_LOG_FILE = RUN_LOG_DIR / "run-log.jsonl"


def _get_log_path(project: Optional[str] = None) -> Path:
    """Return the run-log path. Defaults to nexus-core."""
    if project:
        return RUN_LOG_DIR.parent / project / "run-log.jsonl"
    return RUN_LOG_FILE


def log_run(
    tool: str,
    task: Optional[str] = None,
    result: str = "ok",
    notes: str = "",
    command: Optional[str] = None,
    returncode: Optional[int] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    timed_out: bool = False,
    blocked: bool = False,
    reason: Optional[str] = None,
    project: Optional[str] = None,
    **extra: Any,
) -> dict:
    """Append one JSONL record to the project run-log and return it.

    Args:
        tool: tool name that completed (e.g. "terminal", "github_create_repo")
        task: human-readable description of what was done
        result: "ok", "error", "partial", "skipped"
        notes: free-form notes
        command: the command that was run (for terminal tool)
        returncode: exit code (for terminal tool)
        stdout: captured stdout
        stderr: captured stderr
        timed_out: whether the command timed out
        blocked: whether the command was blocked by guardrails
        reason: guardrails block reason
        project: override project (defaults to nexus-core)
        **extra: additional fields to include in the record
    Returns:
        The record dict that was written.
    """
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = _get_log_path(project)

    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
    }
    if task is not None:
        record["task"] = task
    record["result"] = result
    if notes:
        record["notes"] = notes
    if command is not None:
        record["command"] = command
    if returncode is not None:
        record["returncode"] = returncode
    if stdout is not None:
        record["stdout"] = stdout
    if stderr is not None:
        record["stderr"] = stderr
    if timed_out:
        record["timed_out"] = True
    if blocked:
        record["blocked"] = True
    if reason is not None:
        record["reason"] = reason
    if extra:
        record.update(extra)

    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record

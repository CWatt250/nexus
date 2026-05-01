"""Centralized run-log helper for all Nexus tools.

Every task completion appends a JSONL record to the project's
``run-log.jsonl``. Import and use ``log_run`` instead of writing
to the file directly.

Secrets defense (May 1 backup-prep audit):
  Earlier auto-commits captured plaintext token values when the
  terminal tool ran ``cat .env`` / ``cat secrets.yaml`` (Nexus
  inspecting its own config). Every value going into the record
  now passes through ``core.secrets.redact`` first, which masks
  any known-secret value with ``<REDACTED>`` before it hits disk.
  History got rewritten via git-filter-repo; this hook prevents
  the same leak from recurring.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

RUN_LOG_DIR = Path.home() / "AI_Agent" / "projects" / "nexus-core"
RUN_LOG_FILE = RUN_LOG_DIR / "run-log.jsonl"


def _redact(value: Any) -> Any:
    """Pass strings through core.secrets.redact so any value that
    matches a known secret token (GITHUB_TOKEN, TELEGRAM_BOT_TOKEN,
    etc.) is masked. Non-strings pass through unchanged. Never raises
    — a missing/broken secrets module degrades to the original value
    so the log line still gets written."""
    if not isinstance(value, str) or not value:
        return value
    try:
        from core import secrets as _secrets  # noqa: PLC0415
        return _secrets.redact(value)
    except Exception:
        return value


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
        record["task"] = _redact(task)
    record["result"] = result
    if notes:
        record["notes"] = _redact(notes)
    if command is not None:
        record["command"] = _redact(command)
    if returncode is not None:
        record["returncode"] = returncode
    if stdout is not None:
        record["stdout"] = _redact(stdout)
    if stderr is not None:
        record["stderr"] = _redact(stderr)
    if timed_out:
        record["timed_out"] = True
    if blocked:
        record["blocked"] = True
    if reason is not None:
        record["reason"] = _redact(reason)
    if extra:
        # Defensive: redact every string value in extra fields too.
        record.update({k: _redact(v) for k, v in extra.items()})

    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record

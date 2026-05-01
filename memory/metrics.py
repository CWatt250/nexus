"""Agent + tool metrics logger (Phase 14.2).

Two append-only JSONL files under ~/AI_Agent/memory/:
  task_metrics.jsonl  — one record per agent turn
  tool_metrics.jsonl  — one record per tool call

Both files are readable by line. Use `tail -f` for live trace, or read with
`json.loads` per line for analytics. Writes are best-effort: a failed log
must never break the agent.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Iterable

from core import json_safe

ROOT = Path.home() / "AI_Agent"
MEMORY_DIR = ROOT / "memory"
TASK_LOG = MEMORY_DIR / "task_metrics.jsonl"
TOOL_LOG = MEMORY_DIR / "tool_metrics.jsonl"

log = logging.getLogger("nexus.metrics")

_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _approx_tokens(s: Any) -> int:
    if not isinstance(s, str):
        s = str(s)
    return max(0, len(s) // 4)


def _coerce_str(s: Any) -> str:
    """Normalize bytes / non-str inputs to str. Tool outputs sometimes
    come back as `bytes` when a tool forgot `.decode()`; slicing those
    yields more bytes which json.dumps refuses. Coerce here instead of
    chasing every leaf call site."""
    if isinstance(s, (bytes, bytearray)):
        return s.decode("utf-8", errors="replace")
    if isinstance(s, str):
        return s
    return str(s) if s is not None else ""


def _append(path: Path, record: dict) -> None:
    try:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK, path.open("a", encoding="utf-8") as f:
            f.write(json_safe.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("metrics write failed for %s: %s", path.name, exc)


def record_tool_call(
    *,
    task_id: str,
    tool: str,
    latency_ms: float,
    success: bool,
    tokens_in: int = 0,
    tokens_out: int = 0,
    error: str = "",
) -> None:
    record = {
        "ts": _now_iso(),
        "task_id": task_id,
        "tool": tool,
        "latency_ms": round(latency_ms, 2),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "success": bool(success),
        "error": error[:500],
    }
    _append(TOOL_LOG, record)
    # Also fan out as a dashboard event (Phase 17.2). Best-effort.
    try:
        from core import event_bus
        event_bus.emit("tool_called", **record)
    except Exception:
        pass


def record_agent_turn(
    *,
    task_id: str,
    started_at: float,
    ended_at: float,
    route: str,
    model: str,
    user_text: str,
    reply_text: str,
    tool_calls: int,
    success: bool,
    error: str = "",
) -> None:
    user_text = _coerce_str(user_text)
    reply_text = _coerce_str(reply_text)
    error = _coerce_str(error)
    _append(TASK_LOG, {
        "ts": _now_iso(),
        "task_id": task_id,
        "wall_seconds": round(ended_at - started_at, 3),
        "route": route,
        "model": model,
        "tokens_in": _approx_tokens(user_text),
        "tokens_out": _approx_tokens(reply_text),
        "tool_calls": int(tool_calls),
        "success": bool(success),
        "error": error[:500],
        "user_preview": user_text[:200],
        "reply_preview": reply_text[:200],
    })


# A thread-local task id so wrap_tool_with_metrics can attribute calls to
# the agent turn that triggered them. Falls back to "ad-hoc" when nothing
# is set (e.g. CLI smoke tests).
_TASK_CTX = threading.local()


def current_task_id() -> str:
    return getattr(_TASK_CTX, "id", "ad-hoc")


@contextmanager
def task_context(task_id: str):
    """Bind `task_id` to the current thread for the duration of the block.
    Tools wrapped by `wrap_tool_with_metrics` will pick this up automatically."""
    prev = getattr(_TASK_CTX, "id", None)
    _TASK_CTX.id = task_id
    try:
        yield task_id
    finally:
        if prev is None:
            try:
                delattr(_TASK_CTX, "id")
            except AttributeError:
                pass
        else:
            _TASK_CTX.id = prev


def wrap_tool_with_metrics(tool):
    """Time the tool's sync and async callables and record per-call metrics.

    Mutates in place. Returns the tool for chaining. Idempotent — calling
    twice is a no-op (uses an attribute marker)."""
    if getattr(tool, "_metrics_wrapped", False):
        return tool
    tool_name = getattr(tool, "name", getattr(tool, "__name__", "unknown"))

    func = getattr(tool, "func", None)
    if func is not None:
        original = func

        @wraps(original)
        def _sync(*args, **kwargs):
            started = time.monotonic()
            ok = True
            err = ""
            try:
                result = original(*args, **kwargs)
                return result
            except Exception as exc:
                ok = False
                err = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                latency_ms = (time.monotonic() - started) * 1000
                record_tool_call(
                    task_id=current_task_id(),
                    tool=tool_name,
                    latency_ms=latency_ms,
                    success=ok,
                    tokens_in=sum(_approx_tokens(a) for a in args)
                              + sum(_approx_tokens(v) for v in kwargs.values()),
                    error=err,
                )

        tool.func = _sync

    coro = getattr(tool, "coroutine", None)
    if coro is not None:
        original_async = coro

        @wraps(original_async)
        async def _async(*args, **kwargs):
            started = time.monotonic()
            ok = True
            err = ""
            try:
                result = await original_async(*args, **kwargs)
                return result
            except Exception as exc:
                ok = False
                err = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                latency_ms = (time.monotonic() - started) * 1000
                record_tool_call(
                    task_id=current_task_id(),
                    tool=tool_name,
                    latency_ms=latency_ms,
                    success=ok,
                    tokens_in=sum(_approx_tokens(a) for a in args)
                              + sum(_approx_tokens(v) for v in kwargs.values()),
                    error=err,
                )

        tool.coroutine = _async

    tool._metrics_wrapped = True
    return tool


def wrap_tools_with_metrics(tools: Iterable):
    out = list(tools)
    for t in out:
        wrap_tool_with_metrics(t)
    return out

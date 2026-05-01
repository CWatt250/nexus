"""Defensive JSON encoder that doesn't blow up on bytes / dataclasses /
Path / set objects that occasionally leak in from tool outputs.

A regular Nexus task hit a 14-minute timeout because a tool returned
`bytes` (e.g. terminal_tool stdout without `.decode()`), and a
downstream `json.dumps(record)` raised `TypeError: Object of type
bytes is not JSON serializable`. This wraps the standard encoder so
hot logging paths can keep going instead of crashing the worker.

Usage:
    from core.json_safe import dumps
    dumps(record)                      # bytes → str(decode), Path → str
    dumps(record, ensure_ascii=False)  # accepts the same kwargs as json.dumps
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any


def _default(obj: Any) -> Any:
    """`json.dumps(default=...)` callback. Returns a JSON-safe surrogate
    or raises TypeError so the encoder still flags genuinely bad inputs."""
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if hasattr(obj, "isoformat"):              # datetime, date
        try:
            return obj.isoformat()
        except Exception:
            pass
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def dumps(obj: Any, **kwargs: Any) -> str:
    """`json.dumps` with `default=_default`. Caller may still pass
    ensure_ascii / indent / separators / etc. Caller's `default` wins."""
    kwargs.setdefault("default", _default)
    return json.dumps(obj, **kwargs)


def dump(obj: Any, fp: Any, **kwargs: Any) -> None:
    kwargs.setdefault("default", _default)
    json.dump(obj, fp, **kwargs)

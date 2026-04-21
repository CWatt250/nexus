"""Nexus circuit breaker.

Three independent checks:

1. **Tool loop detection.** Per-tool sliding window; if the same tool
   fires more than `TOOL_LIMIT` times in `TOOL_WINDOW` seconds, `track_tool`
   returns a non-empty "tripped" message. Callers should surface that
   message instead of executing the tool again.

2. **Runaway memory.** `check_memory()` reads RSS for nexus-agent via
   systemd; if it exceeds `RAM_LIMIT_BYTES` it logs a warning and attempts
   `systemctl restart nexus-agent`.

3. **Runaway Ollama.** `check_ollama()` hits `/api/ps`; if any loaded
   model has been running for more than `OLLAMA_MAX_SECONDS` it logs a
   warning. (Unloading is left to the ollama server's own TTL.)"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

TOOL_LIMIT = 10                         # same tool this many times…
TOOL_WINDOW = 60                        # …within this many seconds → trip

RAM_LIMIT_BYTES = 8 * 1024 * 1024 * 1024   # 8 GB
OLLAMA_MAX_SECONDS = 30 * 60               # 30 minutes

WATCHDOG_LOG = Path.home() / "AI_Agent" / "memory" / "watchdog.log"
OLLAMA_URL = "http://localhost:11434"

log = logging.getLogger("nexus.circuit_breaker")


def _log_event(kind: str, detail: dict) -> None:
    WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "component": "circuit_breaker",
        "event": kind,
    }
    entry.update(detail)
    try:
        with WATCHDOG_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("watchdog.log write failed: %s", exc)


# ---------------------------------------------------------------------------
# Tool loop detection
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_tool_calls: dict[str, deque[float]] = defaultdict(deque)
_tripped_tools: dict[str, float] = {}
TRIP_COOLDOWN = 120  # how long a trip stays hot after firing


def track_tool(tool_name: str) -> str:
    """Record one invocation of `tool_name`. Returns an empty string if
    everything is fine, or a human-readable message if the breaker has
    tripped for that tool."""
    now = time.time()
    with _lock:
        # Clear an expired trip.
        trip_at = _tripped_tools.get(tool_name)
        if trip_at and now - trip_at > TRIP_COOLDOWN:
            _tripped_tools.pop(tool_name, None)
            trip_at = None

        hist = _tool_calls[tool_name]
        cutoff = now - TOOL_WINDOW
        while hist and hist[0] < cutoff:
            hist.popleft()
        hist.append(now)

        if trip_at:
            return (
                "Circuit breaker triggered — possible infinite loop detected "
                f"(tool={tool_name!r})"
            )
        if len(hist) > TOOL_LIMIT:
            _tripped_tools[tool_name] = now
            _log_event("tool_loop", {"tool": tool_name, "calls": len(hist), "window": TOOL_WINDOW})
            return (
                "Circuit breaker triggered — possible infinite loop detected "
                f"(tool={tool_name!r}, {len(hist)} calls in {TOOL_WINDOW}s)"
            )
    return ""


def reset_tool(tool_name: str | None = None) -> None:
    with _lock:
        if tool_name is None:
            _tool_calls.clear()
            _tripped_tools.clear()
        else:
            _tool_calls.pop(tool_name, None)
            _tripped_tools.pop(tool_name, None)


# ---------------------------------------------------------------------------
# RAM watchdog
# ---------------------------------------------------------------------------

def _nexus_agent_rss() -> int | None:
    """Return combined RSS (bytes) of the nexus-agent cgroup, or None."""
    try:
        res = subprocess.run(
            ["systemctl", "show", "nexus-agent", "--property=MemoryCurrent", "--value"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    raw = (res.stdout or "").strip()
    if not raw or raw == "[not set]":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def check_memory(*, restart: bool = True) -> dict:
    rss = _nexus_agent_rss()
    status = {"rss": rss, "limit": RAM_LIMIT_BYTES, "over": False, "restarted": False}
    if rss is None:
        return status
    if rss > RAM_LIMIT_BYTES:
        status["over"] = True
        log.warning("nexus-agent RSS %d > %d; attempting restart", rss, RAM_LIMIT_BYTES)
        _log_event("ram_over", {"rss": rss, "limit": RAM_LIMIT_BYTES})
        if restart:
            try:
                subprocess.run(
                    ["systemctl", "restart", "nexus-agent"],
                    capture_output=True, text=True, timeout=15,
                )
                status["restarted"] = True
                _log_event("ram_restart", {"service": "nexus-agent", "rss": rss})
            except Exception as exc:
                _log_event("ram_restart_failed", {"error": f"{type(exc).__name__}: {exc}"})
    return status


# ---------------------------------------------------------------------------
# Ollama runtime watchdog
# ---------------------------------------------------------------------------

def check_ollama() -> list[dict]:
    try:
        req = urllib.request.Request(OLLAMA_URL + "/api/ps")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _log_event("ollama_query_failed", {"error": f"{type(exc).__name__}: {exc}"})
        return []

    offenders: list[dict] = []
    now = time.time()
    for m in data.get("models", []) or []:
        expires_at = m.get("expires_at") or m.get("expire_at")
        # Ollama exposes an RFC3339 expires_at; compute how long it's been
        # resident from size_vram/expire window isn't reliable, so we track
        # first-seen ourselves.
        name = m.get("name") or m.get("model") or "?"
        seen = _ollama_first_seen.setdefault(name, now)
        elapsed = now - seen
        if elapsed > OLLAMA_MAX_SECONDS:
            offenders.append({"model": name, "resident_seconds": int(elapsed)})
            _log_event(
                "ollama_long_resident",
                {"model": name, "resident_seconds": int(elapsed), "limit": OLLAMA_MAX_SECONDS},
            )
        if expires_at:
            # Clean up entries the server has already evicted.
            pass
    # Drop tracking for models that are no longer loaded.
    active = {m.get("name") or m.get("model") for m in data.get("models", []) or []}
    for gone in list(_ollama_first_seen):
        if gone not in active:
            _ollama_first_seen.pop(gone, None)
    return offenders


_ollama_first_seen: dict[str, float] = {}


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Simulate a loop.
    for i in range(12):
        r = track_tool("echo")
        print(i, r or "ok")
    print("memory:", check_memory(restart=False))
    print("ollama:", check_ollama())

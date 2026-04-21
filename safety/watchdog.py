#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Nexus watchdog.

Runs as the `nexus-watchdog` systemd service. Every 30 seconds it checks
each target service with `systemctl is-active`. If anything is not
`active`, it runs `systemctl restart <svc>`, logs the event to
~/AI_Agent/memory/watchdog.log, and raises a desktop notification via
notify-send.

Also runs the circuit-breaker RAM and Ollama checks on each pass."""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from safety import circuit_breaker  # noqa: E402

WATCH_INTERVAL = 30
SERVICES = [
    "nexus-agent",
    "nexus-api",
    "nexus-design",
    "open-webui",
    "open-terminal",
    "ollama",
]
LOG_PATH = Path.home() / "AI_Agent" / "memory" / "watchdog.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s nexus-watchdog %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("nexus.watchdog")


def _log_event(event: str, detail: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "component": "watchdog",
        "event": event,
    }
    entry.update(detail)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("watchdog.log write failed: %s", exc)


def _notify(summary: str, body: str = "") -> None:
    bin_ = shutil.which("notify-send")
    if not bin_:
        return
    # Try to talk to the user's session bus.
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    uid = env.get("SUDO_UID") or "1000"
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    try:
        subprocess.run(
            [bin_, "-a", "nexus-watchdog", summary, body],
            env=env, capture_output=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _systemctl(*args: str, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _service_exists(svc: str) -> bool:
    res = _systemctl("list-unit-files", f"{svc}.service", "--no-legend")
    return bool(res.stdout.strip())


def _is_active(svc: str) -> tuple[bool, str]:
    res = _systemctl("is-active", svc, timeout=5)
    state = (res.stdout or res.stderr or "").strip()
    return state == "active", state


def _restart(svc: str) -> tuple[bool, str]:
    res = _systemctl("restart", svc, timeout=30)
    ok = res.returncode == 0
    return ok, (res.stderr or res.stdout or "").strip()


def _check_services(known: set[str]) -> None:
    for svc in SERVICES:
        if svc not in known:
            continue
        active, state = _is_active(svc)
        if active:
            continue
        log.warning("%s is %s — restarting", svc, state or "inactive")
        _log_event("service_down", {"service": svc, "state": state})
        ok, detail = _restart(svc)
        _log_event(
            "service_restart",
            {"service": svc, "ok": ok, "detail": detail[:500]},
        )
        summary = f"Nexus watchdog: {svc} {'restarted' if ok else 'restart failed'}"
        _notify(summary, detail[:200] or state)
        if not ok:
            log.error("%s restart failed: %s", svc, detail)


def main() -> None:
    stop = {"flag": False}

    def handle(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    # Figure out which services are actually installed so we don't log
    # noise for services this box doesn't run.
    known = {s for s in SERVICES if _service_exists(s)}
    missing = [s for s in SERVICES if s not in known]
    log.info("nexus-watchdog starting; watching %s", sorted(known))
    if missing:
        log.info("(ignoring services not installed on this host: %s)", missing)
    _log_event("watchdog_start", {"watching": sorted(known), "missing": missing})

    while not stop["flag"]:
        try:
            _check_services(known)
            circuit_breaker.check_memory()
            circuit_breaker.check_ollama()
        except Exception as exc:
            log.exception("watchdog pass failed: %s", exc)
            _log_event("watchdog_error", {"error": f"{type(exc).__name__}: {exc}"})
        for _ in range(WATCH_INTERVAL):
            if stop["flag"]:
                break
            time.sleep(1)
    log.info("nexus-watchdog stopping")
    _log_event("watchdog_stop", {})


if __name__ == "__main__":
    main()

"""Phase 22.4 — nexus_restart_services tool.

Lets Nexus (or the dashboard) restart its own systemd services after a
Claude Code dispatch lands new code on disk. Uses `sudo systemctl
restart` via the NOPASSWD sudoers entry installed by SUDO_DISPATCH.sh.

Without that entry, the tool returns a clear error explaining what to
run — never silently fails."""
from __future__ import annotations

import subprocess
from typing import Iterable, Optional

from langchain_core.tools import tool

DEFAULT_SERVICES = [
    "nexus-task-worker",
    "nexus-agent",
    "nexus-api",
    "nexus-telegram",
    "nexus-dashboard",
    "nexus-cc-dispatcher",
    "nexus-cc-reporter",
]

ALLOWED_PREFIX = "nexus-"


def _restart_one(name: str, *, dry_run: bool = False) -> tuple[bool, str]:
    """Returns (ok, message). Validates the service name starts with the
    allowed prefix so a mistyped tool call can't restart sshd."""
    if not name.startswith(ALLOWED_PREFIX):
        return False, f"refused: {name} — must start with {ALLOWED_PREFIX!r}"
    if dry_run:
        return True, f"dry-run: would restart {name}"
    try:
        proc = subprocess.run(
            ["sudo", "-n", "/bin/systemctl", "restart", f"{name}.service"],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, f"{name}: timeout (30s)"
    except FileNotFoundError:
        return False, "sudo or systemctl missing"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:200]
        if "a password is required" in err.lower() or "no tty" in err.lower():
            return False, (
                f"{name}: NOPASSWD sudoers entry not installed. "
                "Run ~/AI_Agent/SUDO_DISPATCH.sh."
            )
        return False, f"{name}: {err}"
    return True, f"{name}: restarted"


def _normalize_services(services: Optional[Iterable[str]]) -> list[str]:
    if services is None:
        return list(DEFAULT_SERVICES)
    if isinstance(services, str):
        return [s.strip() for s in services.split(",") if s.strip()]
    return [str(s).strip() for s in services if str(s).strip()]


@tool
def nexus_restart_services(services: str = "", dry_run: bool = False) -> str:
    """Restart one or more nexus-* systemd services.

    Args:
        services: Comma-separated service names without `.service` suffix
            (e.g. `nexus-api,nexus-task-worker`). Empty = restart the
            full default set: api, agent, telegram, task-worker,
            dashboard, cc-dispatcher, cc-reporter.
        dry_run: If true, validates names but doesn't actually restart.

    Returns:
        One line per service with success/failure status.
    """
    names = _normalize_services(services or None)
    lines = []
    ok_count = 0
    for n in names:
        ok, msg = _restart_one(n, dry_run=dry_run)
        if ok:
            ok_count += 1
        lines.append(("✓ " if ok else "✗ ") + msg)
    summary = f"restarted {ok_count}/{len(names)}"
    return summary + "\n" + "\n".join(lines)


def restart_services_sync(services: list[str] | None = None) -> dict:
    """Direct entry point for non-LangGraph callers (API, dashboard,
    Telegram listener). Returns a structured dict instead of formatted
    text so callers can render their own output."""
    names = _normalize_services(services)
    results = []
    ok_count = 0
    for n in names:
        ok, msg = _restart_one(n)
        results.append({"service": n, "ok": ok, "message": msg})
        if ok:
            ok_count += 1
    return {"total": len(names), "ok": ok_count, "results": results}


RESTART_SERVICES_TOOLS = [nexus_restart_services]

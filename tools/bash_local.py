"""Phase 27 — allowlisted bash for the local builder path.

Sibling to tools/terminal_tool.py (which delegates to the safety
sandbox blacklist). bash_local uses an explicit allowlist of FIRST
TOKENS — only commands starting with one of those run. Combined with
a working-directory restriction (~/AI_Agent, ~/Dev, /tmp), this gives
the local_builder a tight surface area to install deps + invoke
test runners without granting general shell access.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

HOME = Path.home()
ALLOWED_CWDS: tuple[Path, ...] = (
    HOME / "AI_Agent",
    HOME / "Dev",
    Path("/tmp"),
)

# Each entry is a tuple-prefix that the parsed argv must start with.
# Single-word matches like ("ls",) accept any args after; multi-word
# matches like ("npm", "install") require both tokens up front.
ALLOWED_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("ls",), ("cat",), ("mkdir",), ("touch",), ("mv",), ("cp",),
    ("find",), ("grep",), ("head",), ("tail",), ("wc",), ("tree",),
    ("python3",), ("python",),
    ("pip", "install"),
    ("npm", "install"), ("npm", "run"), ("npm", "test"), ("npm", "i"),
    ("git",),
    ("echo",), ("pwd",), ("which",), ("file",),
    ("ffprobe",), ("ffmpeg",),
    ("curl",), ("wget",),
)

# Substrings that immediately reject the command, regardless of prefix.
BLOCK_PATTERNS = (
    "rm -rf /", "rm -rf /*", "rm -rf ~", "rm -rf $HOME",
    "sudo ", "su -",
    "mkfs", "dd if=", "dd of=/dev/", "format ",
    "shutdown", "reboot", "halt ",
    "> /dev/sd", ">/dev/sd",
    "chmod -R 777 /",
    ":(){", "fork()",
)


def _is_allowed_cwd(cwd: Optional[str]) -> Optional[str]:
    """Return error message when cwd is out of scope, None when ok."""
    if cwd is None:
        return None
    p = Path(cwd).expanduser().absolute()
    for root in ALLOWED_CWDS:
        try:
            p.relative_to(root.resolve() if root.exists() else root)
            return None
        except ValueError:
            continue
    return (
        f"cwd {p} is outside allowed roots "
        f"({', '.join(str(r) for r in ALLOWED_CWDS)})"
    )


def _check_blocklist(command: str) -> Optional[str]:
    low = command.lower()
    for pat in BLOCK_PATTERNS:
        if pat in low:
            return f"blocked by pattern {pat!r}"
    return None


def _check_allowlist(command: str) -> Optional[str]:
    """Tokenize and return error if no prefix matches; None on allow."""
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"could not parse command ({exc})"
    if not argv:
        return "empty command"
    for prefix in ALLOWED_PREFIXES:
        if len(argv) >= len(prefix) and tuple(argv[:len(prefix)]) == prefix:
            return None
    return (
        f"first tokens {argv[:2]!r} do not match any allowlist entry. "
        f"Allowed prefixes: {[' '.join(p) for p in ALLOWED_PREFIXES]}"
    )


@tool
def bash_local(command: str, cwd: str = "", timeout: int = 60) -> str:
    """Run an allowlisted bash command. Refuses anything outside the
    explicit allowlist (ls / cat / mkdir / mv / cp / find / grep /
    head / tail / wc / tree / npm install|run|test / pip install /
    python3 / git / echo / pwd / which / file / ffprobe / ffmpeg /
    curl / wget).

    Working directory is restricted to ~/AI_Agent, ~/Dev, or /tmp.

    Returns formatted output:
        exit_code: <int>
        stdout: <text>
        stderr: <text>
    """
    if not command or not command.strip():
        return "refused: empty command"
    err = _check_blocklist(command)
    if err:
        return f"refused: {err}"
    err = _check_allowlist(command)
    if err:
        return f"refused: {err}"
    cwd_err = _is_allowed_cwd(cwd) if cwd else None
    if cwd_err:
        return f"refused: {cwd_err}"
    workdir = str(Path(cwd).expanduser().absolute()) if cwd else None
    timeout = max(1, min(int(timeout), 600))
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            cwd=workdir, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"refused: command exceeded {timeout}s timeout"
    except FileNotFoundError as exc:
        return f"failed: {exc}"
    return (
        f"exit_code: {proc.returncode}\n"
        f"stdout: {proc.stdout.rstrip()}\n"
        f"stderr: {proc.stderr.rstrip()}"
    )


def bash_local_dict(command: str, cwd: Optional[str] = None,
                     timeout: int = 60) -> dict:
    """Same as bash_local but returns a dict for programmatic callers
    (local_builder, smoke tests). Same allowlist/blocklist."""
    if not command or not command.strip():
        return {"refused": "empty command", "exit_code": -1, "stdout": "", "stderr": ""}
    err = _check_blocklist(command) or _check_allowlist(command)
    if err:
        return {"refused": err, "exit_code": -1, "stdout": "", "stderr": ""}
    cwd_err = _is_allowed_cwd(cwd) if cwd else None
    if cwd_err:
        return {"refused": cwd_err, "exit_code": -1, "stdout": "", "stderr": ""}
    workdir = str(Path(cwd).expanduser().absolute()) if cwd else None
    timeout = max(1, min(int(timeout), 600))
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            cwd=workdir, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"refused": f"timeout after {timeout}s", "exit_code": -1,
                "stdout": "", "stderr": ""}
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


BASH_LOCAL_TOOLS = [bash_local]

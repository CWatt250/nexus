"""Phase 27 — scope-restricted git operations for the local builder path.

All commands operate against repos under ~/AI_Agent or ~/Dev. No push
(push stays manual to preserve the safety net the user keeps mentioning).
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

HOME = Path.home()
ALLOWED_REPO_ROOTS: tuple[Path, ...] = (
    HOME / "AI_Agent",
    HOME / "Dev",
)


def _resolve_repo(path: str) -> tuple[Optional[Path], Optional[str]]:
    """Validate that `path` is inside an allowed repo root. Returns
    (path, error). path may not actually be a git repo; the caller
    can handle that — we only block escapes here."""
    if not path:
        return None, "repo path is empty"
    try:
        p = Path(path).expanduser().absolute()
    except Exception as exc:
        return None, f"path expansion failed: {exc}"
    for root in ALLOWED_REPO_ROOTS:
        try:
            p.relative_to(root.resolve() if root.exists() else root)
            return p, None
        except ValueError:
            continue
    return None, (
        f"repo path {p} is outside allowed roots "
        f"({', '.join(str(r) for r in ALLOWED_REPO_ROOTS)})"
    )


def _git(cwd: Path, *args: str, timeout: int = 30) -> dict:
    """Run a git subcommand. Returns dict with exit_code/stdout/stderr."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"timeout after {timeout}s"}
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _format(result: dict, max_chars: int = 4000) -> str:
    body = (
        f"exit_code: {result['exit_code']}\n"
        f"stdout:\n{result['stdout'].rstrip()}\n"
    )
    if result["stderr"].strip():
        body += f"stderr:\n{result['stderr'].rstrip()}\n"
    if len(body) > max_chars:
        body = body[:max_chars] + "\n[...truncated]"
    return body


@tool
def git_status(repo: str) -> str:
    """git status -s on a repo under ~/AI_Agent or ~/Dev."""
    p, err = _resolve_repo(repo)
    if err or p is None:
        return f"refused: {err}"
    return _format(_git(p, "status", "-s"))


@tool
def git_add(repo: str, paths: str = ".") -> str:
    """git add the given comma-separated paths (default '.')."""
    p, err = _resolve_repo(repo)
    if err or p is None:
        return f"refused: {err}"
    items = [s.strip() for s in paths.split(",") if s.strip()] or ["."]
    return _format(_git(p, "add", *items))


@tool
def git_commit(repo: str, message: str) -> str:
    """git commit -m <message>. Refuses empty messages."""
    p, err = _resolve_repo(repo)
    if err or p is None:
        return f"refused: {err}"
    if not message or not message.strip():
        return "refused: commit message is empty"
    return _format(_git(p, "commit", "-m", message))


@tool
def git_log(repo: str, limit: int = 10) -> str:
    """git log --oneline -n <limit>."""
    p, err = _resolve_repo(repo)
    if err or p is None:
        return f"refused: {err}"
    limit = max(1, min(int(limit), 100))
    return _format(_git(p, "log", "--oneline", f"-n{limit}"))


@tool
def git_diff(repo: str, staged: bool = False) -> str:
    """git diff (or --staged when staged=True). Truncates at 4000 chars."""
    p, err = _resolve_repo(repo)
    if err or p is None:
        return f"refused: {err}"
    args = ("diff", "--staged") if staged else ("diff",)
    return _format(_git(p, *args), max_chars=4000)


@tool
def git_branch(repo: str) -> str:
    """List branches (current marked with *)."""
    p, err = _resolve_repo(repo)
    if err or p is None:
        return f"refused: {err}"
    return _format(_git(p, "branch", "--all"))


GIT_LOCAL_TOOLS = [git_status, git_add, git_commit, git_log, git_diff, git_branch]

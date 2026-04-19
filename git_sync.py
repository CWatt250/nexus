"""Auto-commit helper for the Nexus workspace.

Commits only the "content" paths — projects/ (wiki + run-log) and the human
markdown memory files — so runtime state (checkpoints db, current_thread,
sessions.json) stays out of history.

Git identity is injected via `-c` flags per command, so this module does NOT
modify ~/.gitconfig or the repo's stored config.
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

REPO = Path.home() / "AI_Agent"

# Paths auto_commit() will stage. Relative to the repo root.
TRACKED_PATHS = [
    "projects",
    "memory/lessons.md",
    "memory/improvements.md",
    "memory/patterns.md",
]

AUTHOR_NAME = "nexus"
AUTHOR_EMAIL = "nexus@wattbott.local"


def _git(*args: str, capture: bool = True, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a git command inside the Nexus repo. Never touches user config."""
    return subprocess.run(
        ["git", "-c", f"user.name={AUTHOR_NAME}", "-c", f"user.email={AUTHOR_EMAIL}", *args],
        cwd=str(REPO),
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def is_repo() -> bool:
    r = _git("rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == "true"


def _stage_tracked() -> list[str]:
    """Stage TRACKED_PATHS if they exist. Return list of now-staged files."""
    for p in TRACKED_PATHS:
        full = REPO / p
        if full.exists():
            _git("add", "--", p)
    r = _git("diff", "--cached", "--name-only")
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def auto_commit(message: str | None = None) -> bool:
    """Stage + commit the tracked paths. Returns True if a commit was made."""
    if not is_repo():
        return False
    staged = _stage_tracked()
    if not staged:
        return False
    if not message:
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M")
        names = [Path(p).name for p in staged]
        summary = ", ".join(names[:5])
        if len(names) > 5:
            summary += f" (+{len(names) - 5} more)"
        message = f"nexus: {ts} — {summary}"
    r = _git("commit", "-m", message)
    return r.returncode == 0


def push_if_remote() -> bool:
    """Push HEAD to origin if a remote is configured. Silent no-op otherwise."""
    if not is_repo():
        return False
    r = _git("remote")
    if r.returncode != 0 or not r.stdout.strip():
        return False
    r2 = _git("push", timeout=60)
    return r2.returncode == 0


def get_log(n: int = 10) -> list[str]:
    """Return the last n commit lines as '<hash> <date> <subject>'."""
    if not is_repo():
        return []
    r = _git("log", f"-n{int(n)}", "--pretty=format:%h %ad %s", "--date=short")
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "log":
        for line in get_log(int(sys.argv[2]) if len(sys.argv) > 2 else 10):
            print(line)
    else:
        made = auto_commit(sys.argv[1] if len(sys.argv) > 1 else None)
        print("committed" if made else "nothing to commit")

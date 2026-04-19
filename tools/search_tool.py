"""Search tools for Nexus: glob (files by pattern) and grep (content search)."""
from __future__ import annotations

import os
import re
from pathlib import Path

from langchain_core.tools import tool

MAX_HITS = 200
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache"}


def _resolve(path: str) -> Path:
    return Path(os.path.expanduser(path))


@tool
def glob_tool(pattern: str, root: str = ".") -> str:
    """List files matching a glob pattern under `root`. Pattern supports ** for
    recursive match (e.g. "**/*.py"). Returns up to 200 paths, one per line.
    """
    base = _resolve(root)
    if not base.exists():
        return f"ERROR: root not found: {base}"
    matches = []
    for p in sorted(base.glob(pattern)):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        matches.append(str(p))
        if len(matches) >= MAX_HITS:
            matches.append(f"[truncated at {MAX_HITS} results]")
            break
    return "\n".join(matches) if matches else "(no matches)"


@tool
def grep_tool(pattern: str, root: str = ".", glob: str = "**/*") -> str:
    """Search for a regex `pattern` across files under `root` matching `glob`.
    Returns up to 200 'path:line:text' hits.
    """
    base = _resolve(root)
    if not base.exists():
        return f"ERROR: root not found: {base}"
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: bad regex: {exc}"
    hits = []
    for path in base.glob(glob):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if regex.search(line):
                        hits.append(f"{path}:{i}:{line.rstrip()}")
                        if len(hits) >= MAX_HITS:
                            hits.append(f"[truncated at {MAX_HITS} hits]")
                            return "\n".join(hits)
        except (OSError, UnicodeError):
            continue
    return "\n".join(hits) if hits else "(no matches)"

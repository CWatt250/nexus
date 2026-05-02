"""Phase 27 — scope-guarded file write tools for the local builder path.

Sibling to tools/file_tool.py (which is unrestricted). These versions
refuse any write outside an allowlisted root: ~/AI_Agent, ~/Dev, /tmp,
~/Documents, ~/Downloads. The local_builder + intent-routed "build me X"
flow uses these so a runaway LLM can't write to /etc, ~/.ssh, etc.

All three tools tagged MEDIUM tier in TOOLS.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

HOME = Path.home()
ALLOWED_ROOTS: tuple[Path, ...] = tuple(
    p.resolve() for p in (
        HOME / "AI_Agent",
        HOME / "Dev",
        Path("/tmp"),
        HOME / "Documents",
        HOME / "Downloads",
    ) if p.exists() or True  # /tmp always exists; Dev/Documents/Downloads may not
)


def _resolve_in_scope(path: str) -> tuple[Optional[Path], Optional[str]]:
    """Expand ~ and resolve, then assert the result lives under an
    ALLOWED_ROOT. Returns (resolved_path, error_message). On in-scope,
    error_message is None. On out-of-scope, resolved_path is None."""
    if not path:
        return None, "path is empty"
    try:
        # Use absolute() not resolve() — resolve() requires the file to
        # exist for symlink-following on some Python versions. We need
        # to validate paths that don't exist yet (writes).
        p = Path(path).expanduser().absolute()
    except Exception as exc:
        return None, f"path expansion failed: {type(exc).__name__}: {exc}"
    # Defeat ../.. tricks: walk parents looking for a containment match
    # against any allowed root after symlink resolution where possible.
    try:
        # If parent exists, resolve it (catches symlink escapes); the
        # leaf can be non-existent.
        parent = p.parent
        if parent.exists():
            p = parent.resolve() / p.name
    except Exception:
        pass
    for root in ALLOWED_ROOTS:
        try:
            p.relative_to(root)
            return p, None
        except ValueError:
            continue
    return None, (
        f"path {p} is outside the allowed roots "
        f"({', '.join(str(r) for r in ALLOWED_ROOTS)})"
    )


@tool
def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file.

    Scope: only paths under ~/AI_Agent, ~/Dev, /tmp, ~/Documents,
    ~/Downloads are allowed. Anything else is refused with a clear
    error so a runaway LLM can't write to /etc, ~/.ssh, etc.

    Args:
        path: Filesystem path (~ expanded). Parent directories are
            created if missing.
        content: Text to write. UTF-8.

    Returns:
        Status line: "wrote N bytes to <path>" or "refused: <reason>".
    """
    target, err = _resolve_in_scope(path)
    if err or target is None:
        return f"refused: {err}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"write failed: {type(exc).__name__}: {exc}"
    return f"wrote {len(content.encode('utf-8'))} bytes to {target}"


@tool
def edit_file(path: str, old_str: str, new_str: str) -> str:
    """Find-and-replace inside a file. Same scope guards as write_file.

    Args:
        path: Filesystem path. Must already exist.
        old_str: Exact substring to replace. Must appear EXACTLY ONCE
            in the file (otherwise the call refuses; ambiguous edits
            are easy to corrupt). Pass `replace_all=True` is NOT
            supported — make the surrounding context unique instead.
        new_str: Replacement string.

    Returns:
        Status line.
    """
    target, err = _resolve_in_scope(path)
    if err or target is None:
        return f"refused: {err}"
    if not target.exists():
        return f"refused: {target} does not exist (use write_file to create)"
    try:
        original = target.read_text(encoding="utf-8")
    except OSError as exc:
        return f"read failed: {type(exc).__name__}: {exc}"
    if not old_str:
        return "refused: old_str is empty"
    count = original.count(old_str)
    if count == 0:
        return f"refused: old_str not found in {target}"
    if count > 1:
        return (
            f"refused: old_str matches {count} times in {target} — "
            f"make the surrounding context unique to disambiguate"
        )
    updated = original.replace(old_str, new_str, 1)
    try:
        target.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return f"write failed: {type(exc).__name__}: {exc}"
    return f"edited {target}: {len(old_str)} → {len(new_str)} chars"


@tool
def create_directory(path: str) -> str:
    """mkdir -p. Same scope guards as write_file.

    Args:
        path: Directory path (~ expanded). Parents created if missing.

    Returns:
        Status line.
    """
    target, err = _resolve_in_scope(path)
    if err or target is None:
        return f"refused: {err}"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"mkdir failed: {type(exc).__name__}: {exc}"
    return f"directory ready: {target}"


FILE_WRITE_TOOLS = [write_file, edit_file, create_directory]

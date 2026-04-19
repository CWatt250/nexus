"""Filesystem tools for Nexus: read, write, edit."""
from __future__ import annotations

import os
from pathlib import Path

from langchain_core.tools import tool

MAX_READ_BYTES = 512 * 1024


def _resolve(path: str) -> Path:
    return Path(os.path.expanduser(path)).expanduser()


@tool
def file_read_tool(path: str) -> str:
    """Read a file from disk and return its contents.

    `path` may include ~ for the home directory. Returns the file text; for
    files larger than 512KB only the first 512KB is returned with a notice.
    """
    p = _resolve(path)
    if not p.exists():
        return f"ERROR: file not found: {p}"
    if not p.is_file():
        return f"ERROR: not a regular file: {p}"
    data = p.read_bytes()
    truncated = len(data) > MAX_READ_BYTES
    text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[truncated — file is {len(data)} bytes, returned first {MAX_READ_BYTES}]"
    return text


@tool
def file_write_tool(path: str, content: str) -> str:
    """Write (or overwrite) a file with the given content.

    Creates parent directories as needed. Returns the absolute path written
    and the number of bytes written.
    """
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    p.write_bytes(data)
    return f"wrote {len(data)} bytes to {p}"


@tool
def file_edit_tool(path: str, old_string: str, new_string: str) -> str:
    """Find-and-replace in a file. Replaces every occurrence of old_string
    with new_string. Returns the number of replacements made.
    """
    p = _resolve(path)
    if not p.exists():
        return f"ERROR: file not found: {p}"
    text = p.read_text(encoding="utf-8", errors="replace")
    count = text.count(old_string)
    if count == 0:
        return f"ERROR: old_string not found in {p}"
    new_text = text.replace(old_string, new_string)
    p.write_text(new_text, encoding="utf-8")
    return f"replaced {count} occurrence(s) in {p}"

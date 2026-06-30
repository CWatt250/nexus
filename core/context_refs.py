"""@-context references (G1, Hermes-inspired).

Lets a message pull live content inline:
  @file:<path>   → contents of a repo/disk file
  @diff          → `git diff` of the workspace (unstaged + staged)
  @git:<n>       → last n commits (oneline + stat)
  @url:<url>     → fetched page text

`expand_refs(text)` appends a clearly-delimited "[Referenced context]" block
to the message (the user's words are left intact, so routing still sees the
original intent). Everything is capped so a giant file can't blow the context
window. Best-effort: a bad ref yields an inline "(could not read …)" note,
never an exception.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("nexus.context_refs")

ROOT = Path.home() / "AI_Agent"
_MAX_PER_REF = 6000      # chars per individual ref
_MAX_TOTAL = 16000       # chars across all refs in one message

_HAS_RE = re.compile(r"@(?:file:|diff\b|git:\d|url:)", re.IGNORECASE)
_FILE_RE = re.compile(r"@file:([^\s]+)")
_DIFF_RE = re.compile(r"@diff\b", re.IGNORECASE)
_GIT_RE = re.compile(r"@git:(\d{1,3})")
_URL_RE = re.compile(r"@url:(https?://[^\s]+)")


def has_refs(text: str) -> bool:
    return bool(text) and bool(_HAS_RE.search(text))


def _cap(s: str, n: int = _MAX_PER_REF) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n… (truncated, {len(s)} chars total)"


def _read_file(rel: str) -> str:
    p = Path(rel).expanduser()
    if not p.is_absolute():
        p = ROOT / rel
    try:
        if not p.is_file():
            return f"(could not read @file:{rel} — not a file)"
        return _cap(p.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return f"(could not read @file:{rel} — {type(exc).__name__})"


def _git(args: list[str]) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                             text=True, timeout=15)
        return out.stdout if out.returncode == 0 else (out.stderr or "(git error)")
    except Exception as exc:
        return f"(git failed — {type(exc).__name__})"


def _fetch_url(url: str) -> str:
    try:
        import httpx  # noqa: PLC0415
        r = httpx.get(url, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "Nexus/1.0"})
        text = r.text
        # crude tag strip — enough for context, not rendering
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text))
        return _cap(text.strip())
    except Exception as exc:
        return f"(could not fetch @url:{url} — {type(exc).__name__})"


def expand_refs(text: str) -> str:
    """Return `text` with a referenced-context block appended, or `text`
    unchanged when there are no refs / nothing resolved."""
    if not has_refs(text):
        return text
    blocks: list[str] = []
    total = 0

    def add(title: str, body: str) -> None:
        nonlocal total
        if total >= _MAX_TOTAL:
            return
        body = body[: max(0, _MAX_TOTAL - total)]
        total += len(body)
        blocks.append(f"### {title}\n{body.rstrip()}")

    for rel in dict.fromkeys(_FILE_RE.findall(text)):     # dedup, preserve order
        add(f"@file:{rel}", _read_file(rel))
    if _DIFF_RE.search(text):
        diff = _git(["diff"]) + _git(["diff", "--staged"])
        add("@diff (working tree)", _cap(diff.strip() or "(no changes)"))
    for n in dict.fromkeys(_GIT_RE.findall(text)):
        add(f"@git:{n}", _cap(_git(["log", f"-{int(n)}", "--stat", "--oneline"])))
    for url in dict.fromkeys(_URL_RE.findall(text)):
        add(f"@url:{url}", _fetch_url(url))

    if not blocks:
        return text
    return f"{text}\n\n[Referenced context]\n" + "\n\n".join(blocks)

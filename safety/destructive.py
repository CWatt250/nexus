"""Soft-destructive command detection (Phase 14.1).

The hard guardrails layer (`safety.guardrails`) outright blocks shell
commands like `rm -rf` or `mkfs`. This module catches the broader class of
"hard to reverse" operations — git history rewrites, branch deletes,
schema drops, force pushes — that aren't always wrong but should pause
before running.

API:
  is_destructive(command) -> (bool, reason)
    Inspect a shell command. Returns True with a reason if the command is
    soft-destructive. The caller (sandbox / tool wrapper) can then return
    a dry-run summary instead of executing.

  needs_approval(command) -> bool
    Convenience: True iff destructive and not prefixed with `APPROVED:`.

  strip_approval(command) -> str
    Remove the `APPROVED:` / `APPROVE:` prefix so the underlying runner
    sees the bare command.
"""
from __future__ import annotations

import re

# Each entry: (compiled regex, short reason). Order matters — first hit wins.
_DESTRUCTIVE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgit\s+push\s+(.*\s)?(--force|-f)\b"), "git force-push"),
    (re.compile(r"\bgit\s+push\s+(.*\s)?--force-with-lease\b"), "git force-push"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "git reset --hard (discards uncommitted)"),
    (re.compile(r"\bgit\s+clean\s+-[a-zA-Z]*[fdx]"), "git clean -fdx (deletes untracked)"),
    (re.compile(r"\bgit\s+branch\s+-D\b"), "git branch -D (force delete branch)"),
    (re.compile(r"\bgit\s+rebase\s+(.*\s)?--root\b"), "git rebase --root (history rewrite)"),
    (re.compile(r"\bgit\s+filter-(branch|repo)\b"), "git filter (history rewrite)"),
    (re.compile(r"\bgit\s+checkout\s+(--|\.)\s*$"), "git checkout -- . (discards working tree)"),
    (re.compile(r"\bgit\s+restore\s+(\.|--source=)"), "git restore (discards changes)"),
    (re.compile(r"\bgit\s+update-ref\s+-d\b"), "git update-ref -d (deletes ref)"),
    (re.compile(r"\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX)\b", re.IGNORECASE), "SQL DROP"),
    (re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE), "SQL TRUNCATE"),
    (re.compile(r"\bDELETE\s+FROM\b(?!\s+\w+\s+WHERE)", re.IGNORECASE), "DELETE FROM without WHERE"),
    (re.compile(r"\brm\s+(?!.*--help)(\S*\s+)*-[a-zA-Z]*r[a-zA-Z]*\b"), "rm with -r (recursive)"),
    (re.compile(r"\brmdir\s+.*-p\b"), "rmdir -p"),
    (re.compile(r"\bmv\s+\S+\s+/dev/null\b"), "mv to /dev/null"),
    (re.compile(r">\s*/dev/sd[a-z]"), "redirect to /dev/sd*"),
    (re.compile(r"\bdocker\s+system\s+prune\s+(.*\s)?(--all|-a|--volumes)\b"), "docker prune --all"),
    (re.compile(r"\bdocker\s+volume\s+rm\b"), "docker volume rm"),
    (re.compile(r"\bkubectl\s+delete\b"), "kubectl delete"),
    (re.compile(r"\bsupabase\s+db\s+(reset|push)\b"), "supabase db reset/push"),
    (re.compile(r"\balembic\s+downgrade\b"), "alembic downgrade"),
    (re.compile(r"\bnpm\s+publish\b"), "npm publish"),
    (re.compile(r"\bvercel\s+(remove|rm)\b"), "vercel remove"),
]

_APPROVAL_RE = re.compile(r"^\s*APPROVED?\s*:\s*", re.IGNORECASE)


def strip_approval(command: str) -> str:
    """Remove a leading `APPROVED:` (or `APPROVE:`) prefix, ignoring case
    and surrounding whitespace. Returns the command unchanged when there
    is no prefix."""
    return _APPROVAL_RE.sub("", command or "", count=1)


def has_approval_prefix(command: str) -> bool:
    return bool(_APPROVAL_RE.match(command or ""))


def is_destructive(command: str) -> tuple[bool, str]:
    """Return (True, reason) if `command` matches a soft-destructive pattern.
    The match runs against the post-`strip_approval` command so an
    `APPROVED:` prefix doesn't leak into the regex search."""
    bare = strip_approval(command).strip()
    if not bare:
        return False, ""
    for pattern, reason in _DESTRUCTIVE:
        if pattern.search(bare):
            return True, reason
    return False, ""


def needs_approval(command: str) -> bool:
    """True iff the command is destructive AND not prefixed with APPROVED:."""
    if has_approval_prefix(command):
        return False
    return is_destructive(command)[0]


def dry_run_summary(command: str, reason: str) -> str:
    """Build the human-readable response Nexus returns instead of executing."""
    return (
        "DRY-RUN: not executed.\n"
        f"reason: {reason}\n"
        f"command: {command.strip()}\n"
        "to actually run this, prepend `APPROVED:` to the command and call again."
    )

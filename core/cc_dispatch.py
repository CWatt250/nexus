"""Phase 22 — Claude Code dispatch shared core.

Single-source paths, state I/O, risky-prompt detection, cost tracking.
The tool, the watcher daemon, the reporter, the API layer, and the
Telegram listener all read/write through this module so the dispatch
contract stays one definition.

State machine:

    pending_approval  ── go ──>  queued ──> running ──> {done, failed, timeout}
            │
            └── cancel ──> cancelled

Filesystem layout:

    cc_inbox/<id>.md            — queued prompts, picked up FIFO
    cc_inbox/.pending/<id>.md   — risky prompts awaiting Telegram approval
    cc_archive/<id>.md          — completed/cancelled prompts (post-mortem)
    cc_logs/<id>.log            — stdout/stderr from the claude subprocess
    cc_results/<id>.json        — structured outcome (status, commits, summary)
    cc_metrics/dispatches.jsonl — append-only audit + cost log
"""
from __future__ import annotations

import json
import re
import secrets as _secrets_mod
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core import json_safe

ROOT = Path.home() / "AI_Agent"
INBOX = ROOT / "cc_inbox"
PENDING = INBOX / ".pending"
ARCHIVE = ROOT / "cc_archive"
LOGS = ROOT / "cc_logs"
RESULTS = ROOT / "cc_results"
METRICS = ROOT / "cc_metrics"
METRICS_LOG = METRICS / "dispatches.jsonl"

DEFAULT_TIME_BUDGET_MIN = 120

# Patterns that force Telegram approval before dispatch. Conservative —
# false positives cost a tap; false negatives cost data.
RISKY_PATTERNS = [
    r"\bdrop\s+(?:database|table|schema)\b",
    r"\bdelete\s+from\b",
    r"\brm\s+-rf\b",
    r"\bgit\s+push\s+(?:--?force|-f)\b",
    r"\bforce[- ]?push\b",
    r"\bproduction\b",
    r"\bPROD\b",
    r"\bskip\s+tests?\b",
    r"\bbypass\s+(?:auth|security|tests?)\b",
    r"\bmain\s+branch\s+directly\b",
    r"\b--no-verify\b",
    r"\bsudo\s+",
    r"\bdelete\b.*\b(?:all|every|everything)\b",
]
_RISKY_RE = re.compile("|".join(RISKY_PATTERNS), re.IGNORECASE)


@dataclass
class DispatchMeta:
    dispatch_id: str
    label: str
    created_at: str
    time_budget_minutes: int
    requesting_user: str = "colton"
    risky_match: str = ""

    @classmethod
    def new(cls, label: str, time_budget_minutes: int, *,
            requesting_user: str = "colton", risky_match: str = "") -> "DispatchMeta":
        return cls(
            dispatch_id=new_dispatch_id(),
            label=label or "(unlabeled)",
            created_at=datetime.now(timezone.utc).isoformat(),
            time_budget_minutes=time_budget_minutes,
            requesting_user=requesting_user,
            risky_match=risky_match,
        )

    def to_header(self) -> str:
        return (
            "<!--cc-dispatch\n"
            + json_safe.dumps(asdict(self), ensure_ascii=False, indent=2)
            + "\n-->\n"
        )


@dataclass
class DispatchResult:
    dispatch_id: str
    status: str                 # done | failed | timeout | cancelled
    exit_code: Optional[int] = None
    duration_seconds: float = 0.0
    started_at: str = ""
    finished_at: str = ""
    killed_by_timeout: bool = False
    commits_made: list[str] = field(default_factory=list)   # commit subjects
    files_changed: int = 0
    one_line_summary: str = ""
    error_tail: str = ""
    estimated_cost_usd: float = 0.0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0


def ensure_dirs() -> None:
    for p in (INBOX, PENDING, ARCHIVE, LOGS, RESULTS, METRICS):
        p.mkdir(parents=True, exist_ok=True)


def new_dispatch_id() -> str:
    return "cc_" + _secrets_mod.token_hex(4)  # cc_xxxxxxxx (10 chars)


def is_risky(prompt: str) -> str:
    """Return the matched risky pattern (truncated) or empty string if clean."""
    m = _RISKY_RE.search(prompt or "")
    if not m:
        return ""
    return m.group(0)[:80]


def write_prompt(meta: DispatchMeta, prompt_body: str, *, pending: bool) -> Path:
    """Write meta + body to inbox (queued) or .pending (awaiting approval).
    Returns the path written."""
    ensure_dirs()
    target_dir = PENDING if pending else INBOX
    path = target_dir / f"{meta.dispatch_id}.md"
    path.write_text(meta.to_header() + "\n" + (prompt_body or "").rstrip() + "\n", encoding="utf-8")
    return path


def read_prompt(path: Path) -> tuple[Optional[DispatchMeta], str]:
    """Parse a dispatch file. Returns (meta, body) or (None, '') on failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, ""
    if not text.startswith("<!--cc-dispatch"):
        return None, text
    end = text.find("-->")
    if end == -1:
        return None, text
    header = text[len("<!--cc-dispatch"):end].strip()
    body = text[end + 3:].lstrip("\n")
    try:
        data = json.loads(header)
        return DispatchMeta(**data), body
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, body


def find_pending(dispatch_id: str) -> Optional[Path]:
    p = PENDING / f"{dispatch_id}.md"
    return p if p.exists() else None


def find_inbox(dispatch_id: str) -> Optional[Path]:
    p = INBOX / f"{dispatch_id}.md"
    return p if p.exists() else None


def find_any(dispatch_id: str) -> Optional[Path]:
    """Search inbox, pending, and archive for the dispatch file."""
    for d in (INBOX, PENDING, ARCHIVE):
        p = d / f"{dispatch_id}.md"
        if p.exists():
            return p
    return None


def approve(dispatch_id: str) -> Optional[Path]:
    """Move pending -> inbox. Returns the new inbox path or None if not pending."""
    src = find_pending(dispatch_id)
    if not src:
        return None
    dst = INBOX / f"{dispatch_id}.md"
    src.rename(dst)
    return dst


def cancel(dispatch_id: str) -> Optional[Path]:
    """Move pending -> archive (cancelled). Returns archive path or None."""
    src = find_pending(dispatch_id) or find_inbox(dispatch_id)
    if not src:
        return None
    dst = ARCHIVE / f"{dispatch_id}.md"
    src.rename(dst)
    # Mark a result so the reporter knows this one was cancelled pre-flight.
    write_result(DispatchResult(
        dispatch_id=dispatch_id, status="cancelled",
        one_line_summary="cancelled by user before dispatch",
    ))
    return dst


def archive_after_run(dispatch_id: str) -> Optional[Path]:
    """Move inbox -> archive after the watcher finishes a dispatch."""
    src = find_inbox(dispatch_id)
    if not src:
        return None
    dst = ARCHIVE / f"{dispatch_id}.md"
    src.rename(dst)
    return dst


def list_inbox() -> list[Path]:
    """FIFO order — oldest mtime first, hidden files (.pending/) excluded."""
    if not INBOX.exists():
        return []
    files = [p for p in INBOX.iterdir() if p.is_file() and p.suffix == ".md"]
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def list_pending() -> list[Path]:
    if not PENDING.exists():
        return []
    files = [p for p in PENDING.iterdir() if p.is_file() and p.suffix == ".md"]
    files.sort(key=lambda p: p.stat().st_mtime)
    return files


def write_result(result: DispatchResult) -> Path:
    """Append outcome to cc_results/<id>.json (overwrites if exists)."""
    ensure_dirs()
    path = RESULTS / f"{result.dispatch_id}.json"
    path.write_text(json_safe.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_result(dispatch_id: str) -> Optional[DispatchResult]:
    path = RESULTS / f"{dispatch_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return DispatchResult(**data)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def list_results(limit: int = 20) -> list[DispatchResult]:
    """Most recent results first."""
    if not RESULTS.exists():
        return []
    files = sorted(RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out: list[DispatchResult] = []
    for f in files:
        try:
            out.append(DispatchResult(**json.loads(f.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return out


# ── Cost tracking ──────────────────────────────────────────────────────────
# Conservative estimate model: every dispatch is logged with a flat token
# guess that scales with duration. Real cost only known via Anthropic API
# which the CLI doesn't expose. Treated as a soft budget alarm, not billing.
DEFAULT_INPUT_TOKENS_PER_MINUTE = 8000   # ~8k input tokens/minute amortized
DEFAULT_OUTPUT_TOKENS_PER_MINUTE = 1200  # ~1.2k output tokens/minute
SONNET_INPUT_PER_M = 3.00
SONNET_OUTPUT_PER_M = 15.00


def estimate_cost(duration_seconds: float) -> tuple[float, int, int]:
    """Return (usd, input_tokens, output_tokens). Sonnet 4.6 pricing default."""
    minutes = max(0.1, duration_seconds / 60.0)
    in_tok = int(DEFAULT_INPUT_TOKENS_PER_MINUTE * minutes)
    out_tok = int(DEFAULT_OUTPUT_TOKENS_PER_MINUTE * minutes)
    usd = (in_tok / 1_000_000 * SONNET_INPUT_PER_M
           + out_tok / 1_000_000 * SONNET_OUTPUT_PER_M)
    return round(usd, 4), in_tok, out_tok


def log_dispatch(meta: DispatchMeta, result: DispatchResult) -> None:
    """Append one line to cc_metrics/dispatches.jsonl."""
    ensure_dirs()
    line = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "dispatch_id": meta.dispatch_id,
        "label": meta.label,
        "status": result.status,
        "duration_seconds": result.duration_seconds,
        "killed_by_timeout": result.killed_by_timeout,
        "commits": len(result.commits_made),
        "files_changed": result.files_changed,
        "estimated_cost_usd": result.estimated_cost_usd,
        "estimated_input_tokens": result.estimated_input_tokens,
        "estimated_output_tokens": result.estimated_output_tokens,
    }
    with METRICS_LOG.open("a", encoding="utf-8") as f:
        f.write(json_safe.dumps(line, ensure_ascii=False) + "\n")


def month_spend_usd() -> float:
    """Sum estimated_cost_usd for the current calendar month (UTC)."""
    if not METRICS_LOG.exists():
        return 0.0
    now = datetime.now(timezone.utc)
    prefix = now.strftime("%Y-%m")
    total = 0.0
    try:
        with METRICS_LOG.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not str(rec.get("ts", "")).startswith(prefix):
                    continue
                total += float(rec.get("estimated_cost_usd") or 0)
    except OSError:
        pass
    return round(total, 4)


def get_monthly_budget_usd() -> float:
    """Read CLAUDE_CODE_MONTHLY_BUDGET from secrets.yaml/.env. Default $50."""
    try:
        from core import secrets as _secrets
        raw = _secrets.get("CLAUDE_CODE_MONTHLY_BUDGET") or "50"
    except Exception:
        raw = "50"
    try:
        return float(raw)
    except ValueError:
        return 50.0


def budget_status(force: bool = False) -> tuple[str, float, float]:
    """Return (level, spend, budget). level: ok | warn50 | warn80 | over.
    `force` flag bypasses the over-budget block (still reports the level)."""
    spend = month_spend_usd()
    budget = get_monthly_budget_usd()
    if budget <= 0:
        return "ok", spend, budget
    pct = spend / budget
    if pct >= 1.0:
        return "over", spend, budget
    if pct >= 0.8:
        return "warn80", spend, budget
    if pct >= 0.5:
        return "warn50", spend, budget
    return "ok", spend, budget


def queue_summary() -> dict:
    """Snapshot the dispatch queue. Used by Telegram queue status / dashboard."""
    inbox = list_inbox()
    pending = list_pending()
    running_id = ""
    running_started = 0.0
    # The running file is whichever inbox file the dispatcher is currently
    # touching — we mark it via cc_inbox/.lock containing dispatch_id and
    # start epoch (written by workers/cc_dispatcher.py).
    lock = INBOX / ".lock"
    if lock.exists():
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
            running_id = data.get("dispatch_id", "")
            running_started = float(data.get("started_at_epoch") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return {
        "running": {
            "dispatch_id": running_id,
            "elapsed_seconds": round(time.time() - running_started, 1) if running_started else 0,
        } if running_id else None,
        "queued_count": len([p for p in inbox if p.stem != running_id]),
        "queued": [
            {"dispatch_id": p.stem, "mtime": p.stat().st_mtime}
            for p in inbox if p.stem != running_id
        ],
        "pending_approval": [
            {"dispatch_id": p.stem, "mtime": p.stat().st_mtime}
            for p in pending
        ],
    }


def write_lock(dispatch_id: str) -> None:
    """Mark the currently-running dispatch (one-at-a-time invariant)."""
    ensure_dirs()
    (INBOX / ".lock").write_text(
        json.dumps({"dispatch_id": dispatch_id, "started_at_epoch": time.time()}),
        encoding="utf-8",
    )


def clear_lock() -> None:
    lock = INBOX / ".lock"
    try:
        lock.unlink()
    except FileNotFoundError:
        pass

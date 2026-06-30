#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Phase 22.3 — CC dispatch result reporter.

Watches `cc_results/` for new JSON files and fans the outcome to
Telegram + the dashboard event bus. Tracks which dispatches it has
already reported via `cc_results/.reported` so a daemon restart
doesn't double-notify.

Phase 32.2 — multi-message chunking so long results (investigation
findings, gate checklists, ship reports) arrive complete in Telegram
instead of being silently truncated at 4096 chars.

Runs as `nexus-cc-reporter.service` (Restart=always)."""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import cc_dispatch, event_bus  # noqa: E402

REPORTED_INDEX = cc_dispatch.RESULTS / ".reported"
POLL_SECONDS = 3.0

log = logging.getLogger("nexus.cc_reporter")

# Phase 32.2 — ANSI stripping for log extraction
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Heuristic: lines that are part of a markdown/ASCII table
_TABLE_LINE_RE = re.compile(r"^\s*[\|│\+]")


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_reported() -> set[str]:
    if not REPORTED_INDEX.exists():
        return set()
    try:
        return set(
            line.strip()
            for line in REPORTED_INDEX.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except OSError:
        return set()


def _mark_reported(dispatch_id: str) -> None:
    REPORTED_INDEX.parent.mkdir(parents=True, exist_ok=True)
    with REPORTED_INDEX.open("a", encoding="utf-8") as f:
        f.write(dispatch_id + "\n")


# ---------------------------------------------------------------------------
# Phase 32.2 — reporter config loader
# ---------------------------------------------------------------------------

def _load_reporter_config() -> dict:
    """Read result_reporter section from config/cost_limits.yaml.
    Falls back to safe defaults if the file is missing or malformed."""
    defaults: dict = {
        "max_chunk_chars": 4000,
        "max_total_chunks": 10,
        "include_log_tail_for_investigations": True,
        "log_tail_lines": 200,
    }
    try:
        import yaml  # noqa: PLC0415
        cfg_path = ROOT / "config" / "cost_limits.yaml"
        with cfg_path.open(encoding="utf-8") as fh:
            full = yaml.safe_load(fh) or {}
        defaults.update(full.get("result_reporter", {}))
    except Exception:
        pass
    return defaults


# ---------------------------------------------------------------------------
# Phase 32.2 — multi-message chunking
# ---------------------------------------------------------------------------

def _chunk_text(body: str, max_chars: int = 3990) -> list[str]:
    """Split body into ≤max_chars pieces at structural boundaries.

    Phase 41: this logic now lives in the shared ``core.telegram_chunk``
    module so the conversational reply path reuses the exact same
    newline / table / fence-aware packing. Behaviour is unchanged for the
    dispatch reporter — this delegates to ``chunk_structured``.
    """
    from core.telegram_chunk import chunk_structured  # noqa: PLC0415
    return chunk_structured(body, max_chars)


# ---------------------------------------------------------------------------
# Phase 32.2 — log body extraction
# ---------------------------------------------------------------------------

def _read_log_body(dispatch_id: str, tail_lines: int = 200) -> str:
    """Return the last tail_lines of cc_logs/<dispatch_id>.log, ANSI-stripped."""
    log_path = cc_dispatch.LOGS / f"{dispatch_id}.log"
    if not log_path.exists():
        return ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = _ANSI_RE.sub("", text).strip()
    lines = text.splitlines()
    if tail_lines and len(lines) > tail_lines:
        lines = lines[-tail_lines:]
    return "\n".join(lines)


def _is_investigation(result: cc_dispatch.DispatchResult) -> bool:
    """True when the dispatch made no git changes and ran for >60 s.
    In that case the entire deliverable is Claude's reply in the log."""
    return (
        result.files_changed == 0
        and not result.commits_made
        and result.duration_seconds > 60
    )


def _get_build_context() -> tuple[str, list[str]]:
    """Return (first_3_lines_of_last_commit_msg, top_5_changed_files).
    Uses the AI_Agent git repo. Best-effort; returns ('', []) on failure."""
    commit_msg = ""
    files: list[str] = []
    try:
        p1 = subprocess.run(
            ["git", "-C", str(ROOT), "log", "--format=%B", "-1"],
            capture_output=True, text=True, timeout=5,
        )
        if p1.returncode == 0:
            msg_lines = [l for l in p1.stdout.strip().splitlines() if l.strip()][:3]
            commit_msg = "\n".join(msg_lines)
    except Exception:
        pass
    try:
        p2 = subprocess.run(
            ["git", "-C", str(ROOT), "diff", "--name-only", "HEAD^", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if p2.returncode == 0:
            files = [l.strip() for l in p2.stdout.splitlines() if l.strip()][:5]
    except Exception:
        pass
    return commit_msg, files


# ---------------------------------------------------------------------------
# Telegram send helpers
# ---------------------------------------------------------------------------

def _telegram_raw(text: str) -> None:
    """Send a single Telegram message (no length guard — use _telegram_chunked
    for potentially-long content)."""
    try:
        from tools import telegram_tool  # noqa: PLC0415
        telegram_tool.notify_sync(text, parse_mode=None)
    except Exception as exc:
        log.debug("telegram notify failed: %s", exc)


# Keep the old name as an alias so callers outside this module still work.
_telegram = _telegram_raw


def _telegram_chunked(text: str, dispatch_id: str = "") -> None:
    """Send text as one or more Telegram messages.

    - Splits at newline/table boundaries into ≤max_chunk_chars pieces.
    - Prepends [N/M] to each chunk when there are multiple.
    - If splitting would produce more than max_total_chunks, sends the
      first (max_total_chunks-1) chunks then a "see logs" tail message.
    """
    cfg = _load_reporter_config()
    max_chars = int(cfg.get("max_chunk_chars", 4000))
    max_total = int(cfg.get("max_total_chunks", 10))

    # Reserve space for the [N/M]\n marker (up to "[10/10]\n" = 9 chars)
    chunks = _chunk_text(text, max_chars - 10)

    if not chunks:
        return

    total = len(chunks)

    if total > max_total:
        chunks = chunks[: max_total - 1]
        overflow = (
            f"[{max_total}/{total}] …output truncated "
            f"({total - max_total + 1} more chunk(s) not shown).\n"
            f"Full output: `~/AI_Agent/cc_logs/{dispatch_id}.log`"
        )
        chunks.append(overflow)
        total = max_total

    for n, chunk in enumerate(chunks, 1):
        msg = f"[{n}/{total}]\n{chunk}" if total > 1 else chunk
        _telegram_raw(msg)


# ---------------------------------------------------------------------------
# Phase 28 attachment helper
# ---------------------------------------------------------------------------

_MAX_ATTACH_BYTES = 10 * 1024 * 1024


def _telegram_send_file(path: str, caption: str = "") -> None:
    try:
        from tools import telegram_tool  # noqa: PLC0415
        p = Path(path).expanduser()
        if not p.exists() or not p.is_file():
            log.debug("attach skip — missing %s", path)
            return
        if p.stat().st_size > _MAX_ATTACH_BYTES:
            log.debug("attach skip — %s > 10MB", path)
            return
        telegram_tool.send_file_sync(str(p), caption=caption)
    except Exception as exc:
        log.debug("telegram file send failed for %s: %s", path, exc)


def _attach_artifacts(result: cc_dispatch.DispatchResult) -> None:
    artifacts = getattr(result, "artifact_paths", []) or []
    for p in artifacts[:5]:
        caption = f"{Path(p).name} (from {result.dispatch_id})"
        _telegram_send_file(p, caption)


# ---------------------------------------------------------------------------
# Phase 32.2 — enriched Telegram formatter
# ---------------------------------------------------------------------------

def _format_telegram(meta_label: str, result: cc_dispatch.DispatchResult) -> str:
    """Compose the user-facing Telegram message for one dispatch.

    Phase 32.2 changes:
    - Investigation dispatches (no git changes, ran >60 s) get the full
      Claude reply from cc_logs/<id>.log appended after the header.
    - Build dispatches get top-5 changed files + first 3 commit message lines.
    - All other fields unchanged from Phase 22.3.
    The returned string may be much longer than 4096 chars; the caller
    must pass it through _telegram_chunked().
    """
    dur_min = result.duration_seconds / 60
    cost = f"~${result.estimated_cost_usd:.3f}" if result.estimated_cost_usd else ""
    tier_line = ""
    if getattr(result, "tier", "") and getattr(result, "model_used", ""):
        tier_line = f"\nTier: {result.tier} ({result.model_used})"

    if result.status == "done":
        commits_line = ""
        if result.commits_made:
            preview = ", ".join(result.commits_made[:3])
            if len(preview) > 200:
                preview = preview[:200] + "…"
            commits_line = f"\n{len(result.commits_made)} commit(s): {preview}"
        files_line = (
            f"\nFiles changed: {result.files_changed}" if result.files_changed else ""
        )
        artifact_line = ""
        artifacts = getattr(result, "artifact_paths", []) or []
        if artifacts:
            names = [Path(p).name for p in artifacts[:5]]
            artifact_line = f"\nArtifacts: {', '.join(names)}"
        cost_line = f"\nCost: {cost}" if cost else ""
        review_line = ""
        if getattr(result, "needs_review", False):
            notes = (getattr(result, "review_notes", "") or "")[:240]
            review_line = f"\n⚠️ needs_review: {notes}"

        cfg = _load_reporter_config()

        # --- Investigation dispatch: ship the full log body ---
        if _is_investigation(result) and cfg.get("include_log_tail_for_investigations", True):
            log_body = _read_log_body(
                result.dispatch_id, int(cfg.get("log_tail_lines", 200))
            )
            summary_line = (
                f"\n[investigation — full findings below]"
                if log_body
                else f"\nSummary: {result.one_line_summary}"
            )
            header = (
                f"✅ `{result.dispatch_id}` — {meta_label} done in {dur_min:.1f}m."
                f"{tier_line}{commits_line}{files_line}{artifact_line}"
                f"{summary_line}{cost_line}{review_line}"
                f"\n\nReply `restart {result.dispatch_id}` to bounce nexus-* services, "
                f"or `restart nexus-api` for one."
            )
            if log_body:
                return header + "\n\n---\n" + log_body
            return header

        # --- Build dispatch: add top-5 files + commit message ---
        summary_line = (
            f"\nSummary: {result.one_line_summary}" if result.one_line_summary else ""
        )
        extra_build = ""
        if result.files_changed > 0 or result.commits_made:
            commit_msg, changed_files = _get_build_context()
            if changed_files:
                extra_build += "\nTop files: " + ", ".join(changed_files)
            if commit_msg:
                extra_build += f"\nCommit msg:\n{commit_msg}"

        return (
            f"✅ `{result.dispatch_id}` — {meta_label} done in {dur_min:.1f}m."
            f"{tier_line}{commits_line}{files_line}{artifact_line}"
            f"{summary_line}{cost_line}{review_line}{extra_build}"
            f"\n\nReply `restart {result.dispatch_id}` to bounce nexus-* services, "
            f"or `restart nexus-api` for one."
        )

    if result.status == "timeout":
        partial = ""
        if result.commits_made:
            partial = f" Made {len(result.commits_made)} commit(s) before kill."
        return (
            f"⏰ `{result.dispatch_id}` — {meta_label} hit time limit "
            f"at {dur_min:.1f}m.{partial}\n"
            f"Logs: ~/AI_Agent/cc_logs/{result.dispatch_id}.log\n"
            f"Reply `extend {result.dispatch_id} <minutes>` to re-dispatch with more budget."
        )

    if result.status == "cancelled":
        return f"🛑 `{result.dispatch_id}` — {meta_label} cancelled before dispatch."

    err = (result.error_tail or "")[-200:].replace("\n", " ")
    return (
        f"⚠️ `{result.dispatch_id}` — {meta_label} failed after {dur_min:.1f}m."
        f"\nLast error: `{err}`"
        f"\nLogs: ~/AI_Agent/cc_logs/{result.dispatch_id}.log"
        f"\nReply `retry {result.dispatch_id}` to re-run the same prompt."
    )


# ---------------------------------------------------------------------------
# Archive / meta helpers
# ---------------------------------------------------------------------------

def _meta_for(dispatch_id: str) -> str:
    archive_path = cc_dispatch.ARCHIVE / f"{dispatch_id}.md"
    if not archive_path.exists():
        return dispatch_id
    meta, _ = cc_dispatch.read_prompt(archive_path)
    return meta.label if meta else dispatch_id


# ---------------------------------------------------------------------------
# Phase 28 — memory bridge
# ---------------------------------------------------------------------------

_LOG_PATH = Path.home() / "AI_Agent" / "wiki" / "log.md"
_ROUTER_ENTITY_PATH = (
    Path.home() / "AI_Agent" / "wiki" / "entities" / "coding-router.md"
)


def _log_dispatch_to_wiki(label: str, result: cc_dispatch.DispatchResult) -> None:
    if result.status not in ("done", "failed", "timeout"):
        return
    if not getattr(result, "tier", ""):
        return
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        line = (
            f"\n{ts} — Phase 28 dispatch | tier={result.tier} | "
            f'"{label[:50]}" | {result.duration_seconds:.1f}s | '
            f"${result.estimated_cost_usd:.4f} | {result.status}"
        )
        if not _LOG_PATH.exists():
            return
        existing = _LOG_PATH.read_text(encoding="utf-8")
        if "\n---\n" in existing:
            head, body = existing.split("\n---\n", 1)
            new_text = head + "\n---\n" + line + "\n" + body
        else:
            new_text = existing + line + "\n"
        _LOG_PATH.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        log.debug("wiki log append failed: %s", exc)


def _bump_router_entity_stats(result: cc_dispatch.DispatchResult) -> None:
    if not getattr(result, "tier", ""):
        return
    try:
        from datetime import datetime as _dt
        import json as _json
        metrics = cc_dispatch.METRICS_LOG
        if not metrics.exists():
            return
        per_tier: dict[str, dict] = {}
        total_cost = 0.0
        total_done = 0
        total_count = 0
        with metrics.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = _json.loads(raw)
                except _json.JSONDecodeError:
                    continue
                raw_tier = rec.get("tier") or ""
                if not raw_tier:
                    continue
                tier = cc_dispatch.normalize_tier(raw_tier)
                total_count += 1
                cost = float(rec.get("estimated_cost_usd") or 0)
                total_cost += cost
                done = rec.get("status") == "done"
                if done:
                    total_done += 1
                stats = per_tier.setdefault(tier, {"count": 0, "done": 0, "cost": 0.0})
                stats["count"] += 1
                stats["cost"] += cost
                if done:
                    stats["done"] += 1
        success_rate = (total_done / total_count * 100) if total_count else 0.0
        tier_order = ["max", "local", "quick", "flash", "pro", "api"]
        rows = []
        for tier in tier_order + sorted(t for t in per_tier if t not in tier_order):
            if tier not in per_tier:
                continue
            s = per_tier[tier]
            sr = (s["done"] / s["count"] * 100) if s["count"] else 0.0
            rows.append(
                f"| {tier} | {s['count']} | {s['done']} | ${s['cost']:.4f} | {sr:.0f}% |"
            )
        body = (
            f"---\n"
            f"name: coding-router\n"
            f"type: entity\n"
            f"updated: {_dt.now(timezone.utc).isoformat()}\n"
            f"---\n\n"
            f"# Coding Router (Phase 28 + 29)\n\n"
            f"Tracks dispatches routed through the tier-aware Claude Code "
            f"dispatcher. Source: `cc_metrics/dispatches.jsonl`. Last "
            f"update auto-rewritten by `workers/cc_result_reporter`.\n\n"
            f"Phase 29 made `/max` the default for complex builds — Colton "
            f"already pays for the Max subscription, so the API-key path "
            f"became a rare fallback.\n\n"
            f"## Cumulative\n\n"
            f"- Total dispatches: **{total_count}**\n"
            f"- Successful: **{total_done}** ({success_rate:.0f}%)\n"
            f"- Total estimated cost (API-billed only): **${total_cost:.4f}**\n\n"
            f"## By tier\n\n"
            f"| tier | count | done | est. cost | success |\n"
            f"|------|-------|------|-----------|---------|\n"
            + ("\n".join(rows) if rows else "| (none yet) | 0 | 0 | $0.00 | 0% |")
            + "\n\n"
            f"## Slash commands (Phase 29 ladder)\n\n"
            f"- `/max <prompt>`   — Claude Sonnet 4.6 via Max plan ($0 marginal) **default**\n"
            f"- `/code <prompt>`  — DeepSeek V4-Flash (~$0.005)\n"
            f"- `/pro <prompt>`   — DeepSeek V4-Pro (~$0.05)\n"
            f"- `/api <prompt>`   — Sonnet 4.6 via API key (~$0.10–1.00)\n"
            f"- `/local <prompt>` — qwen3-coder:30b local ($0)\n"
            f"- `/quick <prompt>` — qwen3:4b chat ($0)\n"
            f"- `/real <prompt>`  — *deprecated alias for /api*\n\n"
            f"## Routing without a slash\n\n"
            f"- Casual chat → `/quick`\n"
            f"- `make a quick/simple/tiny X` → `/local`\n"
            f"- `build me X` / `create X` / `make me X` / `code X` → `/max`\n"
        )
        _ROUTER_ENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ROUTER_ENTITY_PATH.write_text(body, encoding="utf-8")
    except Exception as exc:
        log.debug("router entity update failed: %s", exc)


def _wiki_ingest_dispatch(dispatch_id: str, label: str, result_path: Path) -> None:
    try:
        from tools import wiki_tool  # noqa: PLC0415
        msg = wiki_tool.wiki_ingest.invoke({
            "source": str(result_path),
            "source_type": "dispatch_result",
        })
        log.info("wiki_ingest dispatch=%s → %s", dispatch_id, msg)
    except Exception as exc:
        log.debug("wiki_ingest failed for %s: %s", dispatch_id, exc)


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------

def _process_new_results(reported: set[str]) -> None:
    if not cc_dispatch.RESULTS.exists():
        return
    files = sorted(
        cc_dispatch.RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime
    )
    for f in files:
        dispatch_id = f.stem
        if dispatch_id in reported:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result = cc_dispatch.DispatchResult(**data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("skipping bad result %s: %s", f, exc)
            reported.add(dispatch_id)
            continue

        label = _meta_for(dispatch_id)
        msg = _format_telegram(label, result)
        log.info(
            "reporting dispatch %s status=%s tier=%s investigation=%s",
            dispatch_id,
            result.status,
            getattr(result, "tier", ""),
            _is_investigation(result),
        )
        # Phase 32.2 — chunked send so long results arrive complete
        _telegram_chunked(msg, dispatch_id)

        if result.status == "done":
            try:
                _attach_artifacts(result)
            except Exception as exc:
                log.debug("attach_artifacts crashed for %s: %s", dispatch_id, exc)
        try:
            event_bus.publish_remote(
                "cc_dispatch_reported",
                dispatch_id=dispatch_id,
                label=label,
                status=result.status,
                duration_seconds=result.duration_seconds,
                commits=len(result.commits_made),
                tier=getattr(result, "tier", ""),
            )
        except Exception:
            pass
        try:
            _log_dispatch_to_wiki(label, result)
            _bump_router_entity_stats(result)
        except Exception as exc:
            log.debug("memory bridge crashed for %s: %s", dispatch_id, exc)
        if not label.startswith("wiki-extract:"):
            _wiki_ingest_dispatch(dispatch_id, label, f)
        _mark_reported(dispatch_id)
        reported.add(dispatch_id)


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    cc_dispatch.ensure_dirs()
    reported = _load_reported()
    log.info(
        "cc_reporter ready (pid=%d, %d previously reported)",
        os.getpid(),
        len(reported),
    )

    stop = {"flag": False}

    def _handler(_signum, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)

    while not stop["flag"]:
        try:
            _process_new_results(reported)
        except Exception as exc:
            log.exception("reporter loop error: %s", exc)
        time.sleep(POLL_SECONDS)

    log.info("cc_reporter exiting cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())

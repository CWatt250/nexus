#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Phase 22.3 — CC dispatch result reporter.

Watches `cc_results/` for new JSON files and fans the outcome to
Telegram + the dashboard event bus. Tracks which dispatches it has
already reported via `cc_results/.reported` so a daemon restart
doesn't double-notify.

Runs as `nexus-cc-reporter.service` (Restart=always)."""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import cc_dispatch, event_bus  # noqa: E402

REPORTED_INDEX = cc_dispatch.RESULTS / ".reported"
POLL_SECONDS = 3.0

log = logging.getLogger("nexus.cc_reporter")


def _load_reported() -> set[str]:
    if not REPORTED_INDEX.exists():
        return set()
    try:
        return set(line.strip() for line in REPORTED_INDEX.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return set()


def _mark_reported(dispatch_id: str) -> None:
    REPORTED_INDEX.parent.mkdir(parents=True, exist_ok=True)
    with REPORTED_INDEX.open("a", encoding="utf-8") as f:
        f.write(dispatch_id + "\n")


def _format_telegram(meta_label: str, result: cc_dispatch.DispatchResult) -> str:
    """Compose the user-facing Telegram message for one dispatch."""
    dur_min = result.duration_seconds / 60
    cost = f"~${result.estimated_cost_usd:.3f}" if result.estimated_cost_usd else ""
    if result.status == "done":
        commits_line = ""
        if result.commits_made:
            preview = ", ".join(result.commits_made[:3])
            if len(preview) > 200:
                preview = preview[:200] + "…"
            commits_line = f"\n{len(result.commits_made)} commit(s): {preview}"
        files_line = f"\nFiles changed: {result.files_changed}" if result.files_changed else ""
        summary_line = f"\nSummary: {result.one_line_summary}" if result.one_line_summary else ""
        cost_line = f"\nCost: {cost}" if cost else ""
        return (
            f"✅ `{result.dispatch_id}` — {meta_label} done in {dur_min:.1f}m."
            f"{commits_line}{files_line}{summary_line}{cost_line}"
            f"\n\nReply `restart {result.dispatch_id}` to bounce nexus-* services, "
            f"or `restart nexus-api` for one."
        )
    if result.status == "timeout":
        partial = ""
        if result.commits_made:
            partial = f" Made {len(result.commits_made)} commit(s) before kill."
        return (
            f"⏰ `{result.dispatch_id}` — {meta_label} hit time limit "
            f"at {dur_min:.1f}m.{partial}\nLogs: ~/AI_Agent/cc_logs/{result.dispatch_id}.log"
            f"\nReply `extend {result.dispatch_id} <minutes>` to re-dispatch with more budget."
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


def _meta_for(dispatch_id: str) -> str:
    """Pull the original meta label out of cc_archive (or fall back)."""
    archive_path = cc_dispatch.ARCHIVE / f"{dispatch_id}.md"
    if not archive_path.exists():
        return dispatch_id
    meta, _ = cc_dispatch.read_prompt(archive_path)
    return meta.label if meta else dispatch_id


def _telegram(text: str) -> None:
    try:
        from tools import telegram_tool  # noqa: PLC0415
        telegram_tool.notify_sync(text, parse_mode=None)
    except Exception as exc:
        log.debug("telegram notify failed: %s", exc)


def _wiki_ingest_dispatch(dispatch_id: str, label: str, result_path: Path) -> None:
    """Phase 25 integration — every dispatch result becomes a wiki source so
    its findings can be extracted into entities/concepts/decisions.

    We pass the raw JSON result path to wiki_ingest so the file lands in
    wiki/sources/ with frontmatter and triggers the extractor worker.
    Best-effort: silent on import or write failure (the dispatch is
    already reported via Telegram + event_bus, the wiki is bonus durability).
    """
    try:
        from tools import wiki_tool  # noqa: PLC0415
        # wiki_ingest expects a string source — the result file's path works.
        msg = wiki_tool.wiki_ingest.invoke({
            "source": str(result_path),
            "source_type": "dispatch_result",
        })
        log.info("wiki_ingest dispatch=%s → %s", dispatch_id, msg)
    except Exception as exc:
        log.debug("wiki_ingest failed for %s: %s", dispatch_id, exc)


def _process_new_results(reported: set[str]) -> None:
    if not cc_dispatch.RESULTS.exists():
        return
    files = sorted(cc_dispatch.RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime)
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
        log.info("reporting dispatch %s status=%s", dispatch_id, result.status)
        _telegram(msg)
        try:
            event_bus.publish_remote(
                "cc_dispatch_reported",
                dispatch_id=dispatch_id, label=label, status=result.status,
                duration_seconds=result.duration_seconds,
                commits=len(result.commits_made),
            )
        except Exception:
            pass
        # Phase 25 — fan dispatch result into the wiki sources layer so
        # the extractor can fold findings back into curated pages. Skip
        # for wiki-extract dispatches themselves to avoid feedback loops.
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
    log.info("cc_reporter ready (pid=%d, %d previously reported)",
             os.getpid(), len(reported))

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

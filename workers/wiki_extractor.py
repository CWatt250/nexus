#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Phase 25.5 — Knowledge Garden source extractor.

Watches `wiki/.extractor_inbox/` (filesystem-as-queue, dropped by
`wiki_ingest`) and for each new source dispatches a small Claude Code
job (15-min budget) with a prompt asking it to read the source and
update relevant entity / concept / decision pages following SCHEMA.md.

We deliberately use the existing CC dispatch system (core.cc_dispatch)
rather than spawning `claude` ourselves — that way the extraction shows
up in the queue, respects the monthly budget, gets archived, and
notifies via Telegram on completion just like any other dispatch.

Each processed trigger is moved to `.extractor_inbox/.done/` so a
restart doesn't redispatch. Logs all extractions to wiki/log.md.

Runs as `nexus-wiki-extractor.service` (Restart=always).
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import cc_dispatch  # noqa: E402

WIKI_ROOT = ROOT / "wiki"
INBOX = WIKI_ROOT / ".extractor_inbox"
DONE = INBOX / ".done"
LOG_MD = WIKI_ROOT / "log.md"
POLL_SECONDS = 5.0
EXTRACT_BUDGET_MIN = 15

log = logging.getLogger("nexus.wiki_extractor")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def _append_log(line: str) -> None:
    if not LOG_MD.exists():
        LOG_MD.write_text("# Wiki Journal\n\n", encoding="utf-8")
    existing = LOG_MD.read_text(encoding="utf-8")
    sep = existing.find("---\n")
    if sep == -1:
        LOG_MD.write_text(existing + f"\n{line}\n", encoding="utf-8")
        return
    insert_at = existing.find("\n", sep + 4) + 1
    new = existing[:insert_at] + f"\n{line}\n" + existing[insert_at:]
    LOG_MD.write_text(new, encoding="utf-8")


def _build_extraction_prompt(source_path: Path) -> str:
    rel = source_path.relative_to(ROOT)
    return f"""You are a wiki maintainer for the Nexus knowledge garden at ~/AI_Agent/wiki/.

A new source was just ingested: `~/{rel}`

Your job:
1. Read ~/AI_Agent/wiki/SCHEMA.md to refresh on the contract.
2. Read the source file at ~/{rel}.
3. Skim ~/AI_Agent/wiki/index.md and any directly-relevant pages under entities/, concepts/, decisions/.
4. Decide what (if anything) should change in the curated layer:
   - If a new entity/concept/decision is warranted, create it (proper frontmatter, add to index.md).
   - If existing pages need facts updated, edit them and bump `last_updated`.
   - If the source is duplicative or trivial, do nothing — note it in wiki/log.md and stop.
5. For every change, append one line to wiki/log.md (newest at top, under the `---` block).
6. Cite the source file in the page's `sources:` frontmatter list.

Constraints:
- DO NOT modify files in wiki/sources/ — those are immutable.
- DO NOT create more than 3 new pages from a single source.
- Match SCHEMA.md frontmatter exactly. Use kebab-case slugs.
- Keep edits surgical. No rewriting unrelated sections.

Time budget: {EXTRACT_BUDGET_MIN} minutes. When done, commit your changes
with message `wiki(extractor): integrate {source_path.name}`.
"""


def _dispatch_extraction(source_path: Path) -> str | None:
    """Queue a Claude Code dispatch for this source. Returns dispatch_id."""
    if not source_path.exists():
        log.warning("source vanished before dispatch: %s", source_path)
        return None
    label = f"wiki-extract: {source_path.name[:40]}"
    meta = cc_dispatch.DispatchMeta.new(
        label=label,
        time_budget_minutes=EXTRACT_BUDGET_MIN,
    )
    body = _build_extraction_prompt(source_path)
    cc_dispatch.write_prompt(meta, body, pending=False)
    log.info("dispatched %s for %s", meta.dispatch_id, source_path.name)
    return meta.dispatch_id


def _process_inbox() -> int:
    if not INBOX.exists():
        return 0
    DONE.mkdir(parents=True, exist_ok=True)
    triggers = sorted(INBOX.glob("*"), key=lambda p: p.stat().st_mtime)
    n = 0
    for t in triggers:
        if not t.is_file():
            continue
        if t.name.startswith("."):
            continue
        try:
            payload = json.loads(t.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("bad trigger %s: %s", t, exc)
            t.rename(DONE / f"bad_{t.name}")
            continue
        source_str = payload.get("source", "")
        source = Path(source_str)
        if not source.exists():
            log.warning("trigger references missing source %s", source_str)
            t.rename(DONE / t.name)
            continue
        dispatch_id = _dispatch_extraction(source)
        if dispatch_id:
            _append_log(
                f"{_today()} — extractor: queued `{dispatch_id}` for "
                f"`wiki/sources/{source.name}`"
            )
            n += 1
        t.rename(DONE / t.name)
    return n


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    INBOX.mkdir(parents=True, exist_ok=True)
    DONE.mkdir(parents=True, exist_ok=True)
    cc_dispatch.ensure_dirs()
    log.info("wiki_extractor ready (pid=%d, inbox=%s)", os.getpid(), INBOX)

    stop = {"flag": False}

    def _handler(_signum, _frame):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)

    while not stop["flag"]:
        try:
            dispatched = _process_inbox()
            if dispatched:
                log.info("queued %d extractions this tick", dispatched)
        except Exception as exc:
            log.exception("extractor loop error: %s", exc)
        time.sleep(POLL_SECONDS)

    log.info("wiki_extractor exiting cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Phase 25.3.2 — research/ → wiki/sources/ auto-ingest.

Watches ~/AI_Agent/research/ for new or modified files and pipes each
through wiki_ingest so it lands in wiki/sources/ with frontmatter and
queues the wiki extractor to update curated pages.

Polled (not inotify) — same pattern as file_watcher.py — because
research/ is low-traffic and we already pay 5s latency on the wiki
extractor side. Idempotent via a `seen` ledger keyed by path+mtime.
"""
from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import wiki_tool  # noqa: E402

RESEARCH_DIR = ROOT / "research"
SEEN_DB = ROOT / "memory" / "research_watcher_seen.txt"
POLL_SECONDS = 10.0
EXTENSIONS = {".md", ".txt", ".json"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s research-watcher %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("nexus.research_watcher")


def _load_seen() -> set[str]:
    if not SEEN_DB.exists():
        return set()
    try:
        return {ln.strip() for ln in SEEN_DB.read_text(encoding="utf-8").splitlines() if ln.strip()}
    except OSError:
        return set()


def _mark_seen(key: str) -> None:
    SEEN_DB.parent.mkdir(parents=True, exist_ok=True)
    with SEEN_DB.open("a", encoding="utf-8") as f:
        f.write(key + "\n")


def _key(path: Path) -> str:
    try:
        return f"{path.resolve()}:{int(path.stat().st_mtime)}"
    except OSError:
        return str(path.resolve())


def _ingest(path: Path) -> bool:
    try:
        msg = wiki_tool.wiki_ingest.invoke({
            "source": str(path),
            "source_type": "research",
        })
        log.info("ingested %s → %s", path.name, msg)
        return True
    except Exception as exc:
        log.warning("ingest failed for %s: %s", path, exc)
        return False


def _scan(seen: set[str]) -> int:
    if not RESEARCH_DIR.exists():
        return 0
    n = 0
    for p in sorted(RESEARCH_DIR.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in EXTENSIONS:
            continue
        k = _key(p)
        if k in seen:
            continue
        if _ingest(p):
            _mark_seen(k)
            seen.add(k)
            n += 1
    return n


def backfill() -> int:
    """One-shot: ingest every existing research/*.md not yet seen."""
    seen = _load_seen()
    n = _scan(seen)
    log.info("backfill complete: %d new ingestions", n)
    return n


def main() -> int:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    seen = _load_seen()
    log.info("research_watcher ready (dir=%s, %d previously seen)", RESEARCH_DIR, len(seen))

    # First pass = backfill anything new since last run.
    initial = _scan(seen)
    if initial:
        log.info("initial backfill ingested %d files", initial)

    stop = {"flag": False}

    def _handler(_signum, _frame):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)

    while not stop["flag"]:
        try:
            _scan(seen)
        except Exception as exc:
            log.exception("scan error: %s", exc)
        time.sleep(POLL_SECONDS)

    log.info("research_watcher exiting cleanly")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "backfill":
        sys.exit(0 if backfill() >= 0 else 1)
    sys.exit(main())

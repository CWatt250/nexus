#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Nexus file watcher.

Watches ~/Downloads and ~/Documents for newly-arrived office documents
(PDF, Word, Excel, PowerPoint) and auto-converts them to markdown via
MarkItDown, storing the result in Chroma RAG memory tagged with the
original path. Runs as the nexus-file-watcher systemd service."""
from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.markitdown_tool import convert  # noqa: E402
from tools.rag_tool import add_documents  # noqa: E402

WATCH_DIRS = [Path.home() / "Downloads", Path.home() / "Documents"]
EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
SEEN_DB = Path.home() / "AI_Agent" / "memory" / "file_watcher_seen.txt"
SETTLE_SECONDS = 3  # wait for the file to finish copying
MIN_SIZE = 128  # skip near-empty files

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s file-watcher %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("nexus.file_watcher")


def _load_seen() -> set[str]:
    if not SEEN_DB.exists():
        return set()
    try:
        return {ln.strip() for ln in SEEN_DB.read_text().splitlines() if ln.strip()}
    except OSError:
        return set()


def _mark_seen(path: Path) -> None:
    SEEN_DB.parent.mkdir(parents=True, exist_ok=True)
    with SEEN_DB.open("a", encoding="utf-8") as f:
        f.write(str(path.resolve()) + "\n")


def _is_settled(path: Path) -> bool:
    """Two stat calls SETTLE_SECONDS apart returning the same size."""
    try:
        s1 = path.stat().st_size
    except OSError:
        return False
    time.sleep(SETTLE_SECONDS)
    try:
        s2 = path.stat().st_size
    except OSError:
        return False
    return s1 == s2 and s1 >= MIN_SIZE


def _ingest(path: Path) -> None:
    log.info("converting %s", path)
    try:
        text, meta = convert(str(path))
    except Exception as exc:
        log.warning("markitdown failed on %s: %s: %s", path, type(exc).__name__, exc)
        return
    if not text.strip():
        log.info("empty conversion for %s, skipping RAG", path)
        return
    meta.update({"watcher": "file", "ext": path.suffix.lower()})
    try:
        ids = add_documents([text], metadatas=[meta])
    except Exception as exc:
        log.warning("RAG store failed for %s: %s: %s", path, type(exc).__name__, exc)
        return
    log.info("ingested %s → chroma id=%s (%d chars)", path.name, ids[0] if ids else "?", len(text))
    # Phase 19.2 — event-driven Sparky nudge.
    try:
        from core import event_bus
        event_bus.publish_remote(
            "file_ingested",
            path=str(path),
            ext=path.suffix.lower(),
            chars=len(text),
        )
    except Exception:
        pass


def _scan_once(seen: set[str]) -> None:
    for root in WATCH_DIRS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in EXTENSIONS:
                continue
            key = str(p.resolve())
            if key in seen:
                continue
            if not _is_settled(p):
                continue
            _ingest(p)
            _mark_seen(p)
            seen.add(key)


def main() -> None:
    stop = {"flag": False}

    def handle(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    log.info("nexus-file-watcher starting; watching %s", [str(d) for d in WATCH_DIRS])
    seen = _load_seen()
    # Baseline: mark everything already present as seen, so we only act on new arrivals.
    if not seen:
        for root in WATCH_DIRS:
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if p.is_file() and p.suffix.lower() in EXTENSIONS:
                    _mark_seen(p)
                    seen.add(str(p.resolve()))
        log.info("baseline recorded %d existing files", len(seen))

    while not stop["flag"]:
        try:
            _scan_once(seen)
        except Exception as exc:
            log.exception("scan failed: %s: %s", type(exc).__name__, exc)
        for _ in range(10):
            if stop["flag"]:
                break
            time.sleep(1)
    log.info("nexus-file-watcher stopping")


if __name__ == "__main__":
    main()

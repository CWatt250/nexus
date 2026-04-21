#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Nexus clipboard watcher.

Polls the X clipboard every 5 seconds via xclip. When new text longer than
20 chars appears, appends it to ~/AI_Agent/memory/clipboard-log.md with a
timestamp and stores it in Chroma RAG tagged as clipboard. Runs as the
nexus-clipboard-watcher systemd service."""
from __future__ import annotations

import hashlib
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.rag_tool import add_documents  # noqa: E402

LOG_PATH = Path.home() / "AI_Agent" / "memory" / "clipboard-log.md"
POLL_SECONDS = 5
MIN_CHARS = 20
MAX_CHARS = 50_000  # cap absurd clipboard dumps
SELECTIONS = ("clipboard", "primary")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s clipboard-watcher %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("nexus.clipboard_watcher")


def _read_selection(sel: str) -> str:
    try:
        res = subprocess.run(
            ["xclip", "-selection", sel, "-o"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if res.returncode != 0:
        return ""
    return res.stdout or ""


def _append_log(text: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.exists():
        LOG_PATH.write_text("# Nexus clipboard log\n\n", encoding="utf-8")
    ts = datetime.now().isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"## {ts}\n\n```\n{text}\n```\n\n")


def _ingest(text: str) -> None:
    meta = {
        "source": "clipboard",
        "tag": "clipboard",
        "ts": int(time.time()),
    }
    try:
        add_documents([text], metadatas=[meta])
    except Exception as exc:
        log.warning("RAG store failed: %s: %s", type(exc).__name__, exc)


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="replace")).hexdigest()


def _check_xclip() -> bool:
    from shutil import which
    return which("xclip") is not None


def main() -> None:
    if not _check_xclip():
        log.error("xclip not found on PATH — install with: sudo apt install -y xclip")
        sys.exit(1)
    if not os.environ.get("DISPLAY"):
        log.warning("DISPLAY not set; xclip likely cannot reach the X session")

    stop = {"flag": False}

    def handle(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    log.info("nexus-clipboard-watcher starting; poll=%ds, min_chars=%d", POLL_SECONDS, MIN_CHARS)
    last_hash = ""
    while not stop["flag"]:
        try:
            text = ""
            for sel in SELECTIONS:
                t = _read_selection(sel)
                if t and len(t) >= MIN_CHARS:
                    text = t
                    break
            if text:
                if len(text) > MAX_CHARS:
                    text = text[:MAX_CHARS]
                h = _hash(text)
                if h != last_hash:
                    last_hash = h
                    _append_log(text)
                    _ingest(text)
                    log.info("captured %d chars (hash=%s)", len(text), h[:8])
        except Exception as exc:
            log.exception("poll failed: %s: %s", type(exc).__name__, exc)
        for _ in range(POLL_SECONDS):
            if stop["flag"]:
                break
            time.sleep(1)
    log.info("nexus-clipboard-watcher stopping")


if __name__ == "__main__":
    main()

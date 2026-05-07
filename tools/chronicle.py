#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Nexus Chronicle — passive screen memory.

Every 5 minutes:
  1. Capture the active screen with `scrot` into a temp PNG.
  2. OCR the image with tesseract (pytesseract wrapper).
  3. If the OCR text is shorter than MIN_TEXT_CHARS (50), skip.
  4. Ask qwen3:4b for a 2–3 sentence summary of what the user is doing.
  5. Append the summary (with timestamp) to memory/chronicle/YYYY-MM-DD.md.
  6. Index the summary in Chroma RAG tagged `chronicle` for semantic recall.

Skips silently when the screen appears locked (gnome-screensaver /
systemd-loginctl), when scrot is missing, or when no DISPLAY is set.

Runs as the nexus-chronicle systemd service."""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.rag_tool import add_documents  # noqa: E402

INTERVAL_SECONDS = 5 * 60
MIN_TEXT_CHARS = 50
CHRONICLE_DIR = Path.home() / "AI_Agent" / "memory" / "chronicle"
OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:4b"

SYSTEM_PROMPT = (
    "You summarize a person's current screen content in 2-3 sentences. "
    "Focus on what they are working on, not on UI chrome or irrelevant text. "
    "Be specific about apps, files, or topics you can identify. "
    "No preamble. No lists. Just the summary."
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s nexus-chronicle %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("nexus.chronicle")


def _screen_locked() -> bool:
    # Try GNOME (most common on Ubuntu 24.04)
    try:
        res = subprocess.run(
            ["gnome-screensaver-command", "--query"],
            capture_output=True, text=True, timeout=3,
        )
        if res.returncode == 0 and "is active" in (res.stdout or res.stderr):
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Fall back to loginctl
    try:
        res = subprocess.run(
            ["loginctl", "show-session", os.environ.get("XDG_SESSION_ID", ""), "-p", "LockedHint"],
            capture_output=True, text=True, timeout=3,
        )
        if "LockedHint=yes" in (res.stdout or ""):
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


_HEADLESS_DISPLAY = ":99"


def _resolve_display() -> str | None:
    """Return a usable DISPLAY value.

    Prefers whatever is already set in the environment. Falls back to the
    nexus-xvfb.service virtual display at :99 when no real display is set."""
    current = os.environ.get("DISPLAY")
    if current:
        return current
    try:
        proc = subprocess.run(
            ["xdpyinfo", "-display", _HEADLESS_DISPLAY],
            capture_output=True, timeout=2,
        )
        if proc.returncode == 0:
            os.environ["DISPLAY"] = _HEADLESS_DISPLAY
            log.debug("chronicle: using headless display %s", _HEADLESS_DISPLAY)
            return _HEADLESS_DISPLAY
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _take_screenshot(dest: Path) -> bool:
    scrot = shutil.which("scrot")
    if not scrot:
        log.warning("scrot not on PATH — install with: sudo apt install -y scrot")
        return False
    display = _resolve_display()
    if not display:
        log.debug("no DISPLAY available for scrot")
        return False
    env = {**os.environ, "DISPLAY": display}
    try:
        res = subprocess.run(
            [scrot, "-o", str(dest)],
            capture_output=True, text=True, timeout=10, env=env,
        )
    except subprocess.TimeoutExpired:
        return False
    if res.returncode != 0:
        log.warning("scrot failed: %s", (res.stderr or "").strip())
        return False
    return dest.exists()


def _ocr(image_path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        log.warning("pytesseract/Pillow missing: %s", exc)
        return ""
    try:
        with Image.open(image_path) as img:
            return pytesseract.image_to_string(img) or ""
    except Exception as exc:
        log.warning("OCR failed: %s", exc)
        return ""


def _summarize(ocr_text: str) -> str:
    try:
        import ollama
    except ImportError:
        log.warning("ollama python client missing")
        return ""
    user = (
        "Here is the OCR text captured from my screen right now. "
        "Summarize what I'm working on in 2-3 sentences.\n\n"
        + ocr_text[:6000]
    )
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            stream=False,
            think=False,
            options={"temperature": 0.2, "num_predict": 200, "num_ctx": 8192},
        )
    except Exception as exc:
        log.warning("qwen3 summary failed: %s", exc)
        return ""
    content = resp["message"]["content"] if isinstance(resp, dict) else getattr(resp.message, "content", "")
    # Strip any stray <think> leakage just in case.
    import re
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL | re.IGNORECASE).strip()
    return content


def _append_day_md(summary: str) -> None:
    CHRONICLE_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    ts = datetime.now().isoformat(timespec="seconds")
    path = CHRONICLE_DIR / f"{day}.md"
    if not path.exists():
        path.write_text(f"# Chronicle — {day}\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"## {ts}\n\n{summary}\n\n")


def _rag_store(summary: str) -> None:
    meta = {
        "tag": "chronicle",
        "ts": int(time.time()),
        "when": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        add_documents([summary], metadatas=[meta])
    except Exception as exc:
        log.warning("RAG store failed: %s", exc)


def _tick() -> None:
    if not _resolve_display():
        log.debug("no display available (real or :99); skipping")
        return
    if _screen_locked():
        log.debug("screen is locked; skipping")
        return
    with tempfile.TemporaryDirectory(prefix="chronicle-") as td:
        shot = Path(td) / "shot.png"
        if not _take_screenshot(shot):
            return
        ocr = _ocr(shot).strip()
        if len(ocr) < MIN_TEXT_CHARS:
            log.debug("OCR text too short (%d chars); skipping", len(ocr))
            return
        summary = _summarize(ocr).strip()
        if not summary:
            return
        _append_day_md(summary)
        _rag_store(summary)
        log.info("chronicle: %s", summary[:120])


def main() -> None:
    stop = {"flag": False}

    def handle(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    log.info("nexus-chronicle starting; interval=%ds", INTERVAL_SECONDS)
    while not stop["flag"]:
        try:
            _tick()
        except Exception as exc:
            log.exception("tick failed: %s", exc)
        for _ in range(INTERVAL_SECONDS):
            if stop["flag"]:
                break
            time.sleep(1)
    log.info("nexus-chronicle stopping")


if __name__ == "__main__":
    main()

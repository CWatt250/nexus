#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Sparky brain — proactive hint daemon.

Every 5 minutes (and every time the Chronicle writes a new summary), this
daemon:

  1. Gathers recent context — latest screen summaries from memory/chronicle/,
     last 10 clipboard entries, last 5 git commits.
  2. Asks qwen3:4b via Ollama for a short helpful hint.
  3. Tells the Sparky state bridge that Sparky is talking + carries the
     message; triggers the mouth-animation lifecycle via
     /speaking/start → Kokoro TTS playback → /speaking/stop.

Also fires a one-shot greeting ~25 seconds after startup.

Cooldown: no hint fires within MIN_COOLDOWN_SECONDS of the last one, so
the chronicle trigger and the 5-minute timer don't step on each other."""
from __future__ import annotations

import json
import logging
import random
import re
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx
import ollama

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.tts_tool import resolved_voice  # noqa: E402
from tools.tts_tool import speak as tts_speak  # noqa: E402

BRIDGE_URL = "http://localhost:11437"
CHRONICLE_DIR = ROOT / "memory" / "chronicle"
CLIPBOARD_LOG = ROOT / "memory" / "clipboard-log.md"
GIT_LOG = ROOT / "memory" / "git-activity.log"
OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:4b"

HINT_TIMER_SECONDS = 300     # 5 minutes
GREETING_DELAY = 25          # seconds after startup
MIN_COOLDOWN_SECONDS = 60    # floor between any two hints
CHRONICLE_DEBOUNCE = 30      # fs-watcher suppresses repeated fires inside this window

PROMPT = (
    "You are Sparky, a witty and helpful AI companion. Based on what Colton "
    "is working on, give him ONE short helpful hint, tip, recommendation, or "
    "observation. Max 2 sentences. Be direct, useful, and occasionally funny. "
    "Never say 'I notice' or 'I see'. Just say the thing."
)

GREETINGS = [
    "Sparky online. Let's build something ridiculous today, Colton.",
    "Good to see you back. WattBott is humming — what's the mission?",
    "Hey Colton. I've been quietly watching over your shoulder. Ready when you are.",
    "Sparky here. Full tool belt strapped, horns polished. Point me at something.",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s sparky-brain %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("sparky.brain")

_last_hint_ts = 0.0
_speak_lock = threading.Lock()
_stop_event = threading.Event()


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------

def _latest_chronicle(entries: int = 3) -> str:
    """Return the last `entries` chronicle summaries from the most recently
    modified YYYY-MM-DD.md file."""
    if not CHRONICLE_DIR.exists():
        return ""
    files = sorted(CHRONICLE_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return ""
    try:
        text = files[0].read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    # Entries are "## <ts>\n\n<summary>\n\n". Split on "\n## " to keep each.
    parts = re.split(r"\n## ", text)
    parts = [p for p in parts if p.strip() and not p.lstrip().startswith("# ")]
    relevant = parts[-entries:]
    return "\n---\n".join(p.strip() for p in relevant)


def _last_clipboard_entries(n: int = 10) -> str:
    if not CLIPBOARD_LOG.exists():
        return ""
    try:
        text = CLIPBOARD_LOG.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    parts = re.split(r"\n## ", text)
    parts = [p for p in parts if p.strip() and not p.lstrip().startswith("# ")]
    last = parts[-n:]
    return "\n---\n".join(p.strip() for p in last)


def _last_git_entries(n: int = 5) -> list[dict]:
    if not GIT_LOG.exists():
        return []
    try:
        lines = GIT_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
        if len(out) >= n:
            break
    return list(reversed(out))


def _build_payload() -> str:
    chronicle = _latest_chronicle()
    clipboard = _last_clipboard_entries(10)
    git = _last_git_entries(5)
    git_text = "\n".join(
        f"- {g.get('when', '')}  {g.get('repo', '')}@{(g.get('sha') or '')[:8]}  {g.get('subject', '')}"
        for g in git
    )
    sections: list[str] = []
    if chronicle:
        sections.append(f"## Recent screen summaries\n{chronicle[:3000]}")
    if clipboard:
        sections.append(f"## Recent clipboard\n{clipboard[:3000]}")
    if git_text:
        sections.append(f"## Recent commits\n{git_text[:1500]}")
    if not sections:
        sections.append("(no recent activity captured yet — say hello and offer a nudge)")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Model + bridge + speak
# ---------------------------------------------------------------------------

def _ask_qwen(user_payload: str) -> str:
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": user_payload},
            ],
            stream=False,
            think=False,
            options={"temperature": 0.7, "num_predict": 140, "num_ctx": 8192},
        )
    except Exception as exc:
        log.warning("ollama call failed: %s", exc)
        return ""
    content = resp["message"]["content"] if isinstance(resp, dict) else getattr(resp.message, "content", "")
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL | re.IGNORECASE).strip()
    # Cap to 2 sentences so the assistant doesn't ramble past the spec.
    sentences = re.split(r"(?<=[.!?])\s+", content)
    return " ".join(sentences[:2]).strip()


def _bridge_post(path: str, body: dict | None = None) -> None:
    try:
        with httpx.Client(timeout=5) as client:
            if body is None:
                client.post(f"{BRIDGE_URL}{path}")
            else:
                client.post(f"{BRIDGE_URL}{path}", json=body)
    except Exception as exc:
        log.warning("bridge %s failed: %s", path, exc)


def _is_muted() -> bool:
    """Hard mute check — called immediately before every audio playback.
    Uses `requests` synchronously so it can't be lost to an async edge
    case; returns False only if the bridge is reachable AND muted=False."""
    try:
        import requests
        r = requests.get(f"{BRIDGE_URL}/muted", timeout=1)
        return bool(r.json().get("muted", False))
    except Exception:
        return False


def _speak_hint(message: str) -> None:
    """Orchestrate the full speak flow for one hint."""
    global _last_hint_ts
    if not message or not message.strip():
        return
    if not _speak_lock.acquire(blocking=False):
        log.info("another speak in progress; skipping")
        return
    try:
        log.info("speaking: %s", message[:160])
        _last_hint_ts = time.time()
        _bridge_post("/state", {"state": "talking", "message": message})
        # Fire the speech bubble at the same moment the TTS lifecycle begins
        # so the on-screen text and the audio track together.
        _bridge_post("/message", {"text": message})
        _bridge_post("/speaking/start")
        try:
            # HARD MUTE GATE — checked immediately before any audio call.
            if _is_muted():
                log.info("muted — skipping TTS playback (bubble still shown)")
            else:
                status = tts_speak(message)
                if isinstance(status, str) and status.startswith("ERROR:"):
                    log.warning("tts: %s", status)
        except Exception as exc:
            log.exception("tts crashed: %s", exc)
        finally:
            _bridge_post("/speaking/stop")
    finally:
        _speak_lock.release()


def _maybe_trigger(reason: str, *, force: bool = False) -> None:
    global _last_hint_ts
    now = time.time()
    if not force and now - _last_hint_ts < MIN_COOLDOWN_SECONDS:
        log.info("cooldown (%.0fs since last); skipping %s", now - _last_hint_ts, reason)
        return
    log.info("trigger: %s", reason)
    payload = _build_payload()
    message = _ask_qwen(payload)
    if not message:
        log.info("qwen returned empty; skipping")
        return
    _speak_hint(message)


def _say_greeting() -> None:
    _speak_hint(random.choice(GREETINGS))


# ---------------------------------------------------------------------------
# Chronicle watcher
# ---------------------------------------------------------------------------

def _install_chronicle_watcher() -> object | None:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except Exception as exc:
        log.warning("watchdog unavailable: %s — chronicle real-time trigger disabled", exc)
        return None

    CHRONICLE_DIR.mkdir(parents=True, exist_ok=True)

    class Handler(FileSystemEventHandler):
        def __init__(self) -> None:
            self._last_fire = 0.0

        def _maybe_fire(self, path: str) -> None:
            if not path.endswith(".md"):
                return
            now = time.time()
            if now - self._last_fire < CHRONICLE_DEBOUNCE:
                return
            self._last_fire = now

            def _delayed():
                # Let the file settle (chronicle.py appends, takes a moment).
                time.sleep(2)
                if _stop_event.is_set():
                    return
                _maybe_trigger("chronicle-update")

            threading.Thread(target=_delayed, name="chronicle-fire", daemon=True).start()

        def on_modified(self, event):
            if not event.is_directory:
                self._maybe_fire(str(event.src_path))

        def on_created(self, event):
            if not event.is_directory:
                self._maybe_fire(str(event.src_path))

    observer = Observer()
    observer.schedule(Handler(), str(CHRONICLE_DIR), recursive=False)
    observer.start()
    log.info("watching %s for new chronicle summaries", CHRONICLE_DIR)
    return observer


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    def _handle(signum, frame):
        _stop_event.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    log.info("sparky-brain starting — warming TTS…")
    try:
        log.info("resolved TTS voice: %s", resolved_voice())
    except Exception as exc:
        log.warning("voice probe failed (will retry per hint): %s", exc)

    observer = _install_chronicle_watcher()

    # Greeting after GREETING_DELAY seconds.
    def _greet():
        if _stop_event.wait(GREETING_DELAY):
            return
        _say_greeting()

    threading.Thread(target=_greet, name="greeting", daemon=True).start()

    # 5-minute tick. Sleep 5s at a time so SIGTERM shuts us down quickly.
    last_tick = time.time()
    while not _stop_event.is_set():
        now = time.time()
        if now - last_tick >= HINT_TIMER_SECONDS:
            last_tick = now
            _maybe_trigger("timer")
        _stop_event.wait(5)

    if observer is not None:
        try:
            observer.stop()
            observer.join(timeout=3)
        except Exception:
            pass
    log.info("sparky-brain stopping")


if __name__ == "__main__":
    main()

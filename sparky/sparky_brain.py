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

import hashlib
import json
import logging
import random
import re
import signal
import sys
import threading
import time
from datetime import datetime, timezone
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
# Was qwen3.6 (retired, 23GB). Use the resident brain via models.json so
# Sparky hints cost 0 extra VRAM; falls back to qwen3:4b if config is missing.
def _live_model(key: str = "brain", default: str = "qwen3:4b") -> str:
    try:
        from pathlib import Path as _P
        return json.loads((_P.home() / "AI_Agent" / "models.json").read_text()).get(key) or default
    except Exception:
        return default


MODEL = _live_model("brain")

HINT_TIMER_SECONDS = 300     # 5 minutes
GREETING_DELAY = 25          # seconds after startup
MIN_COOLDOWN_SECONDS = 60    # floor between any two hints
CHRONICLE_DEBOUNCE = 30      # fs-watcher suppresses repeated fires inside this window

# Anti-repeat knobs
HINT_LOG = Path.home() / "AI_Agent" / "memory" / "sparky-hints.jsonl"
HINT_MEMORY = 10             # how many recent hints to remember / avoid
REPEAT_SIMILARITY = 0.55     # Jaccard token overlap that counts as a repeat
STAGNANT_FINGERPRINT_TTL = 1800  # 30 min — skip if context hasn't changed

PROMPT = (
    "You are Sparky, an adaptive hinting engine for Colton. Your job is to "
    "move him FORWARD, not describe what he's doing.\n\n"
    "Rules:\n"
    "1. ADD VALUE ONLY. Every reply is ONE of: a concrete tip, a pivot, a "
    "warning about a likely pitfall, or a focused diagnostic question. Never narrate.\n"
    "2. NEVER MIRROR. Do not restate what the screen/clipboard shows. Do not say "
    "'you're working on…', 'looks like you're…', 'we are given', or paraphrase his activity.\n"
    "3. BE SPECIFIC. Replace 'try debugging' with concrete steps. Name files, "
    "flags, commands, or library functions when you can.\n"
    "4. DO NOT REPEAT. You'll be shown your recent hints. Pick a different angle — "
    "rotate through optimization, edge cases, workflow shortcuts, alternative "
    "architectures, tooling, security pitfalls.\n"
    "5. ONE to TWO sentences. Fit a tweet.\n\n"
    "Output ONLY the hint. No 'Okay', no 'Let me', no 'We are given', no 'So', no "
    "preamble, no analysis, no sign-off. Start immediately with the suggestion.\n\n"
    "GOOD example:\n"
    "  input: Next.js login form hitting 403 on /api/auth/login; clipboard shows "
    "'Authorization: Bearer ${token}'.\n"
    "  output: Check that ${token} is actually being interpolated — if it's in a "
    "single-quoted string the server is seeing the literal placeholder and 403ing; "
    "swap to backticks or build the header in JS before passing it.\n\n"
    "BAD example:\n"
    "  output: Okay, let me tackle this step by step. The 403 error typically "
    "means the server rejected the request. Since the clipboard shows an "
    "authorization header, the token is probably…"
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
_last_fingerprint: str = ""
_last_fingerprint_at: float = 0.0
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

def _ask_qwen(user_payload: str, recent_hints: list[dict] | None = None,
              *, extra_system: str = "", temperature: float = 0.75) -> str:
    system = PROMPT
    if extra_system:
        system = system + "\n\n" + extra_system
    if recent_hints:
        bullets = "\n".join(f"- {h.get('text','').strip()}" for h in recent_hints if h.get("text"))
        if bullets:
            system += (
                "\n\nRECENT HINTS (you already said these — do NOT repeat, "
                "paraphrase, or drift back into the same topic; pick a "
                "different angle):\n" + bullets
            )
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_payload},
            ],
            stream=False,
            think=False,
            options={"temperature": temperature, "num_predict": 140, "num_ctx": 8192},
        )
    except Exception as exc:
        log.warning("ollama call failed: %s", exc)
        return ""
    content = resp["message"]["content"] if isinstance(resp, dict) else getattr(resp.message, "content", "")
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL | re.IGNORECASE).strip()
    content = _strip_preamble(content)
    sentences = re.split(r"(?<=[.!?])\s+", content)
    return " ".join(sentences[:2]).strip()


# Preamble / meta phrases we strip if the model leaks them despite the
# "output only the hint" instruction.
_PREAMBLE_PATTERNS = [
    r"^\s*(?:okay|ok|alright|well|so|hmm+|right)[,:\s]+",
    r"^\s*let(?:'s| me)[^.]*?[.:\n]\s*",
    r"^\s*here(?:'s| is) (?:a |the |my )?(?:hint|suggestion|tip|observation)[^.]*?[.:\n]\s*",
    r"^\s*i (?:notice|see|think|observe|will|would)[^.]*?[.:\n]\s*",
    r"^\s*(?:we|you) (?:are|'re) (?:given|looking at|working on)[^.]*?[.:\n]\s*",
    r"^\s*based on[^.]*?[.:\n]\s*",
    r"^\s*(?:the |a )?(?:user|colton) is[^.]*?[.:\n]\s*",
    r"^\s*step[- ]?by[- ]?step[^.]*?[.:\n]\s*",
    r"^\s*hint[:\s]+",
    r"^\s*output[:\s]+",
]
_BULLET_LEAD = re.compile(r"^\s*[-*•]\s+")


def _strip_preamble(text: str) -> str:
    """Trim meta-preambles the model sometimes emits despite the prompt."""
    if not text:
        return text
    # Drop empty lines + any leading numbered / bulleted list markers.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    # If the model wrote "hint:" / "output:" on its own line, drop the line.
    while lines and re.match(r"^\s*(hint|output|suggestion|tip)[:\s]*$",
                             lines[0], re.I):
        lines.pop(0)
    joined = "\n".join(lines)
    # Strip single-line leading meta phrases.
    for _ in range(3):  # up to three layers of preamble
        matched = False
        for pat in _PREAMBLE_PATTERNS:
            new = re.sub(pat, "", joined, count=1, flags=re.IGNORECASE)
            if new != joined:
                joined = new.lstrip()
                matched = True
                break
        if not matched:
            break
    # Drop a leading bullet marker if one remains.
    joined = _BULLET_LEAD.sub("", joined, count=1)
    return joined.strip()


# ---------------------------------------------------------------------------
# Anti-repeat memory
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "and", "but", "for", "you", "your", "with", "this", "that", "from",
    "have", "has", "into", "onto", "then", "just", "what", "when", "where",
    "about", "some", "will", "can", "could", "should", "would", "they", "them",
    "their", "these", "those", "there", "its", "his", "her", "one", "two",
    "using", "also", "try", "now",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]{3,}", (text or "").lower())
            if t not in _STOPWORDS}


def _similarity(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _load_recent_hints(n: int = HINT_MEMORY) -> list[dict]:
    if not HINT_LOG.exists():
        return []
    out: list[dict] = []
    try:
        lines = HINT_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for ln in lines[-(n * 3):]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out[-n:]


def _save_hint(text: str, fingerprint: str, source: str) -> None:
    HINT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "text": text,
        "fingerprint": fingerprint,
        "source": source,
    }
    try:
        with HINT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("could not append to hint log: %s", exc)


def _first_repeat(text: str, recent: list[dict]) -> str | None:
    for r in recent:
        prev = r.get("text", "")
        if not prev:
            continue
        if _similarity(text, prev) >= REPEAT_SIMILARITY:
            return prev
    return None


def _context_fingerprint(payload: str) -> str:
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:16]


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
    global _last_hint_ts, _last_fingerprint, _last_fingerprint_at
    now = time.time()
    if not force and now - _last_hint_ts < MIN_COOLDOWN_SECONDS:
        log.info("cooldown (%.0fs since last); skipping %s", now - _last_hint_ts, reason)
        return
    log.info("trigger: %s", reason)

    payload = _build_payload()
    fingerprint = _context_fingerprint(payload)

    # 1. Stagnation guard: if the context hasn't changed AND we fired a hint
    # for it recently, skip rather than echo ourselves.
    if (not force
        and fingerprint == _last_fingerprint
        and now - _last_fingerprint_at < STAGNANT_FINGERPRINT_TTL):
        log.info("context unchanged (fingerprint=%s); skipping %s",
                 fingerprint, reason)
        return

    # 2. Ask the LLM with its recent hints in scope.
    recent = _load_recent_hints()
    message = _ask_qwen(payload, recent_hints=recent)
    if not message:
        log.info("qwen returned empty; skipping")
        return

    # 3. Anti-repeat check; retry once with explicit feedback if the first
    # response is too close to something we've already said.
    dup = _first_repeat(message, recent)
    if dup:
        log.info("response too similar to prior hint; retrying once")
        feedback = (
            "Your last draft was too close to a hint you already gave "
            f"(\"{dup[:120]}\"). Pick a COMPLETELY different angle — a "
            "different file, tool, pitfall, or technique."
        )
        message = _ask_qwen(payload, recent_hints=recent,
                            extra_system=feedback, temperature=0.9) or message
        dup2 = _first_repeat(message, recent)
        if dup2:
            log.info("still a repeat after retry; skipping %s", reason)
            return

    # 4. Ship it.
    _save_hint(message, fingerprint, source=reason)
    _last_fingerprint = fingerprint
    _last_fingerprint_at = now
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

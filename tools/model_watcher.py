"""Auto model watcher (Phase 18.5).

Compares the locally-pulled Ollama models to the official `ollama.com`
library catalog and emits a short list of candidates Colton might want to
try. **Does not auto-pull** — every new model has to be approved manually.

Designed for a weekly trigger (Mondays 08:00 — same window the perf
guardian / lessons aggregator share, which is why we stagger them by a
few minutes; see `nexus-lessons.timer` and the comment in
`safety/perf_guardian.py`).

Output goes to `memory/model-watcher.jsonl` and (best-effort) Telegram.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
from langchain_core.tools import tool

ROOT = Path.home() / "AI_Agent"
LOG_PATH = ROOT / "memory" / "model-watcher.jsonl"
# Snapshot of every library model seen on the previous run. The diff against
# THIS is what's actually new — not the diff against locally-pulled models
# (that set is near-static and was why the watcher repeated the same list).
SEEN_PATH = ROOT / "memory" / "model-watcher-seen.json"
LIBRARY_URL = "https://ollama.com/library"
TIMEOUT = 15

# Model families worth flagging. Prefix/segment matched (not substring) so
# "everythinglm" no longer falsely matches "glm". qwen3.6 dropped (retired).
INTERESTING = ("qwen", "glm", "llama", "deepseek", "mistral", "gemma", "phi", "gpt-oss")

log = logging.getLogger("nexus.model_watcher")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_models() -> list[str]:
    try:
        with httpx.Client(timeout=4) as client:
            r = client.get("http://localhost:11434/api/tags")
        if r.status_code != 200:
            return []
        return [(m.get("name") or "") for m in r.json().get("models", [])]
    except Exception as exc:
        log.warning("ollama tags fetch failed: %s", exc)
        return []


def _library_listing() -> list[str]:
    """Scrape the public library page for visible model names. The HTML
    has model identifiers as `data-controller="model"` blocks with an
    h2 inside; we use a tight regex over `<h2 ...>name</h2>` so we don't
    need a full HTML parser. Best-effort — falls back to an empty list."""
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "nexus-model-watcher/1.0"}) as client:
            r = client.get(LIBRARY_URL)
        if r.status_code != 200:
            return []
        html = r.text
    except Exception as exc:
        log.warning("ollama library fetch failed: %s", exc)
        return []
    names = set()
    for match in re.finditer(r'href="/library/([\w.\-]+)"', html):
        names.add(match.group(1).lower())
    return sorted(names)


def _matches_family(name: str) -> bool:
    """Prefix/segment match against INTERESTING (not substring)."""
    n = name.lower()
    segs = re.split(r"[-:._]", n)
    return any(n.startswith(f) or f in segs for f in INTERESTING)


def _load_seen() -> set[str]:
    """Set of library model names seen on the previous run (empty on first run)."""
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
        return set(data.get("library", []))
    except (OSError, json.JSONDecodeError):
        return set()


def _save_seen(library: list[str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        SEEN_PATH.write_text(
            json.dumps({"ts": _now(), "library": sorted(library)}, ensure_ascii=False),
            encoding="utf-8")
    except OSError as exc:
        log.warning("model-watcher seen-state write failed: %s", exc)


def _append(record: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("model-watcher log write failed: %s", exc)


@tool
def model_watcher_run() -> str:
    """Report Ollama library models NEWLY released since the last run (diff
    against the saved snapshot, NOT against locally-pulled models). First run
    seeds a baseline silently. Records to memory/model-watcher.jsonl; best-effort
    Telegram on genuinely-new interesting models."""
    library = _library_listing()
    if not library:
        return "library fetch failed — nothing to report (will retry next run)"

    seen = _load_seen()
    if not seen:
        # First run — establish the baseline; don't dump the whole catalog.
        _save_seen(library)
        _append({"ts": _now(), "event": "baseline", "library_size": len(library)})
        return (f"baseline established — tracking {len(library)} library models. "
                f"I'll report only NEW releases from here on.")

    new_all = sorted(set(library) - seen)
    new_interesting = [n for n in new_all if _matches_family(n)]
    _save_seen(library)  # advance the snapshot
    _append({"ts": _now(), "library_size": len(library),
             "new_count": len(new_all), "new_interesting": new_interesting})

    if not new_interesting:
        extra = f" ({len(new_all)} new overall, none in tracked families)" if new_all else ""
        return f"no new interesting models since last check{extra}"

    msg = "📦 NEW Ollama models since last check:\n" + "\n".join(
        f"- {n}" for n in new_interesting[:15]
    ) + ("\n…" if len(new_interesting) > 15 else "")
    try:
        import asyncio
        from tools.telegram_tool import proactive_send
        asyncio.run(proactive_send(msg))
    except Exception:
        pass
    return msg


MODEL_WATCHER_TOOLS = [model_watcher_run]

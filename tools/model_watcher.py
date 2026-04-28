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
LIBRARY_URL = "https://ollama.com/library"
TIMEOUT = 15

# Model families we actively care about.
INTERESTING = ("qwen3", "qwen2.5", "qwen3.6", "glm", "llama3", "deepseek", "mistral")

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


def _candidates(local: list[str], library: list[str]) -> list[str]:
    local_bases = {n.split(":")[0].lower() for n in local}
    out: list[str] = []
    for name in library:
        if name in local_bases:
            continue
        if any(family in name for family in INTERESTING):
            out.append(name)
    return out


def _append(record: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("model-watcher log write failed: %s", exc)


@tool
def model_watcher_run() -> str:
    """Compare local Ollama models to the public library and report new
    candidates. Records the diff to memory/model-watcher.jsonl. Best-effort
    Telegram notification with the candidate list."""
    local = _local_models()
    library = _library_listing()
    candidates = _candidates(local, library)
    record = {
        "ts": _now(),
        "local": local,
        "library_size": len(library),
        "candidates": candidates,
    }
    _append(record)
    if not candidates:
        return "no new model candidates"
    msg = "📦 New Ollama models (interesting families):\n" + "\n".join(
        f"- {n}" for n in candidates[:15]
    ) + ("\n…" if len(candidates) > 15 else "")
    try:
        import asyncio
        from tools.telegram_tool import proactive_send
        asyncio.run(proactive_send(msg))
    except Exception:
        pass
    return msg


MODEL_WATCHER_TOOLS = [model_watcher_run]

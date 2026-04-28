"""Weekly LESSONS.md aggregator (Phase 14.4).

Reads `memory/retros/retro_*.md` files modified in the last 7 days, pulls
the bullet-list under `## Lessons`, dedupes, asks qwen3:4b to cluster the
top items, and writes `~/AI_Agent/LESSONS.md` with timestamp + last-week
summary appended at the top of an evergreen file.

Run from the systemd timer `nexus-lessons.timer` (Mondays 8am). Also
runnable manually: `python3 -m memory.lessons_aggregator`.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ollama

ROOT = Path.home() / "AI_Agent"
RETRO_DIR = ROOT / "memory" / "retros"
LESSONS_FILE = ROOT / "LESSONS.md"
WINDOW_DAYS = 7
OLLAMA_URL = "http://localhost:11434"
DIGEST_MODEL = "qwen3:4b"

# Bullets that look like file path / setup / boilerplate noise we don't want
# to keep around as "lessons".
_NOISE = re.compile(r"^(retro:|##|user:|reply:|tools used|wall:|key points)", re.IGNORECASE)


def _last_week_retros(now: datetime) -> list[Path]:
    if not RETRO_DIR.exists():
        return []
    cutoff = now - timedelta(days=WINDOW_DAYS)
    out = []
    for p in RETRO_DIR.glob("retro_*.md"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime >= cutoff:
            out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def _extract_lessons(md: str) -> list[str]:
    """Pull bullet lines from the `## Lessons` section."""
    block = md.split("## Lessons", 1)
    if len(block) < 2:
        return []
    body = block[1]
    bullets = []
    for line in body.splitlines():
        line = line.rstrip()
        stripped = line.lstrip("-* ").strip()
        if not stripped or _NOISE.match(stripped):
            continue
        if line.startswith(("- ", "* ")) and len(stripped) > 4:
            bullets.append(stripped)
    return bullets


def _digest(bullets: list[str]) -> str:
    """Ask qwen3:4b to cluster and rank the bullets. Falls back to the raw
    deduped list when the model is unavailable so the file still updates."""
    if not bullets:
        return "_(no actionable lessons captured this week)_"
    if len(bullets) <= 5:
        return "\n".join(f"- {b}" for b in bullets)
    prompt = (
        "Cluster these per-turn lessons into 5 distinct, concrete, actionable bullets. "
        "Each bullet must be a single sentence in imperative mood (\"Do X\", \"Avoid Y\", "
        "\"Prefer Z\"). Drop duplicates and vague observations. Plain markdown, no preamble.\n\n"
        + "\n".join(f"- {b}" for b in bullets)
        + "\n\nClustered lessons:"
    )
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=DIGEST_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            think=False,
            keep_alive=-1,
            options={"temperature": 0.1, "num_predict": 400, "num_ctx": 8192},
        )
    except Exception:
        return "\n".join(f"- {b}" for b in bullets[:10])
    content = ""
    if isinstance(resp, dict):
        content = ((resp.get("message") or {}).get("content") or "").strip()
    else:
        msg = getattr(resp, "message", None)
        content = (getattr(msg, "content", "") or "").strip()
    # Strip <think> blocks and keep only bullet lines so the model's
    # narrative preamble (which qwen3:4b sometimes leaks) doesn't pollute
    # the file.
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    bullets_only = [
        line for line in content.splitlines()
        if line.strip().startswith(("- ", "* ", "1.", "2.", "3.", "4.", "5."))
    ]
    if bullets_only:
        return "\n".join(b.lstrip("12345. ").rstrip() if b.lstrip()[:2] in ("1.","2.","3.","4.","5.") else b
                         for b in bullets_only)
    return content or "\n".join(f"- {b}" for b in bullets[:10])


def aggregate() -> Path:
    now = datetime.now(timezone.utc)
    retros = _last_week_retros(now)
    bullets: list[str] = []
    for p in retros:
        try:
            md = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        bullets.extend(_extract_lessons(md))
    # Stable de-dupe preserving order.
    seen = set()
    deduped = []
    for b in bullets:
        key = re.sub(r"\s+", " ", b.lower())[:200]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(b)

    body = _digest(deduped)
    iso = now.date().isoformat()
    header = f"## Week of {iso} ({len(retros)} retros, {len(deduped)} unique lessons)\n\n"
    new_section = header + body.strip() + "\n\n---\n\n"

    existing = LESSONS_FILE.read_text(encoding="utf-8") if LESSONS_FILE.exists() else "# Nexus Lessons (weekly digest)\n\n"
    # Drop any prior section for the same week to keep the file idempotent
    # if the timer fires twice in one Monday.
    existing = re.sub(
        rf"## Week of {re.escape(iso)}.*?(?=## Week of |\Z)",
        "",
        existing,
        flags=re.DOTALL,
    )
    if not existing.startswith("# Nexus Lessons"):
        existing = "# Nexus Lessons (weekly digest)\n\n" + existing
    # Insert new section after the H1 header.
    parts = existing.split("\n\n", 1)
    if len(parts) == 2:
        out = parts[0] + "\n\n" + new_section + parts[1].lstrip()
    else:
        out = existing.rstrip() + "\n\n" + new_section
    LESSONS_FILE.write_text(out, encoding="utf-8")
    return LESSONS_FILE


def main() -> int:
    path = aggregate()
    print(f"updated {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Task extraction from messages (Phase 19.1).

Lightweight extractor that pulls commitments out of natural-language text
('I'll send X by Friday') and turns them into reminder rows. Records to
`memory/reminders.jsonl`. Reminders fire by date — the Phase 16.5
scheduler can pick them up for delivery.

Pattern-based first to avoid burning LLM cycles on every Telegram /
email message; LLM fallback only when the regex misses but the message
looks promising (contains 'I will' / 'by Friday' / 'next week' shapes).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ollama
from langchain_core.tools import tool

ROOT = Path.home() / "AI_Agent"
LOG_PATH = ROOT / "memory" / "reminders.jsonl"
OLLAMA_URL = "http://localhost:11434"
EXTRACTOR_MODEL = "qwen3:4b"

log = logging.getLogger("nexus.task_extractor")

_COMMIT_RE = re.compile(
    r"\b(i'?ll|i will|going to|plan(?:ning)? to|need to|"
    r"should|let me|will get|will send)\b",
    re.IGNORECASE,
)
_DEADLINE_RE = re.compile(
    r"\bby\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"tomorrow|tonight|today|next week|end of (?:day|week|month)|\d{1,2}(?:st|nd|rd|th)?(?:\s+\w+)?)",
    re.IGNORECASE,
)
_DATE_FRAGMENTS = {
    "tomorrow": 1, "tonight": 0, "today": 0,
    "monday": "weekday:0", "tuesday": "weekday:1", "wednesday": "weekday:2",
    "thursday": "weekday:3", "friday": "weekday:4",
    "saturday": "weekday:5", "sunday": "weekday:6",
    "next week": 7, "end of day": 0, "end of week": "next:friday",
    "end of month": "month-end",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_due(fragment: str, ref: datetime) -> datetime | None:
    f = fragment.lower().strip()
    if f in _DATE_FRAGMENTS:
        rule = _DATE_FRAGMENTS[f]
        if isinstance(rule, int):
            return ref + timedelta(days=rule)
        if isinstance(rule, str) and rule.startswith("weekday:"):
            target = int(rule.split(":")[1])
            delta = (target - ref.weekday()) % 7 or 7
            return ref + timedelta(days=delta)
        if rule == "next:friday":
            return ref + timedelta(days=(4 - ref.weekday()) % 7 or 7)
        if rule == "month-end":
            year, month = ref.year, ref.month
            if month == 12:
                next_month = ref.replace(year=year + 1, month=1, day=1)
            else:
                next_month = ref.replace(month=month + 1, day=1)
            return next_month - timedelta(seconds=1)
    return None


def _record_reminder(text: str, due: datetime | None, *, source: str) -> dict:
    record = {
        "ts": _now().isoformat(),
        "source": source,
        "text": text[:500],
        "due": due.isoformat() if due else None,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("reminders write failed: %s", exc)
    return record


def _llm_extract(message: str) -> dict | None:
    """Fallback LLM extractor for messages where regexes hit only the
    commitment verb but no recognisable deadline fragment."""
    prompt = (
        "Extract any commitment the speaker is making in the message below. "
        "Return ONLY a JSON object {commitment, deadline_phrase} or {} if no "
        "commitment is present. No preamble.\n\n"
        f"Message: {message}\n\nJSON:"
    )
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=EXTRACTOR_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=False, think=False, keep_alive=-1,
            options={"temperature": 0.0, "num_predict": 120, "num_ctx": 4096, "format": "json"},
        )
    except Exception:
        return None
    content = ""
    if isinstance(resp, dict):
        content = ((resp.get("message") or {}).get("content") or "").strip()
    else:
        m = getattr(resp, "message", None)
        content = (getattr(m, "content", "") or "").strip()
    try:
        obj = json.loads(content)
        if isinstance(obj, dict) and obj.get("commitment"):
            return obj
    except Exception:
        return None
    return None


@tool
def extract_commitments(message: str, source: str = "manual") -> str:
    """Pull any commitments + deadlines out of a message and append them to
    memory/reminders.jsonl. Returns a one-line summary.

    Args:
        message: free text (Telegram / email body / chat).
        source:  short tag for provenance (telegram / email / chat / manual).
    """
    if not message or not message.strip():
        return "no message"
    msg = message.strip()
    found: list[dict] = []
    if _COMMIT_RE.search(msg):
        m = _DEADLINE_RE.search(msg)
        if m:
            due = _resolve_due(m.group(1), _now())
            found.append(_record_reminder(msg, due, source=source))
        else:
            obj = _llm_extract(msg)
            if obj:
                deadline_text = obj.get("deadline_phrase") or ""
                m2 = _DEADLINE_RE.search(deadline_text or "")
                due = _resolve_due(m2.group(1), _now()) if m2 else None
                found.append(_record_reminder(obj.get("commitment", msg), due, source=source))
    if not found:
        return "no commitment detected."
    parts = []
    for r in found:
        due = r.get("due") or "no deadline"
        parts.append(f"reminder: {r['text'][:60]!r} due={due}")
    return "\n".join(parts)


TASK_EXTRACTOR_TOOLS = [extract_commitments]

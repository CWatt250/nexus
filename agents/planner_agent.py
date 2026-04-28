"""Planner agent (Phase 18.1).

First stage in the agent pipeline. Decides whether a user task is concrete
enough to hand off, or vague enough to need clarifying questions back.

Cheap deterministic heuristics first (so we never burn an LLM call on a
clearly-detailed brief), then a qwen3:4b call for borderline cases.

API:
  classify_task(text)              → {kind: 'clear'|'vague', reasons: [...]}
  plan_or_clarify(text)            → {action: 'plan'|'clarify',
                                      plan: str?, questions: [str]?}
"""
from __future__ import annotations

import re
from typing import Any

import ollama

OLLAMA_URL = "http://localhost:11434"
PLANNER_MODEL = "qwen3:4b"

_VAGUE_HINTS = re.compile(
    r"^\s*(build me|make me|do something|help me|build a|make a)\b",
    re.IGNORECASE,
)
_CONCRETE_VERBS = re.compile(
    r"\b(refactor|implement|add|fix|deploy|test|migrate|index|run|"
    r"reindex|review|commit|generate|search|list|read|write|edit|"
    r"compute|summarize|translate|debug)\b",
    re.IGNORECASE,
)
_FILE_OR_REPO_RE = re.compile(r"[/.~][\w/.\-]+\.\w{1,5}|/home/|/etc/|github\.com")


def classify_task(text: str) -> dict:
    """Heuristic vague vs clear classifier."""
    msg = (text or "").strip()
    reasons: list[str] = []
    if not msg:
        return {"kind": "vague", "reasons": ["empty"]}
    word_count = len(msg.split())
    if word_count < 10:
        reasons.append(f"only {word_count} words")
    if _VAGUE_HINTS.match(msg):
        reasons.append("'build me' opener with no specifics")
    if _CONCRETE_VERBS.search(msg) or _FILE_OR_REPO_RE.search(msg):
        # if there's at least one concrete signal, override the vague hint.
        reasons = []
    return {"kind": "vague" if reasons else "clear", "reasons": reasons}


def _ask_for_clarification(msg: str) -> list[str]:
    prompt = (
        "You are Nexus's planner. The user's task is too vague to start "
        "execution. Ask 2-4 short clarifying questions that, once answered, "
        "would let us start. Return ONLY a JSON list of strings, no preamble.\n\n"
        f"User task: {msg}\n\nQuestions:"
    )
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=PLANNER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=False, think=False, keep_alive=-1,
            options={"temperature": 0.1, "num_predict": 240, "num_ctx": 4096},
        )
    except Exception:
        # Default questions if the LLM is unavailable.
        return [
            "What's the concrete deliverable? (file path, URL, or short description)",
            "What's done when this is done?",
            "Any deadline or constraints I should know about?",
        ]
    content = ""
    if isinstance(resp, dict):
        content = ((resp.get("message") or {}).get("content") or "").strip()
    else:
        m = getattr(resp, "message", None)
        content = (getattr(m, "content", "") or "").strip()
    # Try to parse a JSON array out of the response.
    import json
    m = re.search(r"\[.*\]", content, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return [str(x).strip() for x in data if isinstance(x, (str, int))][:5]
        except Exception:
            pass
    # Fall back to splitting numbered/bulleted lines.
    questions = []
    for line in content.splitlines():
        line = line.strip().lstrip("0123456789.- ")
        if line.endswith("?") and 5 < len(line) < 200:
            questions.append(line)
    return questions[:4] or ["What's the concrete deliverable?"]


def _short_plan(msg: str) -> str:
    prompt = (
        "Give a numbered plan (3-6 steps) for the user task below. Each step "
        "must be actionable and have a verification check. End with a one-line "
        "effort estimate (small / medium / large) and the suggested route "
        "(fast / mid / heavy / code / design). No preamble.\n\n"
        f"User task: {msg}\n\nPlan:"
    )
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=PLANNER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=False, think=False, keep_alive=-1,
            options={"temperature": 0.1, "num_predict": 400, "num_ctx": 4096},
        )
    except Exception as exc:
        return f"_(planner unavailable: {type(exc).__name__})_"
    if isinstance(resp, dict):
        return ((resp.get("message") or {}).get("content") or "").strip()
    m = getattr(resp, "message", None)
    return (getattr(m, "content", "") or "").strip()


def plan_or_clarify(text: str) -> dict[str, Any]:
    classification = classify_task(text)
    if classification["kind"] == "vague":
        return {
            "action": "clarify",
            "reasons": classification["reasons"],
            "questions": _ask_for_clarification(text),
        }
    return {
        "action": "plan",
        "plan": _short_plan(text),
    }

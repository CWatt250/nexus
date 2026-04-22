"""Nexus self-reflection pipeline.

After each completed turn, `reflect()` asks qwen3:4b to critique the exchange
and produces a structured JSON record:

    {
        "ts":         "<iso8601>",
        "tool":       "reflection",
        "lesson":     "<one-sentence takeaway>",
        "quality":    <1..5>,
        "tools_used": ["terminal", ...],
        "time_saved": "<rough estimate, e.g. '5 minutes'>",
        "tags":       ["kebab-case", ...],
        "user":       "<user message preview>"
    }

Every reflection appends to the nexus-core run-log. High-quality entries
(>= 4) also land in ~/AI_Agent/memory/lessons.md; low-quality (<= 2) land
in improvements.md so Nexus can learn what to fix.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import ollama

ROOT = Path.home() / "AI_Agent"
RUN_LOG = ROOT / "projects" / "nexus-core" / "run-log.jsonl"
LESSONS = ROOT / "memory" / "lessons.md"
IMPROVEMENTS = ROOT / "memory" / "improvements.md"
OLLAMA_URL = "http://localhost:11434"

MODEL = "qwen3:4b"

# Flag to enable/disable Mem0 integration
USE_MEM0 = True

SYSTEM_PROMPT = """You are a post-turn reflection critic. You review a single completed assistant turn and produce a short structured critique.

Return ONLY a JSON object with exactly these keys:
{
  "lesson":     "one concise sentence — the takeaway, useful pattern, or failure mode; write it so it would help a future turn",
  "quality":    <integer 1-5>,   // 5 = excellent, 4 = good, 3 = ok, 2 = weak, 1 = failed
  "time_saved": "<short human estimate like '2 minutes', '30 seconds', 'none', '1 hour'>",
  "tags":       [<1-3 short kebab-case topic tags like "code", "refactor", "debugging", "design", "file-io">]
}

Rules:
- Output JSON only. No markdown, no commentary, no code fences.
- Quality is about whether the assistant solved the user's actual need cleanly, not about politeness.
- `lesson` should be specific — not "be helpful" but "prefer grep_tool over reading whole files when searching".
- If nothing interesting was learned, lesson can be a short observation; still return all keys."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(entry: dict) -> None:
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _append_md(path: Path, bullet: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# (auto-generated)\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(bullet.rstrip() + "\n")


def _store_in_mem0(entry: dict) -> None:
    """Store high-quality reflection lessons in Mem0 for long-term recall.

    Only stores lessons with quality >= 4 to avoid noise."""
    if not USE_MEM0:
        return
    if entry.get("quality", 0) < 4:
        return

    try:
        from tools.mem0_tool import _get_memory, DEFAULT_USER

        lesson = entry.get("lesson", "")
        tags = entry.get("tags", [])
        tools = entry.get("tools_used", [])

        if not lesson or lesson == "(no lesson)":
            return

        # Create a rich memory entry
        mem_text = f"Lesson: {lesson}"
        if tags:
            mem_text += f" Tags: {', '.join(tags)}."
        if tools:
            mem_text += f" Tools: {', '.join(tools)}."

        mem = _get_memory()
        mem.add(mem_text, user_id=DEFAULT_USER, metadata={
            "type": "reflection",
            "quality": entry.get("quality", 0),
            "ts": entry.get("ts", ""),
        })
    except Exception:
        # Silently fail - mem0 is optional
        pass


def _extract_tool_names(messages) -> list[str]:
    """Walk a LangGraph result['messages'] and pull out invoked tool names."""
    names: list[str] = []
    for m in messages or []:
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            for call in tcs:
                n = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
                if n:
                    names.append(n)
    # dedupe preserving order
    seen = set()
    out = []
    for n in names:
        if n not in seen:
            seen.add(n); out.append(n)
    return out


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return {}


def _clamp_quality(v) -> int:
    try:
        q = int(v)
    except (TypeError, ValueError):
        return 3
    return max(1, min(5, q))


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def _ask_critic(user_msg: str, response: str, tool_names: list[str]) -> dict:
    user_payload = (
        f"USER MESSAGE:\n{user_msg.strip()[:2000]}\n\n"
        f"TOOLS INVOKED: {', '.join(tool_names) if tool_names else '(none)'}\n\n"
        f"ASSISTANT RESPONSE:\n{(response or '').strip()[:4000]}"
    )
    resp = ollama.Client(host=OLLAMA_URL).chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ],
        stream=False,
        think=False,
        format="json",
        options={"temperature": 0.1, "num_predict": 256, "num_ctx": 4096},
    )
    raw = resp["message"]["content"] if isinstance(resp, dict) else getattr(resp.message, "content", "")
    return _parse_json(raw)


def reflect(
    user_msg: str,
    response: str,
    messages=None,
    *,
    route: str | None = None,
    model: str | None = None,
) -> dict:
    """Reflect on a single completed turn.

    `messages` is the optional LangGraph `result['messages']` list so tool
    names can be auto-extracted. Returns the reflection record that was
    written to the run log (empty dict on failure)."""
    tool_names = _extract_tool_names(messages) if messages else []

    try:
        parsed = _ask_critic(user_msg, response, tool_names)
    except Exception as exc:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": "reflection",
            "error": f"{type(exc).__name__}: {exc}",
            "user": (user_msg or "").strip()[:200],
        }
        try:
            _log(entry)
        except OSError:
            pass
        return {}

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": "reflection",
        "lesson": str(parsed.get("lesson", "")).strip()[:400] or "(no lesson)",
        "quality": _clamp_quality(parsed.get("quality")),
        "tools_used": tool_names,
        "time_saved": str(parsed.get("time_saved", "")).strip()[:40] or "unknown",
        "tags": [str(t).strip().lower() for t in (parsed.get("tags") or []) if str(t).strip()][:5],
        "user": (user_msg or "").strip()[:200],
    }
    if route:
        entry["route"] = route
    if model:
        entry["model"] = model

    try:
        _log(entry)
    except OSError:
        pass

    # Surface to human-readable memory files.
    when = datetime.now().strftime("%Y-%m-%d")
    tag_str = f" [{', '.join(entry['tags'])}]" if entry["tags"] else ""
    bullet = f"- {when}{tag_str}: {entry['lesson']}"
    try:
        if entry["quality"] >= 4:
            _append_md(LESSONS, bullet)
        elif entry["quality"] <= 2:
            _append_md(IMPROVEMENTS, bullet)
    except OSError:
        pass

    # Store high-quality lessons in Mem0 for long-term recall
    _store_in_mem0(entry)

    return entry


if __name__ == "__main__":
    import sys
    u = sys.argv[1] if len(sys.argv) > 1 else "what is 2+2?"
    r = sys.argv[2] if len(sys.argv) > 2 else "4"
    print(json.dumps(reflect(u, r), indent=2, ensure_ascii=False))

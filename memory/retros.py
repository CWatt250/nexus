"""Per-turn retrospective generator (Phase 14.3).

After every interesting turn we write `memory/retros/retro_<task_id>.md`
summarising goal, actions, outcome, tokens, wall time, and lessons. The
lessons section is produced by qwen3:4b on the assumption it has just
warmed and a 200-token reply takes <2s.

Trivial turns (no tool calls AND wall < 5s AND success) are skipped to
avoid burying the retro pile in greetings.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

import ollama

ROOT = Path.home() / "AI_Agent"
MEMORY_DIR = ROOT / "memory"
TASK_LOG = MEMORY_DIR / "task_metrics.jsonl"
TOOL_LOG = MEMORY_DIR / "tool_metrics.jsonl"
RETRO_DIR = MEMORY_DIR / "retros"
OLLAMA_URL = "http://localhost:11434"
LESSONS_MODEL = "qwen3:4b"

log = logging.getLogger("nexus.retros")


def _read_jsonl(path: Path, predicate) -> list[dict]:
    if not path.exists():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if predicate(obj):
                out.append(obj)
    except OSError:
        return []
    return out


def _is_interesting(turn: dict, tool_calls: list[dict]) -> bool:
    if not turn.get("success", True):
        return True
    if turn.get("tool_calls", 0) > 0 or len(tool_calls) > 0:
        return True
    if (turn.get("wall_seconds") or 0.0) >= 5.0:
        return True
    return False


def _lessons(turn: dict, tool_calls: list[dict]) -> str:
    tool_summary = "\n".join(
        f"- {t['tool']} ({t.get('latency_ms', 0):.0f}ms, ok={t.get('success', True)})"
        for t in tool_calls[:20]
    ) or "(none)"
    prompt = (
        "You are reviewing one Nexus agent turn. Write 1-3 short bullet lessons "
        "for future runs. Each bullet must be a concrete actionable observation "
        "(\"do X next time\", \"avoid Y\", \"prefer Z\"). No preamble. Plain markdown.\n\n"
        f"User: {turn.get('user_preview','')}\n"
        f"Reply: {turn.get('reply_preview','')}\n"
        f"Wall: {turn.get('wall_seconds')}s  Route: {turn.get('route')}  "
        f"Model: {turn.get('model')}  ToolCalls: {turn.get('tool_calls')}  "
        f"Success: {turn.get('success')}\n"
        f"Tools used:\n{tool_summary}\n\nLessons:"
    )
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=LESSONS_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
            think=False,
            keep_alive=-1,
            options={"temperature": 0.2, "num_predict": 220, "num_ctx": 4096},
        )
    except Exception as exc:
        log.warning("lessons generation failed: %s", exc)
        return "_(lessons unavailable — qwen3:4b call failed)_"
    if isinstance(resp, dict):
        return ((resp.get("message") or {}).get("content") or "").strip() or "_(empty)_"
    msg = getattr(resp, "message", None)
    return (getattr(msg, "content", "") or "").strip() or "_(empty)_"


def _format(turn: dict, tool_calls: list[dict], lessons: str) -> str:
    lines = [
        f"# Retro: {turn['task_id']}",
        "",
        f"- ts: `{turn.get('ts','')}`",
        f"- route: `{turn.get('route','')}`",
        f"- model: `{turn.get('model','')}`",
        f"- wall_seconds: `{turn.get('wall_seconds','')}`",
        f"- tokens_in / out: `{turn.get('tokens_in','?')}` / `{turn.get('tokens_out','?')}`",
        f"- tool_calls: `{turn.get('tool_calls', 0)}`",
        f"- success: `{turn.get('success')}`",
    ]
    if turn.get("error"):
        lines.append(f"- error: `{turn['error']}`")
    lines += [
        "",
        "## Goal",
        f"> {turn.get('user_preview','')}",
        "",
        "## Outcome",
        f"> {turn.get('reply_preview','')}",
        "",
        "## Tool calls",
    ]
    if tool_calls:
        for t in tool_calls:
            err = f" — error: {t.get('error','')}" if not t.get("success", True) else ""
            lines.append(
                f"- `{t.get('tool')}` {t.get('latency_ms', 0):.1f}ms"
                f" tokens_in={t.get('tokens_in', 0)} tokens_out={t.get('tokens_out', 0)}{err}"
            )
    else:
        lines.append("_(none)_")
    lines += ["", "## Lessons", lessons.strip(), ""]
    return "\n".join(lines)


def generate_retro(task_id: str) -> Path | None:
    """Build memory/retros/retro_<task_id>.md from the metric streams.

    Returns the written path, or None if the turn was skipped (uninteresting)
    or the metric record couldn't be found."""
    turns = _read_jsonl(TASK_LOG, lambda o: o.get("task_id") == task_id)
    if not turns:
        return None
    turn = turns[-1]
    tool_calls = _read_jsonl(TOOL_LOG, lambda o: o.get("task_id") == task_id)
    if not _is_interesting(turn, tool_calls):
        return None
    lessons = _lessons(turn, tool_calls)
    body = _format(turn, tool_calls, lessons)
    RETRO_DIR.mkdir(parents=True, exist_ok=True)
    path = RETRO_DIR / f"retro_{task_id}.md"
    try:
        path.write_text(body, encoding="utf-8")
    except OSError as exc:
        log.warning("retro write failed: %s", exc)
        return None
    return path


def generate_retro_async(task_id: str) -> None:
    """Fire-and-forget retro generation on a daemon thread."""
    def _worker():
        try:
            generate_retro(task_id)
        except Exception as exc:
            log.warning("retro worker failed: %s", exc)

    threading.Thread(target=_worker, name=f"retro-{task_id}", daemon=True).start()

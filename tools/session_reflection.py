# tools/session_reflection.py — Post-session reflection for Nexus
# Reads recent runs from run-log, asks qwen3:4b for insights, writes lessons to RAG.

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import httpx

ROOT = Path.home() / "AI_Agent"
RUN_LOG = ROOT / "projects" / "nexus-core" / "run-log.jsonl"
LESSONS_FILE = ROOT / "projects" / "nexus-core" / "wiki" / "lessons-learned.md"
IMPROVEMENTS_FILE = ROOT / "projects" / "nexus-core" / "wiki" / "improvements.md"
OLLAMA_URL = "http://localhost:11434"


@dataclass
class ReflectionResult:
    lessons: list[str]
    improvements: list[str]
    summary: str
    has_new_insights: bool

    def to_dict(self) -> dict:
        return asdict(self)


def read_recent_runs(n: int = 10) -> list[dict]:
    """Read the last n entries from the run log."""
    if not RUN_LOG.exists():
        return []
    entries = []
    with open(RUN_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries[-n:]


def _ollama_reflect(runs: list[dict]) -> Optional[dict]:
    """Ask Ollama to reflect on recent sessions."""
    # Build a compact summary of recent runs
    run_summaries = []
    for i, run in enumerate(runs):
        ts = run.get("ts", run.get("timestamp", "?"))
        route = run.get("route", "?")
        model = run.get("model", "?")
        success = run.get("success", "?")
        tools = ", ".join(run.get("tools", run.get("tools_used", [])))
        run_summaries.append(
            f"{i+1}. [{ts}] route={route} model={model} success={success} tools={tools}"
        )
    runs_text = "\n".join(run_summaries)

    prompt = (
        "You are analyzing recent Nexus agent sessions to extract lessons and improvements.\n\n"
        f"Recent {len(runs)} sessions:\n---\n{runs_text}\n---\n\n"
        "Return ONLY a JSON object with these keys:\n"
        "- lessons: array of string lessons learned (max 3)\n"
        "- improvements: array of string improvements (max 3)\n"
        "- summary: one-sentence summary of patterns\n\n"
        "Format: {\"lessons\": [...], \"improvements\": [...], \"summary\": \"...\"}"
    )

    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": "qwen3:4b", "prompt": prompt, "stream": False, "max_tokens": 1024},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if "response" in result:
            raw = result["response"]
            # Extract JSON from response
            start = raw.index("{")
            end = raw.rindex("}") + 1
            return json.loads(raw[start:end])
    except Exception:
        pass
    return None


def run_reflection(n_runs: int = 10) -> dict:
    """Run reflection on recent runs. Returns JSON result."""
    runs = read_recent_runs(n_runs)
    if not runs:
        return {"lessons": [], "improvements": [], "summary": "No runs to reflect on.", "has_new_insights": False}

    result = _ollama_reflect(runs)
    if result is None:
        result = {
            "lessons": [],
            "improvements": [],
            "summary": "Could not run reflection (Ollama unavailable).",
            "has_new_insights": False,
        }

    # Ensure keys
    result.setdefault("lessons", [])
    result.setdefault("improvements", [])
    result.setdefault("summary", "")
    result["has_new_insights"] = bool(result["lessons"] or result["improvements"])

    # Append lessons to wiki if we have any
    if result["lessons"]:
        lessons_dir = LESSONS_FILE.parent
        lessons_dir.mkdir(parents=True, exist_ok=True)
        
        if not LESSONS_FILE.exists():
            LESSONS_FILE.write_text("# Lessons Learned\n\n")
        
        existing = LESSONS_FILE.read_text()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        for lesson in result["lessons"]:
            existing += f"- **{timestamp}**: {lesson}\n"
        
        # Deduplicate
        lines = existing.splitlines()
        seen = set()
        deduped = []
        for line in lines:
            if line.strip() and line.strip() not in seen:
                seen.add(line.strip())
                deduped.append(line)
        
        LESSONS_FILE.write_text("\n".join(deduped))

    if result["improvements"]:
        improvements_dir = IMPROVEMENTS_FILE.parent
        improvements_dir.mkdir(parents=True, exist_ok=True)
        
        if not IMPROVEMENTS_FILE.exists():
            IMPROVEMENTS_FILE.write_text("# Improvements\n\n")
        
        existing = IMPROVEMENTS_FILE.read_text()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        for imp in result["improvements"]:
            existing += f"- **{timestamp}**: {imp}\n"
        
        lines = existing.splitlines()
        seen = set()
        deduped = []
        for line in lines:
            if line.strip() and line.strip() not in seen:
                seen.add(line.strip())
                deduped.append(line)
        
        IMPROVEMENTS_FILE.write_text("\n".join(deduped))

    return result


# Tool wrapper — callable from agent loop
def session_reflection_tool(n_runs: int = 10) -> str:
    """Run reflection on the last N agent sessions. Returns JSON lessons and improvements."""
    return json.dumps(run_reflection(n_runs), indent=2)


def auto_reflect_threshold(n: int = 10) -> bool:
    """Check if auto-reflection should trigger (10+ turns since last reflection)."""
    runs = read_recent_runs(n * 2)
    # Count turns since last reflection marker
    reflection_count = sum(1 for r in runs if r.get("tool") == "session_reflection")
    return len(runs) >= n and reflection_count == 0

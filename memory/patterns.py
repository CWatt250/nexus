#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Weekly pattern analyzer for the Nexus run log.

Reads ~/AI_Agent/projects/nexus-core/run-log.jsonl, looks at the last N days
(default 7), and writes a human-readable summary to ~/AI_Agent/memory/patterns.md.

Usage:
    python3 ~/AI_Agent/memory/patterns.py           # last 7 days
    python3 ~/AI_Agent/memory/patterns.py --days 30
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home() / "AI_Agent"
RUN_LOG = HOME / "projects" / "nexus-core" / "run-log.jsonl"
OUT = HOME / "memory" / "patterns.md"


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None


def load_entries(days: int) -> list[tuple[datetime, dict]]:
    if not RUN_LOG.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[tuple[datetime, dict]] = []
    for line in RUN_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = _parse_ts(e.get("ts", ""))
        if t is None:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t < cutoff:
            continue
        out.append((t, e))
    return out


def analyze(entries):
    tools = Counter()
    tags = Counter()
    routes = Counter()
    task_types = Counter()
    qualities: list[int] = []
    hours = Counter()

    for t, e in entries:
        hours[t.hour] += 1
        kind = e.get("tool")
        if kind == "reflection":
            q = e.get("quality")
            if isinstance(q, int) and 1 <= q <= 5:
                qualities.append(q)
            for tag in e.get("tags") or []:
                tags[str(tag)] += 1
            for tu in e.get("tools_used") or []:
                tools[str(tu)] += 1
            # use the first tag as a rough task type bucket
            first_tag = (e.get("tags") or [None])[0]
            if first_tag:
                task_types[str(first_tag)] += 1
        elif kind == "terminal":
            tools["terminal"] += 1
        elif kind == "router":
            r = e.get("route")
            if r:
                routes[str(r)] += 1
        elif kind:
            tools[str(kind)] += 1

    return {
        "n_entries": len(entries),
        "n_reflections": len(qualities),
        "avg_quality": (sum(qualities) / len(qualities)) if qualities else 0.0,
        "quality_hist": Counter(qualities),
        "top_tools": tools.most_common(10),
        "top_tags": tags.most_common(10),
        "top_routes": routes.most_common(),
        "top_task_types": task_types.most_common(10),
        "peak_hours": sorted(hours.most_common(5), key=lambda kv: kv[0]),
        "all_hours": sorted(hours.items()),
    }


def render(a: dict, days: int) -> str:
    lines: list[str] = []
    lines.append(f"# Nexus Patterns — last {days} days\n")
    lines.append(f"_Generated: {datetime.now().isoformat(timespec='seconds')}_\n")
    lines.append(f"- Log entries analyzed: **{a['n_entries']}**")
    lines.append(f"- Reflections: **{a['n_reflections']}**")
    lines.append(f"- Average reflection quality: **{a['avg_quality']:.2f} / 5**")
    lines.append("")

    if a["quality_hist"]:
        lines.append("## Quality distribution")
        for q in range(1, 6):
            n = a["quality_hist"].get(q, 0)
            bar = "█" * n
            lines.append(f"- {q}/5 — {n:>3} {bar}")
        lines.append("")

    if a["top_tools"]:
        lines.append("## Most used tools")
        for name, n in a["top_tools"]:
            lines.append(f"- `{name}` — {n}")
        lines.append("")

    if a["top_task_types"]:
        lines.append("## Most common task types (by reflection tag)")
        for name, n in a["top_task_types"]:
            lines.append(f"- `{name}` — {n}")
        lines.append("")

    if a["top_tags"]:
        lines.append("## All tags")
        for name, n in a["top_tags"]:
            lines.append(f"- `{name}` — {n}")
        lines.append("")

    if a["top_routes"]:
        lines.append("## Router decisions")
        for name, n in a["top_routes"]:
            lines.append(f"- `{name}` — {n}")
        lines.append("")

    if a["all_hours"]:
        lines.append("## Hour-of-day usage (UTC)")
        max_n = max(n for _, n in a["all_hours"]) or 1
        for hour, n in a["all_hours"]:
            width = int(40 * n / max_n)
            lines.append(f"- {hour:02d}:00  {'▇' * width} {n}")
        lines.append("")

    if not a["n_entries"]:
        lines.append("_No entries in the window._\n")

    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Summarize recent Nexus activity.")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    entries = load_entries(args.days)
    a = analyze(entries)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(a, args.days), encoding="utf-8")
    print(f"Wrote {OUT} — {a['n_entries']} entries, {a['n_reflections']} reflections, avg quality {a['avg_quality']:.2f}.")


if __name__ == "__main__":
    main()

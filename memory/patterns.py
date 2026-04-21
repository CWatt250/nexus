#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Nexus pattern analyzer.

Reads ~/AI_Agent/projects/nexus-core/run-log.jsonl (and the auxiliary
git-activity.log if present), analyzes the last N days of activity, and
writes:

  - ~/AI_Agent/memory/patterns.md          — full breakdown
  - ~/AI_Agent/memory/weekly-digest.md     — a condensed weekly digest

Metrics it tracks:
  - hour-of-day usage distribution + peak hour
  - reflection topic tags / first-tag bucketing
  - reflection quality trend (first-half vs second-half of the window)
  - router route mix
  - most-used GitHub repos (from terminal commands + github_* tool calls)
  - files most frequently read/written (from file_* tool reflections +
    path references inside terminal commands)
  - git commits from git-activity.log (per-repo subject histogram)

Usage:
    python3 ~/AI_Agent/memory/patterns.py                 # last 7 days
    python3 ~/AI_Agent/memory/patterns.py --days 30
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home() / "AI_Agent"
RUN_LOG = HOME / "projects" / "nexus-core" / "run-log.jsonl"
GIT_LOG = HOME / "memory" / "git-activity.log"
PATTERNS_OUT = HOME / "memory" / "patterns.md"
DIGEST_OUT = HOME / "memory" / "weekly-digest.md"

GITHUB_URL_RE = re.compile(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git|/|\s|$)")
GH_CLI_REPO_RE = re.compile(r"\bgh\s+repo\s+(?:clone|view|create)\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
PATH_RE = re.compile(r"(?:^|\s|['\"=])(/(?:[\w.@+\-]+/)+[\w.@+\-]*|~/[\w./@+\-]+)")

FILE_TOOLS = {
    "file_read_tool": "read",
    "file_write_tool": "write",
    "file_edit_tool": "edit",
}


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


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def load_entries(days: int) -> tuple[list[tuple[datetime, dict]], list[tuple[datetime, dict]]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    run: list[tuple[datetime, dict]] = []
    for e in _iter_jsonl(RUN_LOG):
        t = _parse_ts(e.get("ts", ""))
        if not t:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t >= cutoff:
            run.append((t, e))
    git: list[tuple[datetime, dict]] = []
    for e in _iter_jsonl(GIT_LOG):
        t = _parse_ts(e.get("ts", ""))
        if not t:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t >= cutoff:
            git.append((t, e))
    return run, git


def _quality_trend(qualities_by_ts: list[tuple[datetime, int]]) -> tuple[str, float, float]:
    if len(qualities_by_ts) < 4:
        return "insufficient data", 0.0, 0.0
    qualities_by_ts.sort(key=lambda kv: kv[0])
    mid = len(qualities_by_ts) // 2
    first = [q for _, q in qualities_by_ts[:mid]]
    second = [q for _, q in qualities_by_ts[mid:]]
    a = sum(first) / len(first)
    b = sum(second) / len(second)
    if b - a > 0.3:
        return "improving", a, b
    if a - b > 0.3:
        return "declining", a, b
    return "stable", a, b


def analyze(run_entries, git_entries):
    hours: Counter[int] = Counter()
    tools: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    routes: Counter[str] = Counter()
    task_types: Counter[str] = Counter()
    github_repos: Counter[str] = Counter()
    file_paths_by_action: dict[str, Counter[str]] = defaultdict(Counter)
    git_repos: Counter[str] = Counter()
    git_authors: Counter[str] = Counter()
    qualities: list[int] = []
    qualities_by_ts: list[tuple[datetime, int]] = []

    for t, e in run_entries:
        hours[t.hour] += 1
        kind = e.get("tool")

        if kind == "reflection":
            q = e.get("quality")
            if isinstance(q, int) and 1 <= q <= 5:
                qualities.append(q)
                qualities_by_ts.append((t, q))
            for tag in e.get("tags") or []:
                tags[str(tag)] += 1
            tus = e.get("tools_used") or []
            for tu in tus:
                tools[str(tu)] += 1
                action = FILE_TOOLS.get(str(tu))
                if action:
                    pass  # path unknown from reflection; counted below via terminal parse
                if str(tu).startswith("github_"):
                    github_repos["(github tool invocation)"] += 1
            first_tag = (e.get("tags") or [None])[0]
            if first_tag:
                task_types[str(first_tag)] += 1

        elif kind == "terminal":
            tools["terminal"] += 1
            cmd = e.get("command", "") or ""
            for m in GITHUB_URL_RE.finditer(cmd):
                github_repos[m.group(1).rstrip("/")] += 1
            for m in GH_CLI_REPO_RE.finditer(cmd):
                github_repos[m.group(1)] += 1
            # Cheap path extraction — catches absolute and ~/ paths.
            for m in PATH_RE.finditer(cmd):
                path = m.group(1)
                if any(cmd.split()[:1] == [tool] for tool in ("rm", "mv")):
                    file_paths_by_action["modify"][path] += 1
                # Look for common read/write tokens.
                words = cmd.split()
                if not words:
                    continue
                head = words[0].lstrip("!")
                if head in ("cat", "less", "tail", "head", "grep"):
                    file_paths_by_action["read"][path] += 1
                elif head in ("touch", "tee", ">>", "cp", "install"):
                    file_paths_by_action["write"][path] += 1
                elif ">" in cmd or ">>" in cmd:
                    file_paths_by_action["write"][path] += 1

        elif kind == "router":
            r = e.get("route")
            if r:
                routes[str(r)] += 1

        elif kind:
            tools[str(kind)] += 1

    for _, e in git_entries:
        repo = e.get("repo") or "?"
        git_repos[repo] += 1
        author = e.get("author") or ""
        if author:
            git_authors[author] += 1

    trend, first_avg, second_avg = _quality_trend(qualities_by_ts)

    peak_hour = max(hours.items(), key=lambda kv: kv[1])[0] if hours else None

    return {
        "n_entries": len(run_entries),
        "n_reflections": len(qualities),
        "n_commits": len(git_entries),
        "avg_quality": (sum(qualities) / len(qualities)) if qualities else 0.0,
        "quality_hist": Counter(qualities),
        "quality_trend": trend,
        "quality_first_avg": first_avg,
        "quality_second_avg": second_avg,
        "peak_hour": peak_hour,
        "all_hours": sorted(hours.items()),
        "top_tools": tools.most_common(10),
        "top_tags": tags.most_common(10),
        "top_routes": routes.most_common(),
        "top_task_types": task_types.most_common(10),
        "top_github_repos": github_repos.most_common(10),
        "top_read_files": file_paths_by_action.get("read", Counter()).most_common(10),
        "top_write_files": file_paths_by_action.get("write", Counter()).most_common(10),
        "top_git_repos": git_repos.most_common(10),
        "top_git_authors": git_authors.most_common(5),
    }


def render_patterns(a: dict, days: int) -> str:
    lines: list[str] = []
    lines.append(f"# Nexus Patterns — last {days} days\n")
    lines.append(f"_Generated: {datetime.now().isoformat(timespec='seconds')}_\n")
    lines.append(f"- Log entries analyzed: **{a['n_entries']}**")
    lines.append(f"- Reflections: **{a['n_reflections']}**")
    lines.append(f"- Commits observed: **{a['n_commits']}**")
    lines.append(f"- Average reflection quality: **{a['avg_quality']:.2f} / 5**")
    if a["quality_trend"] != "insufficient data":
        lines.append(
            f"- Quality trend: **{a['quality_trend']}** "
            f"({a['quality_first_avg']:.2f} → {a['quality_second_avg']:.2f})"
        )
    if a["peak_hour"] is not None:
        lines.append(f"- Peak hour (UTC): **{a['peak_hour']:02d}:00**")
    lines.append("")

    if a["quality_hist"]:
        lines.append("## Quality distribution")
        for q in range(1, 6):
            n = a["quality_hist"].get(q, 0)
            bar = "█" * n
            lines.append(f"- {q}/5 — {n:>3} {bar}")
        lines.append("")

    for title, key in (
        ("Most used tools", "top_tools"),
        ("Most common task types (first reflection tag)", "top_task_types"),
        ("All tags", "top_tags"),
        ("Router decisions", "top_routes"),
        ("Most active GitHub repos", "top_github_repos"),
        ("Files most frequently read", "top_read_files"),
        ("Files most frequently written", "top_write_files"),
        ("Git repos with most commits", "top_git_repos"),
        ("Top commit authors", "top_git_authors"),
    ):
        items = a.get(key) or []
        if items:
            lines.append(f"## {title}")
            for name, n in items:
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


def render_digest(a: dict) -> str:
    lines: list[str] = []
    now = datetime.now()
    iso = now.isoformat(timespec="seconds")
    lines.append(f"# Nexus Weekly Digest — {now.strftime('%Y-%m-%d')}\n")
    lines.append(f"_Generated: {iso}_\n")
    lines.append("## Activity this week")
    lines.append(f"- Run-log entries: **{a['n_entries']}**")
    lines.append(f"- Reflections: **{a['n_reflections']}** (avg quality {a['avg_quality']:.2f}/5)")
    if a["quality_trend"] != "insufficient data":
        lines.append(
            f"- Quality trend: **{a['quality_trend']}** "
            f"({a['quality_first_avg']:.2f} → {a['quality_second_avg']:.2f})"
        )
    lines.append(f"- Git commits observed: **{a['n_commits']}**")
    if a["peak_hour"] is not None:
        lines.append(f"- Peak working hour (UTC): **{a['peak_hour']:02d}:00**")
    lines.append("")

    def _bullet(label: str, items):
        if not items:
            return
        lines.append(f"### {label}")
        for name, n in items[:5]:
            lines.append(f"- {name} ({n})")
        lines.append("")

    _bullet("Top tools", a.get("top_tools"))
    _bullet("Top topics", a.get("top_tags"))
    _bullet("Top GitHub repos", a.get("top_github_repos"))
    _bullet("Hot files (reads)", a.get("top_read_files"))
    _bullet("Hot files (writes)", a.get("top_write_files"))
    _bullet("Git repos most changed", a.get("top_git_repos"))
    return "\n".join(lines) + "\n"


def write_outputs(a: dict, days: int) -> None:
    PATTERNS_OUT.parent.mkdir(parents=True, exist_ok=True)
    PATTERNS_OUT.write_text(render_patterns(a, days), encoding="utf-8")
    DIGEST_OUT.write_text(render_digest(a), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Summarize recent Nexus activity.")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    run_entries, git_entries = load_entries(args.days)
    a = analyze(run_entries, git_entries)
    write_outputs(a, args.days)
    print(
        f"Wrote {PATTERNS_OUT} and {DIGEST_OUT} — "
        f"{a['n_entries']} entries, {a['n_reflections']} reflections, "
        f"avg quality {a['avg_quality']:.2f} ({a['quality_trend']})."
    )


if __name__ == "__main__":
    main()

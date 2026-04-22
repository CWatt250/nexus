"""Router telemetry dashboard.

Analyzes run-log.jsonl to show:
- Route distribution (how often each route is used)
- Quality histogram per route
- Time-saved totals
- Error rates
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

RUN_LOG = Path.home() / "AI_Agent" / "projects" / "nexus-core" / "run-log.jsonl"
DASHBOARD_OUTPUT = Path.home() / "AI_Agent" / "memory" / "router-dashboard.md"


def _parse_time_saved(ts: str) -> float:
    """Convert time-saved string to minutes."""
    if not ts or ts in ("unknown", "none", "(none)"):
        return 0.0
    ts = ts.lower().strip()
    # Handle common formats
    for unit, mult in [("hour", 60), ("minute", 1), ("min", 1), ("second", 1/60), ("sec", 1/60)]:
        if unit in ts:
            try:
                # Extract number
                num = "".join(c for c in ts if c.isdigit() or c == ".")
                return float(num) * mult
            except ValueError:
                pass
    return 0.0


def load_entries(days: int = 30) -> list[dict]:
    """Load run-log entries from the last N days."""
    if not RUN_LOG.exists():
        return []

    cutoff = datetime.now() - timedelta(days=days)
    entries = []

    try:
        with RUN_LOG.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    # Parse timestamp
                    ts_str = entry.get("ts", "")
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if ts.replace(tzinfo=None) >= cutoff:
                                entries.append(entry)
                        except ValueError:
                            entries.append(entry)  # Include if can't parse date
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    return entries


def compute_stats(entries: list[dict]) -> dict:
    """Compute telemetry stats from entries."""
    stats = {
        "total_entries": len(entries),
        "route_counts": defaultdict(int),
        "route_quality": defaultdict(list),
        "route_time_saved": defaultdict(float),
        "tool_counts": defaultdict(int),
        "quality_histogram": defaultdict(int),
        "errors": 0,
        "reflections": 0,
        "routings": 0,
    }

    for entry in entries:
        tool_type = entry.get("tool", "")

        if tool_type == "router":
            stats["routings"] += 1
            route = entry.get("route", "unknown")
            stats["route_counts"][route] += 1
            if "error" in entry:
                stats["errors"] += 1

        elif tool_type == "reflection":
            stats["reflections"] += 1
            quality = entry.get("quality", 0)
            stats["quality_histogram"][quality] += 1

            route = entry.get("route", "mid")
            if quality:
                stats["route_quality"][route].append(quality)

            time_saved = _parse_time_saved(entry.get("time_saved", ""))
            stats["route_time_saved"][route] += time_saved

            for tool_name in entry.get("tools_used", []):
                stats["tool_counts"][tool_name] += 1

    return stats


def format_dashboard(stats: dict) -> str:
    """Format stats as markdown dashboard."""
    lines = ["# Router Telemetry Dashboard", ""]
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Total Entries:** {stats['total_entries']}")
    lines.append(f"**Routings:** {stats['routings']}")
    lines.append(f"**Reflections:** {stats['reflections']}")
    lines.append(f"**Errors:** {stats['errors']}")
    lines.append("")

    # Route distribution
    lines.append("## Route Distribution")
    lines.append("")
    total_routes = sum(stats["route_counts"].values()) or 1
    for route in ["fast", "mid", "heavy", "code", "design"]:
        count = stats["route_counts"].get(route, 0)
        pct = (count / total_routes) * 100
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        lines.append(f"- **{route}**: {count} ({pct:.1f}%) {bar}")
    lines.append("")

    # Quality by route
    lines.append("## Quality by Route")
    lines.append("")
    for route in ["fast", "mid", "heavy", "code", "design"]:
        qualities = stats["route_quality"].get(route, [])
        if qualities:
            avg = sum(qualities) / len(qualities)
            lines.append(f"- **{route}**: avg {avg:.2f} (n={len(qualities)})")
        else:
            lines.append(f"- **{route}**: (no data)")
    lines.append("")

    # Quality histogram
    lines.append("## Quality Histogram")
    lines.append("")
    for q in range(1, 6):
        count = stats["quality_histogram"].get(q, 0)
        stars = "⭐" * q
        bar = "█" * (count // 2) if count > 0 else ""
        lines.append(f"- {stars}: {count} {bar}")
    lines.append("")

    # Time saved by route
    lines.append("## Time Saved by Route")
    lines.append("")
    total_time = sum(stats["route_time_saved"].values())
    for route in ["fast", "mid", "heavy", "code", "design"]:
        time_min = stats["route_time_saved"].get(route, 0)
        if time_min > 0:
            hours = time_min / 60
            if hours >= 1:
                lines.append(f"- **{route}**: {hours:.1f} hours")
            else:
                lines.append(f"- **{route}**: {time_min:.0f} minutes")
    if total_time > 0:
        total_hours = total_time / 60
        lines.append(f"\n**Total time saved:** {total_hours:.1f} hours")
    lines.append("")

    # Top tools
    lines.append("## Top Tools")
    lines.append("")
    sorted_tools = sorted(stats["tool_counts"].items(), key=lambda x: -x[1])[:15]
    for tool_name, count in sorted_tools:
        lines.append(f"- {tool_name}: {count}")
    lines.append("")

    return "\n".join(lines)


def save_dashboard(dashboard: str) -> str:
    """Save dashboard to file."""
    DASHBOARD_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_OUTPUT.write_text(dashboard, encoding="utf-8")
    return str(DASHBOARD_OUTPUT)


@tool
def router_telemetry(days: int = 30) -> str:
    """Generate a router telemetry dashboard from the run log.

    Args:
        days: Number of days to analyze (default 30)

    Returns a formatted report showing:
    - Route distribution
    - Quality by route
    - Time saved estimates
    - Top tools used"""
    entries = load_entries(days=days)

    if not entries:
        return "No run-log entries found. Run some conversations first!"

    stats = compute_stats(entries)
    dashboard = format_dashboard(stats)

    # Save to file
    path = save_dashboard(dashboard)

    return f"{dashboard}\n\n---\nSaved to: {path}"


@tool
def router_stats() -> str:
    """Quick summary of router statistics (last 7 days)."""
    entries = load_entries(days=7)

    if not entries:
        return "No entries in the last 7 days."

    stats = compute_stats(entries)

    lines = [
        f"Last 7 days: {stats['total_entries']} entries",
        f"Routings: {stats['routings']} | Reflections: {stats['reflections']} | Errors: {stats['errors']}",
        "",
        "Route usage:",
    ]

    for route in ["fast", "mid", "heavy", "code", "design"]:
        count = stats["route_counts"].get(route, 0)
        lines.append(f"  {route}: {count}")

    avg_quality = []
    for qualities in stats["route_quality"].values():
        avg_quality.extend(qualities)

    if avg_quality:
        overall_avg = sum(avg_quality) / len(avg_quality)
        lines.append(f"\nAvg quality: {overall_avg:.2f}/5")

    return "\n".join(lines)


# CLI interface
if __name__ == "__main__":
    import sys

    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    print(router_telemetry.invoke({"days": days}))

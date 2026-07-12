"""Game links — list playable games in ~/AI_Agent/games with tap-to-play URLs.

All-local game studio (2026-07-12): local dispatches auto-save HTML games
to ~/AI_Agent/games, served by nexus-games.service on :8788. This tool is
the answer to "send me the link" / "what games do I have" — before it,
those questions hit a no-tools chat model that hallucinated paths.
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

GAMES_DIR = Path.home() / "AI_Agent" / "games"
_DEFAULT_BASE = "http://100.124.210.84:8788"


def _url_base() -> str:
    """games_url_base from config/cost_limits.yaml (result_reporter block),
    falling back to the Tailscale default."""
    try:
        import yaml  # noqa: PLC0415
        cfg = Path.home() / "AI_Agent" / "config" / "cost_limits.yaml"
        with cfg.open(encoding="utf-8") as fh:
            full = yaml.safe_load(fh) or {}
        return str(full.get("result_reporter", {}).get(
            "games_url_base", _DEFAULT_BASE)).rstrip("/")
    except Exception:
        return _DEFAULT_BASE


@tool
def game_links(limit: int = 5) -> str:
    """List the newest playable games with their play URLs, newest first.
    Use for: "send me the link", "link to the game", "what games do I
    have", "pull up the snake game"."""
    if not GAMES_DIR.exists():
        return "No games directory yet — build a game first."
    pages = sorted(
        (f for f in GAMES_DIR.glob("*.html") if f.is_file()),
        key=lambda f: f.stat().st_mtime, reverse=True,
    )
    if not pages:
        return "No games saved yet — build one and it will land here."
    base = _url_base()
    limit = max(1, min(int(limit or 5), 20))
    lines = [f"▶ {f.stem}: {base}/{f.name}" for f in pages[:limit]]
    if len(pages) > limit:
        lines.append(f"…and {len(pages) - limit} more at {base}/")
    return "\n".join(lines)

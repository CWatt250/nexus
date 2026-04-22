"""OpenGame Integration Tool for Nexus agent — generates playable web games from prompts."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

OPENGAME_DIR = Path.home() / "AI_Agent" / "tools" / "OpenGame"
OUTPUT_DIR = Path.home() / "AI_Agent" / "output" / "games"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _is_opengame_installed() -> bool:
    """Check if OpenGame is installed and built."""
    return (OPENGAME_DIR / "node_modules").exists() and (
        (OPENGAME_DIR / "dist").exists() or (OPENGAME_DIR / "build").exists()
    )


@tool
def opengame_create(prompt: str, game_name: Optional[str] = None) -> str:
    """Generate a complete playable web game from a text prompt using OpenGame.

    Args:
        prompt: Description of the game to create (e.g., "A simple snake game with neon colors")
        game_name: Optional name for the game folder. Auto-generated if not provided.

    Returns:
        Path to the generated game's index.html or error message
    """
    if not _is_opengame_installed():
        return (
            "Error: OpenGame is not installed.\n\n"
            "To install OpenGame, run these commands:\n"
            "  cd ~/AI_Agent/tools\n"
            "  git clone https://github.com/leigest519/OpenGame.git\n"
            "  cd OpenGame\n"
            "  npm install\n"
            "  npm run build\n"
            "  npm link\n\n"
            "Then try again."
        )

    # Generate game name if not provided
    if not game_name:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        safe_prompt = "".join(c if c.isalnum() else "_" for c in prompt[:20])
        game_name = f"game_{timestamp}_{safe_prompt}"

    game_dir = OUTPUT_DIR / game_name
    game_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Run OpenGame to generate the game
        # Note: OpenGame CLI interface may vary - adjust command as needed
        result = subprocess.run(
            ["npx", "opengame", "create", "--prompt", prompt, "--output", str(game_dir)],
            cwd=str(OPENGAME_DIR),
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout for game generation
        )

        if result.returncode != 0:
            # Try alternative invocation
            result = subprocess.run(
                ["node", "index.js", "--prompt", prompt, "--output", str(game_dir)],
                cwd=str(OPENGAME_DIR),
                capture_output=True,
                text=True,
                timeout=300,
            )

        if result.returncode != 0:
            return f"Error generating game:\n{result.stderr or result.stdout}"

        # Find the generated index.html
        index_path = game_dir / "index.html"
        if index_path.exists():
            return f"Game generated successfully!\nPath: {index_path}\nOpen in browser to play."

        # Check for alternative output
        html_files = list(game_dir.glob("*.html"))
        if html_files:
            return f"Game generated!\nPath: {html_files[0]}\nOpen in browser to play."

        return f"Game generation completed but no HTML output found in {game_dir}"

    except subprocess.TimeoutExpired:
        return "Error: Game generation timed out (5 minute limit)"
    except FileNotFoundError:
        return "Error: OpenGame CLI not found. Make sure npm link was run."
    except Exception as e:
        return f"Error creating game: {type(e).__name__}: {e}"


@tool
def opengame_list_games(limit: int = 10) -> str:
    """List recently generated games.

    Args:
        limit: Maximum number of games to list

    Returns:
        List of game directories with timestamps
    """
    try:
        if not OUTPUT_DIR.exists():
            return "No games generated yet."

        games = sorted(
            [d for d in OUTPUT_DIR.iterdir() if d.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not games:
            return "No games generated yet."

        result = f"Recent games (showing {min(len(games), limit)} of {len(games)}):\n"
        for game in games[:limit]:
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(game.stat().st_mtime))
            index = game / "index.html"
            status = "playable" if index.exists() else "incomplete"
            result += f"  {mtime} - {game.name} ({status})\n"

        return result

    except Exception as e:
        return f"Error listing games: {type(e).__name__}: {e}"


# Export tools
OPENGAME_TOOLS = [opengame_create, opengame_list_games]

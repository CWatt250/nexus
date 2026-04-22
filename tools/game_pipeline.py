"""Game Pipeline Orchestrator — End-to-end game generation pipeline."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests
from langchain_core.tools import tool

OLLAMA_URL = "http://localhost:11434"
OUTPUT_DIR = Path.home() / "AI_Agent" / "output" / "games"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _call_llm(prompt: str, max_tokens: int = 2000) -> str:
    """Call Ollama for LLM tasks."""
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": "qwen3:4b",
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": max_tokens},
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except Exception as e:
        return f"LLM Error: {e}"


@tool
def create_game(prompt: str, game_name: Optional[str] = None) -> str:
    """Create a complete game from a text prompt using the full pipeline.

    This orchestrates multiple tools to:
    1. Generate a game design document
    2. Create game code using OpenGame
    3. Generate sprites/images
    4. Generate sound effects
    5. Generate background music
    6. Generate voice acting
    7. Assemble the final game
    8. Deploy to a hosting platform

    Args:
        prompt: Description of the game to create (e.g., "A retro space shooter with neon visuals")
        game_name: Optional name for the game folder

    Returns:
        Summary of what was created and any play links
    """
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    if not game_name:
        safe_prompt = "".join(c if c.isalnum() else "_" for c in prompt[:20])
        game_name = f"game_{timestamp}_{safe_prompt}"

    game_dir = OUTPUT_DIR / game_name
    game_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "game_name": game_name,
        "game_dir": str(game_dir),
        "steps_completed": [],
        "steps_failed": [],
        "play_url": None,
    }

    # Step 1: Generate Game Design Document
    print(f"[Pipeline] Step 1: Generating game design document...")
    gdd_prompt = f"""Create a game design document for this game concept:
{prompt}

Include:
1. Game Overview (1-2 sentences)
2. Core Mechanics (3-5 bullet points)
3. Visual Style (colors, art style)
4. Sound Design (music mood, key sound effects needed)
5. Characters/Entities (if applicable)
6. Win/Lose Conditions

Keep it concise and actionable."""

    gdd = _call_llm(gdd_prompt)
    gdd_path = game_dir / "game_design_doc.md"
    gdd_path.write_text(f"# Game Design Document\n\n{gdd}")
    results["steps_completed"].append("Game Design Document")

    # Step 2: Generate Game Code (using OpenGame or fallback)
    print(f"[Pipeline] Step 2: Generating game code...")
    try:
        from tools.opengame_tool import opengame_create
        code_result = opengame_create.invoke({"prompt": prompt, "game_name": f"{game_name}_code"})
        if "Error" not in code_result:
            results["steps_completed"].append("Game Code (OpenGame)")
        else:
            results["steps_failed"].append(f"Game Code: {code_result}")
    except Exception as e:
        results["steps_failed"].append(f"Game Code: {e}")

    # Step 3: Generate Images/Sprites
    print(f"[Pipeline] Step 3: Generating sprites...")
    try:
        from tools.image_gen_tool import generate_image
        sprite_prompts = [
            f"game sprite: {prompt}, pixel art style, transparent background",
            f"game background: {prompt}, wide landscape",
        ]
        for i, sp in enumerate(sprite_prompts):
            result = generate_image.invoke({
                "prompt": sp,
                "filename": f"{game_name}_sprite_{i}",
            })
        results["steps_completed"].append("Sprites Generated")
    except Exception as e:
        results["steps_failed"].append(f"Sprites: {e}")

    # Step 4: Generate Sound Effects
    print(f"[Pipeline] Step 4: Generating sound effects...")
    try:
        from tools.audio_gen_tool import generate_sfx
        sfx_prompts = ["game start sound effect", "action impact sound", "victory fanfare"]
        for i, sfx in enumerate(sfx_prompts):
            result = generate_sfx.invoke({
                "prompt": sfx,
                "duration": 2.0,
                "filename": f"{game_name}_sfx_{i}",
            })
        results["steps_completed"].append("Sound Effects")
    except Exception as e:
        results["steps_failed"].append(f"SFX: {e}")

    # Step 5: Generate Music
    print(f"[Pipeline] Step 5: Generating background music...")
    try:
        from tools.audio_gen_tool import generate_music
        result = generate_music.invoke({
            "prompt": f"game background music for {prompt}, loopable",
            "duration": 15.0,
            "filename": f"{game_name}_bgm",
        })
        results["steps_completed"].append("Background Music")
    except Exception as e:
        results["steps_failed"].append(f"Music: {e}")

    # Step 6: Generate Voice Acting (optional - only if characters)
    print(f"[Pipeline] Step 6: Generating voice acting...")
    try:
        from tools.bark_tool import bark_speak
        result = bark_speak.invoke({
            "text": "Welcome to the game! Good luck!",
            "voice_preset": "announcer",
            "filename": f"{game_name}_voice_intro",
        })
        results["steps_completed"].append("Voice Acting")
    except Exception as e:
        results["steps_failed"].append(f"Voice: {e}")

    # Step 7: Create index.html if OpenGame didn't
    index_path = game_dir / "index.html"
    if not index_path.exists():
        print(f"[Pipeline] Step 7: Creating basic HTML game...")
        html_template = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{game_name}</title>
    <style>
        body {{ margin: 0; background: #1a1a2e; color: #fff; font-family: sans-serif; }}
        #game {{ width: 100vw; height: 100vh; display: flex; align-items: center; justify-content: center; }}
        h1 {{ text-align: center; }}
    </style>
</head>
<body>
    <div id="game">
        <div>
            <h1>{game_name}</h1>
            <p>Game generated from: "{prompt}"</p>
            <p>See game_design_doc.md for full design.</p>
        </div>
    </div>
</body>
</html>"""
        index_path.write_text(html_template)
        results["steps_completed"].append("HTML Placeholder")

    # Step 8: Deploy to GitHub Pages (if configured)
    print(f"[Pipeline] Step 8: Deploying...")
    try:
        from tools.vercel_tool import vercel_deploy
        deploy_result = vercel_deploy.invoke({
            "project_dir": str(game_dir),
            "project_name": game_name,
        })
        if "https://" in deploy_result:
            results["play_url"] = deploy_result.split("URL: ")[-1].split("\n")[0]
            results["steps_completed"].append("Deployed to Vercel")
        else:
            results["steps_failed"].append(f"Deploy: {deploy_result}")
    except Exception as e:
        results["steps_failed"].append(f"Deploy: {e}")

    # Step 9: Notify via Telegram
    try:
        from tools.telegram_tool import notify_sync
        msg = f"Game '{game_name}' created!\n"
        msg += f"Completed: {len(results['steps_completed'])} steps\n"
        if results["play_url"]:
            msg += f"Play at: {results['play_url']}"
        notify_sync(msg)
    except Exception:
        pass

    # Summary
    summary = f"""
Game Pipeline Complete: {game_name}

Location: {game_dir}

Steps Completed ({len(results['steps_completed'])}):
{chr(10).join('  - ' + s for s in results['steps_completed'])}

Steps Failed ({len(results['steps_failed'])}):
{chr(10).join('  - ' + s for s in results['steps_failed']) if results['steps_failed'] else '  None!'}

Play URL: {results['play_url'] or 'Not deployed'}
"""

    # Save results
    (game_dir / "pipeline_results.json").write_text(json.dumps(results, indent=2))

    return summary


@tool
def list_created_games(limit: int = 10) -> str:
    """List games created by the pipeline.

    Args:
        limit: Maximum number of games to list

    Returns:
        List of game directories with creation dates
    """
    try:
        games = sorted(
            [d for d in OUTPUT_DIR.iterdir() if d.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not games:
            return "No games created yet."

        result = f"Created games ({min(len(games), limit)} of {len(games)}):\n"
        for game in games[:limit]:
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(game.stat().st_mtime))
            has_index = (game / "index.html").exists()
            status = "playable" if has_index else "partial"
            result += f"  {mtime} - {game.name} ({status})\n"

        return result

    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


# Export tools
GAME_PIPELINE_TOOLS = [create_game, list_created_games]

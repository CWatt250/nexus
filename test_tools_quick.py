#!/usr/bin/env python3
"""Quick test of critical tool imports."""

def test_imports():
    errors = []

    # YouTube tools
    try:
        from tools.youtube_tool import youtube_transcript, youtube_summary
        print("YouTube tools: OK")
    except Exception as e:
        errors.append(f"YouTube: {e}")
        print(f"YouTube tools: FAIL - {e}")

    # Telegram tools
    try:
        from tools.telegram_tool import telegram_notify, telegram_send_file
        print("Telegram tools: OK")
    except Exception as e:
        errors.append(f"Telegram: {e}")
        print(f"Telegram tools: FAIL - {e}")

    # Computer use tools
    try:
        from tools.computer_use_tool import (
            mouse_move, mouse_click, screenshot, keyboard_type
        )
        print("Computer use tools: OK")
    except Exception as e:
        errors.append(f"Computer use: {e}")
        print(f"Computer use tools: FAIL - {e}")

    # Game pipeline
    try:
        from tools.game_pipeline import create_game, list_created_games
        print("Game pipeline: OK")
    except Exception as e:
        errors.append(f"Game pipeline: {e}")
        print(f"Game pipeline: FAIL - {e}")

    # Agents
    try:
        from agents.orchestrator import Orchestrator
        from agents.coder_agent import CoderAgent
        print("Agents: OK")
    except Exception as e:
        errors.append(f"Agents: {e}")
        print(f"Agents: FAIL - {e}")

    print(f"\nTotal: {5 - len(errors)}/5 passing")
    return len(errors) == 0

if __name__ == "__main__":
    test_imports()

#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Nexus voice assistant mode.

Press Enter → record until silence → whisper transcribes → agent replies
→ Kokoro speaks the reply. Type `quit` (or Ctrl-D) to exit.

Run with:  python3 ~/AI_Agent/voice_loop.py
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage

import router  # noqa: E402
from memory import sessions  # noqa: E402
from nexus import (  # noqa: E402
    agent_for_message,
    extend_tools_with_mcp,
    load_system_prompt,
    set_system_prompt,
    strip_thinking,
)
from nexus import _extract_reply, _spawn_reflection  # noqa: E402
from tools.tts_tool import speak  # noqa: E402
from tools.whisper_tool import record_and_transcribe  # noqa: E402


def _banner(thread_id: str) -> None:
    print("=" * 60)
    print(" nexus voice mode — thread", thread_id[:8])
    print(" Enter = record (silence-stops), 'quit' or Ctrl-D = exit")
    print("=" * 60)


def main() -> None:
    set_system_prompt(load_system_prompt())
    extend_tools_with_mcp()

    thread_id = str(uuid.uuid4())
    sessions.set_current_thread(thread_id)
    sessions.touch_session(thread_id, source="voice")
    _banner(thread_id)

    while True:
        try:
            cmd = input("press enter> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return
        if cmd in ("quit", "exit", "q"):
            print("bye.")
            return

        print("recording…")
        user_text = record_and_transcribe(max_seconds=30)
        user_text = (user_text or "").strip()
        if not user_text or user_text.startswith("ERROR:"):
            print(f"(skipped — {user_text or 'no audio captured'})")
            continue
        print(f"you> {user_text}")

        agent, route, model = agent_for_message(user_text)
        print(f"[router: {route} → {model}]")
        config = {"configurable": {"thread_id": thread_id}}
        try:
            result = agent.invoke({"messages": [HumanMessage(content=user_text)]}, config=config)
        except Exception as exc:
            err = f"[agent error: {type(exc).__name__}: {exc}]"
            print(err)
            speak(err)
            continue

        reply = strip_thinking(_extract_reply(result))
        print(f"nexus> {reply}")
        _spawn_reflection(user_text, reply, result.get("messages"), route, model)

        status = speak(reply)
        if status.startswith("ERROR:"):
            print(status)


if __name__ == "__main__":
    main()

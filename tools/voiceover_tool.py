#!/usr/bin/env python3
"""Voiceover Pipeline Tool for Nexus LangGraph.

Wraps the voiceover pipeline as a LangGraph tool for agent use.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from voiceover_pipeline import run_voiceover_pipeline, VoiceoverResult, ToolStatus

@tool
def voiceover_pipeline(video_url: str, script: Optional[str] = None) -> str:
    """Generate a voiceover video from a YouTube video.

    Args:
        video_url: YouTube video URL (or path to local video file)
        script: Voiceover script text or path to script file. If None, attempts transcript.

    Returns:
        Summary string with tool status for each step and output paths.
    """
    result = run_voiceover_pipeline(video_url=video_url, script=script)

    lines = ["=== Voiceover Pipeline Result ==="]
    lines.append(f"video_path: {result.video_path or 'N/A'}")
    lines.append(f"audio_path: {result.audio_path or 'N/A'}")
    lines.append(f"srt_path:   {result.srt_path or 'N/A'}")
    lines.append(f"segments:   {len(result.segments)}")
    lines.append("")
    lines.append("Tool Status:")
    for t in result.tool_results:
        icon = "✓" if t.status.value == "working" else ("✗" if t.status.value == "blocked" else "~")
        lines.append(f"  {icon} {t.name}: {t.status.value}")
        if t.details:
            lines.append(f"     {t.details[:80]}")
        if t.error:
            lines.append(f"     ERROR: {t.error}")
    if result.errors:
        lines.append(f"\nErrors: {', '.join(result.errors)}")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: voiceover_tool.py <youtube_url> [--script <text_or_path>]")
        sys.exit(1)
    url = sys.argv[1]
    script = None
    if "--script" in sys.argv:
        idx = sys.argv.index("--script")
        if idx + 1 < len(sys.argv):
            script_arg = sys.argv[idx + 1]
            p = Path(script_arg)
            if p.exists() and p.is_file():
                script = p.read_text()
            else:
                script = script_arg

    print(voiceover_pipeline.invoke({"video_url": url, "script": script}))

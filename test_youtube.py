#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Test YouTube tool imports."""
import sys
sys.path.insert(0, "/home/cwatt250/AI_Agent")

from tools.youtube_tool import youtube_transcript, youtube_summary

print("YouTube tool loaded successfully")
print(f"youtube_transcript: {youtube_transcript.name}")
print(f"youtube_summary: {youtube_summary.name}")

#!/usr/bin/env python3
"""FFmpeg Wrapper Tool for Nexus LangGraph.

Wraps ffmpeg/ffprobe operations as LangGraph tools.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

OUTPUT_DIR = Path.home() / "AI_Agent" / "output" / "video"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_SUBDIR = OUTPUT_DIR / "audio"
AUDIO_SUBDIR.mkdir(parents=True, exist_ok=True)
FINAL_SUBDIR = OUTPUT_DIR / "final"
FINAL_SUBDIR.mkdir(parents=True, exist_ok=True)
SUBS_SUBDIR = OUTPUT_DIR / "subtitles"
SUBS_SUBDIR.mkdir(parents=True, exist_ok=True)

def _run_ffmpeg(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    cmd = ["ffmpeg", "-y"] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def _probe(path: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, timeout=30
    )
    return json.loads(r.stdout) if r.returncode == 0 else {}

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def ffmpeg_extract_audio(video_path: str, output: Optional[str] = None) -> str:
    """Extract audio from video as 16kHz mono WAV."""
    path = Path(video_path)
    if not output:
        output = str(AUDIO_SUBDIR / (path.stem + "_audio.wav"))
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    r = _run_ffmpeg(["-i", video_path, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", output])
    return f"Audio: {output}" if r.returncode == 0 else f"ERROR: {r.stderr[:200]}"

@tool
def ffmpeg_concat_clips(clip_paths: list[str], output: Optional[str] = None) -> str:
    """Concatenate audio clips end-to-end using concat demuxer."""
    if not output:
        output = str(AUDIO_SUBDIR / "concat.wav")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    list_file = AUDIO_SUBDIR / "_list.txt"
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")
    r = _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file), "-acodec", "pcm_s16le", output])
    list_file.unlink(missing_ok=True)
    return f"Concat: {output}" if r.returncode == 0 else f"ERROR: {r.stderr[:200]}"

@tool
def ffmpeg_sync_audio_to_video(audio_path: str, video_path: str, output: Optional[str] = None) -> str:
    """Replace video audio track with new audio. No video re-encode."""
    if not output:
        p = Path(video_path)
        output = str(FINAL_SUBDIR / f"{p.stem}_vo.mp4")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    r = _run_ffmpeg([
        "-i", video_path, "-i", audio_path,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0", "-shortest", output
    ])
    return f"VO video: {output}" if r.returncode == 0 else f"ERROR: {r.stderr[:200]}"

@tool
def ffmpeg_burn_subtitles(video_path: str, srt_path: str, output: Optional[str] = None) -> str:
    """Burn SRT subtitles into video."""
    if not output:
        p = Path(video_path)
        output = str(FINAL_SUBDIR / f"{p.stem}_subs.mp4")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    r = _run_ffmpeg(["-i", video_path, "-vf", f"subtitles='{srt_path}'", output])
    return f"Subbed: {output}" if r.returncode == 0 else f"ERROR: {r.stderr[:200]}"

@tool
def ffmpeg_trim_audio(audio_path: str, start: float, end: float, output: Optional[str] = None) -> str:
    """Trim audio to [start, end] seconds."""
    dur = end - start
    if not output:
        p = Path(audio_path)
        output = str(AUDIO_SUBDIR / f"trim_{p.stem}_{start:.0f}_{end:.0f}.wav")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    r = _run_ffmpeg(["-i", audio_path, "-ss", str(start), "-t", str(dur), "-acodec", "pcm_s16le", output])
    return f"Trimmed: {output}" if r.returncode == 0 else f"ERROR: {r.stderr[:200]}"

@tool
def ffmpeg_get_duration(path: str) -> float:
    """Get audio or video duration in seconds."""
    probe = _probe(path)
    for s in probe.get("streams", []):
        if s.get("codec_type") == "audio":
            return float(s.get("duration", 0))
    return float(probe.get("format", {}).get("duration", 0))

@tool
def ffmpeg_generate_srt_from_clips(clips: list[dict]) -> str:
    """Generate SRT from [{start, end, text}] clip dicts."""
    out = str(SUBS_SUBDIR / "auto.srt")
    lines = []
    for i, c in enumerate(clips):
        start = _to_srt(c["start"])
        end = _to_srt(c["end"])
        lines.append(f"{i+1}\n{start} --> {end}\n{c['text']}\n")
    Path(out).write_text("\n".join(lines))
    return out

def _to_srt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds * 1000) % 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


if __name__ == "__main__":
    print("Tools available: ffmpeg_extract_audio, ffmpeg_concat_clips, ffmpeg_sync_audio_to_video, ffmpeg_burn_subtitles, ffmpeg_trim_audio, ffmpeg_get_duration, ffmpeg_generate_srt_from_clips")

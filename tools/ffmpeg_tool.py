#!/usr/bin/env python3
"""ffmpeg tool for Nexus — video/audio operations via ffmpeg subprocess.

Wraps ffmpeg/ffprobe with centralized error handling and run logging.
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("nexus.ffmpeg")

# Paths
OUTPUT_DIR = Path.home() / "AI_Agent" / "output" / "video"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

AUDIO_SUBDIR = OUTPUT_DIR / "audio"
AUDIO_SUBDIR.mkdir(parents=True, exist_ok=True)

SUBS_SUBDIR = OUTPUT_DIR / "subtitles"
SUBS_SUBDIR.mkdir(parents=True, exist_ok=True)

FINAL_SUBDIR = OUTPUT_DIR / "final"
FINAL_SUBDIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(*args: str, capture: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run ffmpeg/ffprobe and log it."""
    start = time.time()
    cmd = list(args)
    log.info("ffmpeg run: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            timeout=timeout,
            text=not capture,
        )
        elapsed = time.time() - start
        if result.returncode != 0:
            log.error("ffmpeg exit=%d in %.1fs\nstdout: %s\nstderr: %s",
                      result.returncode, elapsed,
                      result.stdout[:500] if result.stdout else "(none)",
                      result.stderr[:1000] if result.stderr else "(none)")
        else:
            log.info("ffmpeg ok in %.1fs", elapsed)
        return result
    except subprocess.TimeoutExpired:
        log.error("ffmpeg timeout after %ds: %s", timeout, " ".join(cmd))
        raise


def _check_ffmpeg() -> bool:
    """Verify ffmpeg is installed and usable."""
    try:
        r = _run("ffmpeg", "-version")
        return r.returncode == 0
    except Exception:
        return False

def _probe(path: str) -> dict:
    """Return JSON probe dict for an audio/video file."""
    r = _run("ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path)
    import json
    return json.loads(r.stdout) if r.returncode == 0 else {}

def _find_first_audio(probe: dict) -> dict | None:
    """Pick the first audio stream from a probe dict."""
    for s in probe.get("streams", []):
        if s.get("codec_type") == "audio":
            return s
    return None

# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def extract_audio(video_path: str, output: str | None = None) -> str:
    """Extract audio from a video file as 16kHz mono WAV."""
    path = Path(video_path)
    if not output:
        output = str(AUDIO_SUBDIR / (path.stem + ".wav"))
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    _run(
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output,
    )
    return output

def generate_silence(duration_s: float, output: str | None = None) -> str:
    """Generate silent audio for padding."""
    if output is None:
        output = str(AUDIO_SUBDIR / "silence.wav")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    _run(
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
        "-t", str(duration_s),
        output,
    )
    return output

def trim_audio(audio_path: str, start: float, end: float, output: str | None = None) -> str:
    """Trim audio to [start, end] seconds."""
    dur = end - start
    if output is None:
        p = Path(audio_path)
        output = str(AUDIO_SUBDIR / (f"trim_{p.stem}_{start:.0f}_{end:.0f}.wav"))
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    _run(
        "ffmpeg", "-y",
        "-i", audio_path,
        "-ss", str(start),
        "-t", str(dur),
        "-acodec", "pcm_s16le",
        output,
    )
    return output

def concat_audio_files(files: list[str], output: str | None = None) -> str:
    """Concatenate multiple audio clips end-to-end using concat demuxer."""
    if output is None:
        output = str(AUDIO_SUBDIR / "concat.wav")
    Path(output).parent.mkdir(parents=True, exist_ok=True)

    # Write concat file list
    list_path = AUDIO_SUBDIR / "_concat_list.txt"
    with open(list_path, "w") as f:
        for fp in files:
            f.write(f"file '{fp}'\n")

    _run(
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-acodec", "pcm_s16le",
        output,
    )
    list_path.unlink(missing_ok=True)
    return output

def sync_audio_to_video(audio_path: str, video_path: str, output: str | None = None) -> str:
    """Replace video audio track with new audio. No re-encode video."""
    if output is None:
        p = Path(video_path)
        output = str(FINAL_SUBDIR / f"{p.stem}_vo.mp4")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    _run(
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        output,
    )
    return output

def add_subtitles_to_video(video_path: str, subtitle_path: str, output: str | None = None) -> str:
    """Burn subtitles into video."""
    if output is None:
        p = Path(video_path)
        output = str(FINAL_SUBDIR / f"{p.stem}_subs.mp4")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    _run(
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"subtitles={subtitle_path}",
        output,
    )
    return output

def add_subtitles_srt(video_path: str, srt_path: str, output: str | None = None) -> str:
    """Add SRT subtitles (muxed, not burned) to video."""
    if output is None:
        p = Path(video_path)
        output = str(FINAL_SUBDIR / f"{p.stem}_srt.mp4")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    _run(
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"subtitles='{srt_path}'",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        output,
    )
    return output

def generate_srt_from_segments(clips: list[dict]) -> str:
    """Generate an SRT file from a list of {start, end, text} dicts.

    clips: [{start: float, end: float, text: str}, ...]
    Returns path to the .srt file.
    """
    path = SUBS_SUBDIR / "auto.srt"
    lines = []
    for i, clip in enumerate(clips):
        start = _srt_timestamp(clip["start"])
        end = _srt_timestamp(clip["end"])
        lines.append(f"{i+1}\n{start} --> {end}\n{clip['text']}\n")
    content = "\n".join(lines)
    path.write_text(content)
    return str(path)

def _srt_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds * 1000) % 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def get_video_duration(path: str) -> float:
    """Return duration in seconds."""
    probe = _probe(path)
    fmt = probe.get("format", {})
    return float(fmt.get("duration", 0))

def get_audio_duration(path: str) -> float:
    """Return duration in seconds."""
    probe = _probe(path)
    streams = probe.get("streams", [])
    for s in streams:
        if s.get("codec_type") == "audio":
            return float(s.get("duration", 0))
    fmt = probe.get("format", {})
    return float(fmt.get("duration", 0))


# ---------------------------------------------------------------------------
# FFmpeg CLI tools (LangGraph tool-compatible signatures)
# ---------------------------------------------------------------------------

def ffmpeg_extract_audio(video_path: str, output: str | None = None) -> str:
    """Extract audio from video as 16kHz mono WAV. Args: video_path (str), output (str | None). Returns output path."""
    return extract_audio(video_path, output)

def ffmpeg_generate_silence(duration_s: float, output: str | None = None) -> str:
    """Generate silent audio. Args: duration_s (float), output (str | None). Returns path."""
    return generate_silence(duration_s, output)

def ffmpeg_trim_audio(audio_path: str, start: float, end: float, output: str | None = None) -> str:
    """Trim audio. Args: audio_path, start, end, output. Returns output path."""
    return trim_audio(audio_path, start, end, output)

def ffmpeg_concat_audio_files(files: list[str], output: str | None = None) -> str:
    """Concat audio files. Args: files (list of paths), output. Returns output path."""
    return concat_audio_files(files, output)

def ffmpeg_sync_audio_to_video(audio_path: str, video_path: str, output: str | None = None) -> str:
    """Replace video audio with new audio. Args: audio_path, video_path, output. Returns output path."""
    return sync_audio_to_video(audio_path, video_path, output)

def ffmpeg_add_subtitles_srt(video_path: str, srt_path: str, output: str | None = None) -> str:
    """Burn SRT subtitles into video. Args: video_path, srt_path, output. Returns output path."""
    return add_subtitles_srt(video_path, srt_path, output)

def ffmpeg_generate_srt(clips: list[dict]) -> str:
    """Generate SRT from clips [{start, end, text}]. Returns path."""
    return generate_srt_from_segments(clips)

def ffmpeg_get_duration(path: str) -> float:
    """Get video or audio duration in seconds."""
    probe = _probe(path)
    audio = _find_first_audio(probe)
    if audio:
        return float(audio.get("duration", 0))
    fmt = probe.get("format", {})
    return float(fmt.get("duration", 0))


# ---------------------------------------------------------------------------
# Module entry for direct use
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: ffmpeg_tool.py <command> [args...]")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "check":
        print("ffmpeg OK" if _check_ffmpeg() else "ffmpeg NOT FOUND")
    elif cmd == "probe":
        if len(sys.argv) < 3:
            print("Usage: ffmpeg_tool.py probe <path>")
            sys.exit(1)
        import json
        print(json.dumps(_probe(sys.argv[2]), indent=2))
    else:
        print(f"Unknown command: {cmd}")

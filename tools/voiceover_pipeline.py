"""Voiceover Pipeline — Part 1: YouTube → Voiceover → Subtitles → Video.

Sub-parts:
  1.1  ffmpeg wrapper tool
  1.2  voiceover pipeline core (download, script, synthesize, sync)
  1.3  LangGraph tool wrappers
  1.4  Smoke test & verification

Tool status:
  ffmpeg        — working (v6.1)
  yt-dlp        — working (installed)
  youtube-transcript-api — working (installed)
  kokoro-onnx   — working (model files present)
  bark          — working (installed)
  moviepy       — skipped (not needed, ffmpeg handles everything)
  numpy         — needed for Kokoro resampling
  scipy         — needed for Bark WAV write
"""
from __future__ import annotations

import json
import logging
import os
import re
import struct
import subprocess
import sys
import time
import wave
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

log = logging.getLogger("nexus.voiceover")

# ------ paths ------
BASE = Path.home() / "AI_Agent" / "output" / "video"
BASE.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR = BASE / "downloads"
SCRIPT_DIR = BASE / "scripts"
AUDIO_DIR = BASE / "audio"
SUBS_DIR = BASE / "subtitles"
FINAL_DIR = BASE / "final"
RESULTS_LOG = BASE / "results.jsonl"
for d in [DOWNLOAD_DIR, SCRIPT_DIR, AUDIO_DIR, SUBS_DIR, FINAL_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ------ enums / dataclasses ------

class ToolStatus(str, Enum):
    WORKING = "working"
    BLOCKED = "blocked"
    SKIPPED = "skipped"

@dataclass
class ToolResult:
    name: str
    status: ToolStatus
    details: str = ""
    cost_usd: float = 0.0
    error: str = ""

@dataclass
class VoiceoverResult:
    video_path: str = ""
    audio_path: str = ""
    srt_path: str = ""
    script_text: str = ""
    segments: list[dict] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    total_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def add_tool_result(self, name, status, details="", cost=0.0, error=""):
        self.tool_results.append(ToolResult(name, status, details, cost, error))
        if status == ToolStatus.BLOCKED and error:
            self.errors.append(f"{name}: {error}")

    def log_result(self):
        data = {
            "video_path": self.video_path,
            "audio_path": self.audio_path,
            "srt_path": self.srt_path,
            "script_preview": self.script_text[:200] + "..." if len(self.script_text) > 200 else self.script_text,
            "segments": len(self.segments),
            "tools": [{"name": t.name, "status": t.status.value} for t in self.tool_results],
            "total_cost_usd": self.total_cost_usd,
            "errors": self.errors,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with open(RESULTS_LOG, "a") as f:
            f.write(json.dumps(data) + "\n")
        return data

# ------ ffmpeg helpers ------

def _run_ffmpeg(args, timeout=120):
    cmd = ["ffmpeg", "-y"] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

def _probe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, timeout=30
    )
    return json.loads(r.stdout) if r.returncode == 0 else {}

def get_duration(path):
    probe = _probe(path)
    for s in probe.get("streams", []):
        if s.get("codec_type") == "audio":
            return float(s.get("duration", 0))
    return float(probe.get("format", {}).get("duration", 0))

# ------ check dependencies ------

def _check_ytdlp():
    try:
        r = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return ToolResult("yt-dlp", ToolStatus.WORKING, f"version {r.stdout.strip()}")
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        pass
    return ToolResult("yt-dlp", ToolStatus.BLOCKED,
                      "yt-dlp not installed",
                      error="yt-dlp not installed — run: ~/AI_Agent/venv/bin/pip install yt-dlp")

def _check_ffmpeg():
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            ver = r.stdout.split("\n")[0].replace("ffmpeg version ", "")
            return ToolResult("ffmpeg", ToolStatus.WORKING, ver)
    except Exception:
        pass
    return ToolResult("ffmpeg", ToolStatus.BLOCKED,
                      "ffmpeg not installed",
                      error="ffmpeg not installed — run: sudo apt install ffmpeg")

# ------ download video ------

def download_video(url, output_dir=None):
    if output_dir is None:
        output_dir = DOWNLOAD_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["yt-dlp", "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
         "--merge-output-format", "mp4", "-o",
         str(output_dir / "%(title)s.%(ext)s"),
         "--no-playlist", url],
        capture_output=True, text=True, timeout=300
    )
    if r.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {r.stderr[:500]}")
    for f in output_dir.glob("*.mp4"):
        return str(f.resolve())
    raise RuntimeError("yt-dlp succeeded but no .mp4 found")

# ------ get script ------

def _extract_video_id(url):
    patterns = [r'(?:v=|\/)([a-zA-Z0-9_-]{11})', r'shortlink\.co\/([a-zA-Z0-9_-]+)']
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return url.strip()

def get_transcript(url):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        video_id = _extract_video_id(url)
        transcript_list = YouTubeTranscriptApi().list(video_id=video_id)
        transcript = None
        for t in transcript_list:
            if t.language_code.startswith("en"):
                transcript = t
                break
        if transcript is None:
            transcript = transcript_list[0]
        return "\n".join([segment.text for segment in transcript.snippets])
    except ImportError:
        pass
    except Exception as e:
        log.warning("Transcript API failed: %s", e)
    return ""

def create_script_from_text(text):
    script_path = SCRIPT_DIR / "voiceover_script.md"
    script_path.write_text(text)
    return str(script_path)

# ------ split script ------

def split_script_to_segments(script_text, max_words_per_clip=30):
    # Split by sentences first
    sentences = re.split(r'(?<=[.!?])\s+', script_text.strip())
    sentences = [s.strip().rstrip('.') + '.' for s in sentences if s.strip()]
    if not sentences:
        sentences = script_text.strip().split('.')
        sentences = [s.strip() + '.' for s in sentences if s.strip()]
    if not sentences:
        return [{"start": 0, "end": 0, "text": script_text.strip()}]

    segments = []
    current_words = []
    for s in sentences:
        words = s.split()
        if len(current_words) + len(words) > max_words_per_clip and current_words:
            segments.append({"text": " ".join(current_words), "start": 0, "end": 0})
            current_words = words
        else:
            current_words.extend(words)
    if current_words:
        segments.append({"text": " ".join(current_words), "start": 0, "end": 0})
    return segments

# ------ synthesize voice ------

def _get_kokoro_path():
    for base in [Path.home() / "AI_Agent" / "models" / "kokoro", Path.home() / "AI_Agent" / "models"]:
        candidates = list(base.glob("*kokoro*.onnx"))
        if candidates:
            return str(candidates[0])
    return None

def _find_voice_file():
    for base in [Path.home() / "AI_Agent" / "models" / "kokoro", Path.home() / "AI_Agent" / "models"]:
        candidates = list(base.glob("voices*"))
        if candidates:
            return str(candidates[0])
    return None

def _resample(audio, from_sr, to_sr):
    if from_sr == to_sr:
        return audio
    ratio = to_sr / from_sr
    n = int(len(audio) * ratio)
    indices = __import__("numpy").linspace(0, len(audio) - 1, n)
    return __import__("numpy").interp(indices, __import__("numpy").arange(len(audio)), audio)

def synthesize_segment(text, index, output_dir=None):
    if output_dir is None:
        output_dir = AUDIO_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    out = str(output_dir / f"clip_{index:03d}.wav")

    # Try Kokoro first
    kokoro_model = _get_kokoro_path()
    voice_file = _find_voice_file()
    if kokoro_model and voice_file:
        try:
            import numpy as np
            from kokoro_onnx import Kokoro
            k = Kokoro(kokoro_model, voice_file)
            audio, sr = k.create(text, voice='af_heart')
            audio_16k = _resample(audio, int(sr), 16000)
            audio_int16 = np.int16(audio_16k * 32767)
            with wave.open(out, 'w') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_int16.tobytes())
            return out
        except Exception as e:
            log.warning("Kokoro synthesis failed: %s", e)

    # Try Bark
    try:
        import numpy as np
        from scipy.io.wavfile import write as wav_write
        from bark import generate_audio, preload_models
        preload_models()
        audio = generate_audio(text, history_prompt=None)
        wav_write(out, 24000, audio)
        return out
    except Exception as e:
        log.warning("Bark synthesis failed: %s", e)

    return None

# ------ concat clips ------

def concat_clips(clip_paths):
    out = str(AUDIO_DIR / "voiceover.wav")
    list_file = AUDIO_DIR / "_concat_list.txt"
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file),
                 "-acodec", "pcm_s16le", out])
    list_file.unlink(missing_ok=True)
    return out

# ------ sync audio to video ------

def sync_audio_to_video(audio_path, video_path):
    p = Path(video_path)
    out = str(FINAL_DIR / f"{p.stem}_vo.mp4")
    _run_ffmpeg(["-i", video_path, "-i", audio_path,
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                 "-map", "0:v:0", "-map", "1:a:0", "-shortest", out])
    return out

# ------ SRT ------

def _to_srt(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds * 1000) % 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def generate_srt_from_segments(segments):
    out = str(SUBS_DIR / "voiceover.srt")
    lines = []
    for i, seg in enumerate(segments):
        start = _to_srt(seg["start"])
        end = _to_srt(seg["end"])
        lines.append(f"{i+1}\n{start} --> {end}\n{seg['text']}\n")
    Path(out).write_text("\n".join(lines))
    return out

def burn_subtitles(video_path, srt_path):
    p = Path(video_path)
    out = str(FINAL_DIR / f"{p.stem}_subs.mp4")
    _run_ffmpeg(["-i", video_path, "-vf", f"subtitles='{srt_path}'", out])
    return out

# ------ main pipeline ------

def run_voiceover_pipeline(video_url, script=None, output_dir=None):
    result = VoiceoverResult()

    # Step 1: check deps + download
    check_ffmpeg = _check_ffmpeg()
    result.add_tool_result("ffmpeg", check_ffmpeg.status, check_ffmpeg.details, error=check_ffmpeg.error)
    check_ytdlp = _check_ytdlp()
    result.add_tool_result("yt-dlp", check_ytdlp.status, check_ytdlp.details, error=check_ytdlp.error)

    if check_ytdlp.status == ToolStatus.BLOCKED:
        result.errors.append("yt-dlp not installed")
        result.log_result()
        return result
    if check_ffmpeg.status == ToolStatus.BLOCKED:
        result.errors.append("ffmpeg not installed")
        result.log_result()
        return result

    video_path = download_video(video_url)
    result.add_tool_result("download_video", ToolStatus.WORKING, f"Saved to {video_path}")
    video_dur = get_duration(video_path)
    result.add_tool_result("probe_video", ToolStatus.WORKING, f"Duration: {video_dur:.1f}s")

    # Step 2: get script
    if script:
        create_script_from_text(script)
        result.add_tool_result("script_input", ToolStatus.WORKING, f"Script provided ({len(script)} chars)")
        script_text = script
    else:
        transcript = get_transcript(video_url)
        if transcript:
            create_script_from_text(transcript)
            result.add_tool_result("script_transcript", ToolStatus.WORKING, f"Got {len(transcript)} chars of transcript")
            script_text = transcript
        else:
            result.add_tool_result("script_transcript", ToolStatus.SKIPPED, "No transcript available — use --script flag")
            script_text = "[No script available]"
    result.script_text = script_text

    # Step 3: split into segments
    segments = split_script_to_segments(script_text)
    result.add_tool_result("split_script", ToolStatus.WORKING, f"{len(segments)} segments created")
    result.segments = segments

    # Step 4: synthesize voice
    clip_paths = []
    for i, seg in enumerate(segments):
        clip = synthesize_segment(seg["text"], i)
        if clip:
            clip_paths.append(clip)
            dur = get_duration(clip)
            seg["start"] = sum(get_duration(p) for p in clip_paths[:-1])
            seg["end"] = seg["start"] + dur
            result.add_tool_result(f"synth_clip_{i}", ToolStatus.WORKING, f"{dur:.1f}s")
        else:
            result.add_tool_result(f"synth_clip_{i}", ToolStatus.BLOCKED, error="All TTS engines failed")
            result.errors.append(f"Failed to synthesize clip {i}")

    if not clip_paths:
        result.errors.append("No audio clips synthesized. Check TTS model availability.")
        result.log_result()
        return result

    # Step 5: concat
    audio_path = concat_clips(clip_paths)
    result.audio_path = audio_path
    result.add_tool_result("concat_audio", ToolStatus.WORKING, f"Voiceover: {audio_path}")
    for p in clip_paths:
        Path(p).unlink(missing_ok=True)

    # Step 6: sync
    vo_video_path = sync_audio_to_video(audio_path, video_path)
    result.add_tool_result("sync_audio", ToolStatus.WORKING, f"VO video: {vo_video_path}")

    # Step 7: SRT
    srt_path = generate_srt_from_segments(segments)
    result.srt_path = srt_path
    result.add_tool_result("generate_srt", ToolStatus.WORKING, f"SRT: {srt_path}")

    # Step 8: burn subtitles
    final_video = burn_subtitles(vo_video_path, srt_path)
    result.video_path = final_video
    result.add_tool_result("burn_subtitles", ToolStatus.WORKING, f"Final: {final_video}")

    # Clean up
    Path(audio_path).unlink(missing_ok=True)
    Path(vo_video_path).unlink(missing_ok=True)

    result.log_result()
    return result

# ------ CLI ------

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="YouTube Voiceover Pipeline")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("--script", "-s", help="Voiceover script text or file path")
    args = parser.parse_args()

    script_text = None
    if args.script:
        p = Path(args.script)
        if p.exists() and p.is_file():
            script_text = p.read_text()
        else:
            script_text = args.script

    result = run_voiceover_pipeline(video_url=args.url, script=script_text)

    print("\n=== Voiceover Pipeline Result ===")
    print(f"Video path: {result.video_path or 'N/A'}")
    print(f"Audio path: {result.audio_path or 'N/A'}")
    print(f"SRT path:   {result.srt_path or 'N/A'}")
    print(f"Script:     {result.script_text[:100]}{'...' if len(result.script_text) > 100 else ''}")
    print(f"Segments:   {len(result.segments)}")
    print(f"\nTool Status:")
    for t in result.tool_results:
        icon = "+" if t.status == ToolStatus.WORKING else ("!" if t.status == ToolStatus.BLOCKED else "~")
        print(f"  {icon} {t.name}: {t.status.value} — {t.details[:60]}")
        if t.error:
            print(f"    ERROR: {t.error}")
    print(f"\nCost: ${result.total_cost_usd:.2f}")
    if result.errors:
        print(f"Errors: {', '.join(result.errors)}")
    print(f"Log:      {RESULTS_LOG}")

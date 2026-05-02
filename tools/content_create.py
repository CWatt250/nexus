"""Phase 21.4 — content_create orchestrator.

Stitches script_writer + visual_generator + tts_tool + ffmpeg into a
single end-to-end call: topic → vertical 9:16 mp4.

Pipeline per call:
  1. script_writer.script_write_core(topic, duration, tone)
     → markdown at content/scripts/YYYY-MM-DD_<slug>.md
  2. parse_scenes() pulls [VISUAL] + [VOICEOVER] blocks
  3. For each scene:
     a. tts_tool.save_audio → content/voiceovers/<slug>_scene_<N>.wav
     b. visual_generator.visual_generate → content/stills/<slug>_scene_<N>.png
     c. ffmpeg combines image + voice → content/stills/<slug>_scene_<N>.mp4
  4. ffmpeg concat demuxer mashes all scene clips into
     content/final/<slug>.mp4 at 1080x1920 / yuv420p / aac.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from tools import script_writer, visual_generator
from tools import ffmpeg_tool

ROOT = Path.home() / "AI_Agent"
CONTENT = ROOT / "content"
SCRIPTS_DIR = CONTENT / "scripts"
VOICEOVERS_DIR = CONTENT / "voiceovers"
STILLS_DIR = CONTENT / "stills"
FINAL_DIR = CONTENT / "final"

CANVAS_W = 1080
CANVAS_H = 1920

log = logging.getLogger("nexus.content_create")


def _ensure_dirs() -> None:
    for d in (SCRIPTS_DIR, VOICEOVERS_DIR, STILLS_DIR, FINAL_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _ffmpeg(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    """Run ffmpeg with -y (overwrite) prepended. Returns the
    CompletedProcess so callers can inspect rc + stderr."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _build_scene_clip(image_path: Path, audio_path: Path, output_path: Path) -> bool:
    """Combine a still image + audio file into a vertical mp4 scene
    clip. Image is scaled+padded to 1080x1920 so the canvas is uniform
    across scenes. Returns True on success."""
    vf = (
        f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=decrease,"
        f"pad={CANVAS_W}:{CANVAS_H}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1"
    )
    proc = _ffmpeg(
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-c:v", "libx264", "-tune", "stillimage",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-pix_fmt", "yuv420p",
        "-vf", vf,
        "-r", "30",
        "-shortest",
        str(output_path),
    )
    if proc.returncode != 0:
        log.warning("scene clip ffmpeg failed: %s", proc.stderr[-500:])
        return False
    return output_path.exists() and output_path.stat().st_size > 0


def _concat_scene_clips(scene_clips: list[Path], output_path: Path) -> bool:
    """Concat-demuxer all scene clips into the final mp4. All clips
    were produced with identical encoder settings so `-c copy` is safe;
    if it fails (rare) we fall back to a re-encode."""
    if not scene_clips:
        return False
    list_file = output_path.parent / f".{output_path.stem}.concat.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in scene_clips) + "\n",
        encoding="utf-8",
    )
    try:
        proc = _ffmpeg(
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(output_path),
        )
        if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return True
        log.warning("concat -c copy failed (%s) — retrying with re-encode", proc.stderr[-200:])
        # Fallback: re-encode in case timestamps drift.
        proc2 = _ffmpeg(
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(output_path),
            timeout=300,
        )
        return proc2.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0
    finally:
        try:
            list_file.unlink()
        except FileNotFoundError:
            pass


def _safe_audio_duration(path: Path) -> float:
    """ffprobe wrapper that never raises. Returns 0.0 on failure."""
    try:
        return float(ffmpeg_tool.get_audio_duration(str(path)))
    except Exception:
        return 0.0


def content_create_core(
    topic: str,
    duration: int = 30,
    tone: str = "energetic",
    prefer_real_visuals: bool = True,
) -> dict:
    """End-to-end content production. Returns a dict suitable for the
    LangGraph tool wrapper to format. Raises on unrecoverable failure
    (no scenes, no voiceovers, ffmpeg missing)."""
    _ensure_dirs()
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not on PATH")

    started = time.monotonic()

    # 1. Script
    script = script_writer.script_write_core(topic, duration_seconds=duration, tone=tone)
    scenes = script_writer.parse_scenes(script.raw_text)
    if not scenes:
        raise RuntimeError(
            f"No scenes parsed from {script.path} — model may have produced unexpected format"
        )

    slug = script.slug
    scene_clips: list[Path] = []
    fallback_count = 0
    voiceover_seconds = 0.0

    # 2-3. Per-scene assembly
    for sc in scenes:
        n = sc["scene_no"]
        wav_path = VOICEOVERS_DIR / f"{slug}_scene_{n:02d}.wav"
        png_path = STILLS_DIR / f"{slug}_scene_{n:02d}.png"
        clip_path = STILLS_DIR / f"{slug}_scene_{n:02d}.mp4"

        # Voiceover
        try:
            from tools import tts_tool  # noqa: PLC0415
            tts_tool.save_audio(sc["voiceover"], str(wav_path))
        except Exception as exc:  # noqa: BLE001
            log.warning("scene %d TTS failed: %s — skipping", n, exc)
            continue
        if not wav_path.exists() or wav_path.stat().st_size < 1024:
            log.warning("scene %d voiceover empty — skipping", n)
            continue
        voiceover_seconds += _safe_audio_duration(wav_path)

        # Visual
        v = visual_generator.visual_generate(
            sc["visual"], png_path, scene_no=n, prefer_real=prefer_real_visuals,
        )
        if v.get("was_fallback"):
            fallback_count += 1

        # Per-scene clip
        if _build_scene_clip(png_path, wav_path, clip_path):
            scene_clips.append(clip_path)
        else:
            log.warning("scene %d clip build failed — skipping", n)

    if not scene_clips:
        raise RuntimeError("All scene clip builds failed — no final video produced")

    # 4. Final concat
    final_path = FINAL_DIR / f"{slug}.mp4"
    if not _concat_scene_clips(scene_clips, final_path):
        raise RuntimeError("ffmpeg concat failed — see stderr in journal")

    duration_actual = ffmpeg_tool.get_video_duration(str(final_path))
    elapsed = time.monotonic() - started

    return {
        "final_video_path": str(final_path),
        "script_path": script.path,
        "scene_count": len(scenes),
        "scene_clips_built": len(scene_clips),
        "duration_target_seconds": float(duration),
        "duration_actual_seconds": round(duration_actual, 2),
        "voiceover_total_seconds": round(voiceover_seconds, 2),
        "visuals_fallback_count": fallback_count,
        "script_backend": script.backend,
        "cost_usd": script.cost_usd,
        "wall_seconds": round(elapsed, 2),
    }


@tool
def content_create(
    topic: str,
    duration: int = 30,
    tone: str = "energetic",
) -> str:
    """Generate an original short-form vertical video from a topic.

    SLOW tier — wall clock is typically 2-5 minutes for a 30s clip
    depending on TTS speed and whether visuals come from a real
    image API or the PIL fallback. Costs include the script-writer
    Anthropic call (free if ANTHROPIC_API_KEY is missing — falls back
    to local qwen3.6).

    Pipeline: script_writer → per-scene (TTS + image) → ffmpeg concat.
    Output: content/final/<slug>.mp4 at 1080x1920 / 30 fps / yuv420p.

    Args:
        topic: What the video is about. Be specific.
        duration: Target total duration in seconds. Default 30.
        tone: One word ("energetic", "chill", "dramatic"). Default
            "energetic".

    Returns:
        Multi-line summary with the final mp4 path, scene count,
        actual duration, fallback count, and cost.
    """
    info = content_create_core(topic, duration=duration, tone=tone)
    visuals_note = ""
    if info["visuals_fallback_count"]:
        visuals_note = (
            f"  visuals : {info['visuals_fallback_count']}/{info['scene_count']} "
            f"used PIL fallback (no real image API key)\n"
        )
    cost_str = f"${info['cost_usd']:.4f}" if info['cost_usd'] else "free (local script)"
    return (
        f"Content created: {info['final_video_path']}\n"
        f"  scenes  : {info['scene_clips_built']}/{info['scene_count']} built "
        f"(target {info['duration_target_seconds']:.0f}s, "
        f"actual {info['duration_actual_seconds']:.1f}s)\n"
        f"{visuals_note}"
        f"  script  : {info['script_path']}\n"
        f"  backend : {info['script_backend']} | cost: {cost_str}\n"
        f"  wall    : {info['wall_seconds']:.1f}s"
    )


CONTENT_CREATE_TOOLS = [content_create]

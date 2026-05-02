"""Phase 21.4 + Phase 21 Part 2 — content_create orchestrator.

Stitches script_writer + visual_generator + tts_tool + ffmpeg into a
single end-to-end call: topic → polished short-form vertical mp4.

Pipeline per call:
  1. script_writer.script_write_core(topic, duration, tone)
     → markdown at content/scripts/YYYY-MM-DD_<slug>.md
  2. parse_scenes() pulls [VISUAL] + [VOICEOVER] blocks
  3. For each scene:
     a. tts_tool.save_audio → content/voiceovers/<slug>_scene_<N>.wav
     b. visual_generator.visual_generate → content/stills/<slug>_scene_<N>.png
     c. ffmpeg builds scene clip with Ken Burns zoom + burned captions
        → content/stills/<slug>_scene_<N>.mp4
  4. Concat scenes with xfade transitions → master mp4
  5. Mix background music (ducked, faded) → content/final/<slug>.mp4
  6. Render aspect-ratio variants (1:1, 16:9) into content/final/.

Phase 21 Part 2 additions:
  - Background music selection + ffmpeg ducking/fade mix
  - Crossfade transitions between scenes (xfade + acrossfade)
  - Ken Burns zoom on stills (zoompan)
  - Burned-in captions synced to scene audio
  - Multi-aspect output (9:16 master, 1:1 square, 16:9 widescreen)
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from tools import script_writer, visual_generator
from tools import ffmpeg_tool
from tools import music_picker

ROOT = Path.home() / "AI_Agent"
CONTENT = ROOT / "content"
SCRIPTS_DIR = CONTENT / "scripts"
VOICEOVERS_DIR = CONTENT / "voiceovers"
STILLS_DIR = CONTENT / "stills"
FINAL_DIR = CONTENT / "final"

# Master canvas is 9:16 vertical. Variants reuse the same source scenes.
CANVAS_W = 1080
CANVAS_H = 1920
FPS = 30

XFADE_DURATION = 0.3   # seconds — crossfade between scenes
MUSIC_VOLUME = 0.15    # ducked under voiceover (full = 1.0)
MUSIC_FADE_IN = 1.0
MUSIC_FADE_OUT = 1.5

CAPTION_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]

log = logging.getLogger("nexus.content_create")


# ── ffmpeg helpers ─────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    for d in (SCRIPTS_DIR, VOICEOVERS_DIR, STILLS_DIR, FINAL_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _ffmpeg(*args: str, timeout: int = 240) -> subprocess.CompletedProcess:
    """Run ffmpeg with -y (overwrite) prepended."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _safe_audio_duration(path: Path) -> float:
    try:
        return float(ffmpeg_tool.get_audio_duration(str(path)))
    except Exception:
        return 0.0


def _find_caption_font() -> Optional[str]:
    for c in CAPTION_FONT_CANDIDATES:
        if Path(c).exists():
            return c
    return None


# ── Caption timing & escaping ──────────────────────────────────────────

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Split voiceover text into sentence-sized caption chunks. Falls
    back to single chunk if there are no sentence boundaries. Trims
    each chunk and drops empties."""
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text)]
    parts = [p for p in parts if p]
    return parts or [text]


def _allocate_caption_times(sentences: list[str], total_dur: float) -> list[tuple[str, float, float]]:
    """Split scene duration across sentences proportional to char count.
    Returns [(text, start, end), ...]."""
    if not sentences:
        return []
    if len(sentences) == 1:
        return [(sentences[0], 0.0, total_dur)]
    weights = [max(1, len(s)) for s in sentences]
    total_w = sum(weights)
    out: list[tuple[str, float, float]] = []
    t = 0.0
    for i, (s, w) in enumerate(zip(sentences, weights)):
        if i == len(sentences) - 1:
            end = total_dur
        else:
            end = t + total_dur * (w / total_w)
        out.append((s, t, end))
        t = end
    return out


def _escape_drawtext(text: str) -> str:
    """Escape characters that drawtext interprets specially.

    Backslash MUST be escaped first or it doubles up the others. The
    full danger list: \\ ' : % , [ ] ; { }. We escape only the ones
    drawtext actually treats as control chars; the rest pass through."""
    return (
        text.replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
            .replace("%", "\\%")
            .replace(",", "\\,")
    )


def _caption_filter(captions: list[tuple[str, float, float]],
                    font_path: Optional[str],
                    canvas_w: int = CANVAS_W,
                    canvas_h: int = CANVAS_H) -> str:
    """Build a drawtext filter chain with one entry per caption chunk,
    each gated by `enable='between(t,T0,T1)'`.

    Returns "" (empty) if no captions or no font available — caller
    should drop this filter from the chain when empty."""
    if not captions or not font_path:
        return ""
    font_path_esc = font_path.replace(":", "\\:")
    fontsize = max(40, canvas_w // 22)  # scales to canvas width
    parts = []
    for text, t0, t1 in captions:
        if not text.strip():
            continue
        safe = _escape_drawtext(text)
        parts.append(
            f"drawtext=fontfile={font_path_esc}"
            f":text='{safe}'"
            f":fontcolor=white:fontsize={fontsize}"
            f":bordercolor=black@0.85:borderw=4"
            f":x=(w-text_w)/2:y=h*0.78"
            f":line_spacing=8"
            f":enable='between(t\\,{t0:.3f}\\,{t1:.3f})'"
        )
    return ",".join(parts)


# ── Per-scene clip build ───────────────────────────────────────────────

def _build_scene_clip(
    image_path: Path,
    audio_path: Path,
    output_path: Path,
    voiceover_text: str = "",
    scene_index: int = 0,
    canvas_w: int = CANVAS_W,
    canvas_h: int = CANVAS_H,
) -> bool:
    """Combine still + voiceover into a scene clip with Ken Burns zoom
    and burned captions. Returns True on success."""
    dur = _safe_audio_duration(audio_path)
    if dur <= 0:
        log.warning("scene clip: audio duration unknown, skipping ken burns")
        dur = 3.0
    n_frames = max(1, int(dur * FPS) + FPS)  # +1s padding so zoompan never cuts short

    # Alternate zoom direction per scene to avoid monotony.
    if scene_index % 2 == 0:
        # Zoom in slowly
        z_expr = "min(zoom+0.0008,1.18)"
    else:
        # Zoom out (start zoomed, retreat)
        z_expr = "if(eq(on,0),1.18,max(zoom-0.0008,1.0))"

    # Pre-scale the still bigger so zoompan has pixels to zoom into
    # without aliasing. zoompan re-renders at canvas_w x canvas_h.
    pre_w = canvas_w * 2
    pre_h = canvas_h * 2

    base_filter = (
        f"scale={pre_w}:{pre_h}:force_original_aspect_ratio=increase,"
        f"crop={pre_w}:{pre_h},"
        f"zoompan=z='{z_expr}'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={n_frames}:s={canvas_w}x{canvas_h}:fps={FPS},"
        f"setsar=1"
    )

    # Add captions if we have text + a font
    captions = _allocate_caption_times(_split_sentences(voiceover_text), dur)
    font = _find_caption_font()
    cap_chain = _caption_filter(captions, font, canvas_w, canvas_h)
    vf = base_filter + ("," + cap_chain if cap_chain else "")

    proc = _ffmpeg(
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-pix_fmt", "yuv420p",
        "-vf", vf,
        "-r", str(FPS),
        "-shortest",
        str(output_path),
    )
    if proc.returncode != 0:
        log.warning("scene clip ffmpeg failed (last 500 of stderr): %s",
                    proc.stderr[-500:])
        return False
    return output_path.exists() and output_path.stat().st_size > 0


# ── Concat with xfade transitions ──────────────────────────────────────

def _concat_with_xfade(scene_clips: list[Path], output_path: Path,
                        xfade_dur: float = XFADE_DURATION) -> bool:
    """Concatenate scene clips with crossfade transitions between each
    pair. For N clips, produces N-1 xfades on video + acrossfades on
    audio. Falls back to plain concat if xfade fails or N < 2."""
    if not scene_clips:
        return False
    if len(scene_clips) == 1:
        # Single clip — just copy.
        proc = _ffmpeg("-i", str(scene_clips[0]), "-c", "copy", str(output_path))
        return proc.returncode == 0

    # Probe each clip's duration so we can compute xfade offsets.
    durs = [ffmpeg_tool.get_video_duration(str(c)) or 0.0 for c in scene_clips]
    if any(d <= 0 for d in durs):
        log.warning("xfade: some clip durations unknown, falling back to plain concat")
        return _concat_plain(scene_clips, output_path)

    # Build the filter_complex chain.
    inputs: list[str] = []
    for c in scene_clips:
        inputs.extend(["-i", str(c)])

    n = len(scene_clips)
    parts: list[str] = []
    last_v = "0:v"
    last_a = "0:a"
    cum = durs[0]
    for i in range(1, n):
        offset = cum - xfade_dur
        if offset < 0:
            offset = 0
        out_v = f"v{i}"
        out_a = f"a{i}"
        parts.append(
            f"[{last_v}][{i}:v]xfade=transition=fade:duration={xfade_dur}:offset={offset:.3f}[{out_v}]"
        )
        parts.append(
            f"[{last_a}][{i}:a]acrossfade=d={xfade_dur}[{out_a}]"
        )
        last_v = out_v
        last_a = out_a
        # After xfade, total length is cum + next_dur - xfade_dur.
        cum = cum + durs[i] - xfade_dur

    filter_complex = ";".join(parts)
    proc = _ffmpeg(
        *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{last_v}]",
        "-map", f"[{last_a}]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-r", str(FPS),
        str(output_path),
        timeout=420,
    )
    if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
        return True
    log.warning("xfade concat failed (%s) — falling back to plain concat",
                proc.stderr[-300:])
    return _concat_plain(scene_clips, output_path)


def _concat_plain(scene_clips: list[Path], output_path: Path) -> bool:
    """Concat-demuxer fallback — used when xfade fails or there's only
    one clip."""
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
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-r", str(FPS),
            str(output_path),
            timeout=300,
        )
        return proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0
    finally:
        try:
            list_file.unlink()
        except FileNotFoundError:
            pass


# ── Background music mix ───────────────────────────────────────────────

def _mix_music(video_in: Path, music_track: Path, video_out: Path,
                volume: float = MUSIC_VOLUME,
                fade_in: float = MUSIC_FADE_IN,
                fade_out: float = MUSIC_FADE_OUT) -> bool:
    """Add ducked background music to a video. Music is looped to cover
    the full video duration, faded in/out, and mixed at `volume` under
    the existing voiceover audio. Returns True on success."""
    dur = ffmpeg_tool.get_video_duration(str(video_in)) or 0.0
    if dur <= 0:
        log.warning("music mix: unknown video duration, skipping")
        return False

    fade_out_start = max(0.0, dur - fade_out)
    # afade requires duration; we need to volume-adjust + fade music + amix.
    # Use -stream_loop -1 to loop short tracks, then trim with -t to video duration.
    music_filter = (
        f"volume={volume},"
        f"afade=t=in:st=0:d={fade_in:.2f},"
        f"afade=t=out:st={fade_out_start:.2f}:d={fade_out:.2f}"
    )
    filter_complex = (
        f"[1:a]{music_filter}[m];"
        f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    )
    proc = _ffmpeg(
        "-i", str(video_in),
        "-stream_loop", "-1",
        "-t", f"{dur:.3f}",
        "-i", str(music_track),
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        str(video_out),
        timeout=240,
    )
    if proc.returncode != 0:
        log.warning("music mix failed: %s", proc.stderr[-400:])
        return False
    return video_out.exists() and video_out.stat().st_size > 0


# ── Aspect ratio variants ──────────────────────────────────────────────

def _render_square(master: Path, output: Path, side: int = 1080) -> bool:
    """Render 1:1 square from 9:16 master via center crop.
    The master is 1080x1920; we crop the middle 1080x1080."""
    proc = _ffmpeg(
        "-i", str(master),
        "-vf", f"crop={side}:{side}:0:(ih-{side})/2,setsar=1",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(output),
    )
    return proc.returncode == 0 and output.exists() and output.stat().st_size > 0


def _render_widescreen(master: Path, output: Path,
                        w: int = 1920, h: int = 1080) -> bool:
    """Render 16:9 widescreen with blurred-letterbox sides.

    The 9:16 master is too tall for 16:9. We pad to 16:9 by:
      - Scaling the master to fit inside w x h height-wise → narrow
      - Scaling another copy of the master to fill w x h, blurred
      - Overlaying the sharp narrow copy on the blurred fill
    Mirrors the popular 'phone vid on desktop' look."""
    filter_complex = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},boxblur=20:1[bg];"
        f"[0:v]scale=-1:{h}[fg];"
        f"[bg][fg]overlay=(W-w)/2:0,setsar=1[v]"
    )
    proc = _ffmpeg(
        "-i", str(master),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(output),
    )
    return proc.returncode == 0 and output.exists() and output.stat().st_size > 0


# ── Top-level orchestrator ─────────────────────────────────────────────

def content_create_core(
    topic: str,
    duration: int = 30,
    tone: str = "energetic",
    prefer_real_visuals: bool = True,
    add_music: bool = True,
    aspects: tuple[str, ...] = ("9x16",),
) -> dict:
    """End-to-end content production. Returns a dict suitable for the
    LangGraph tool wrapper to format. Raises on unrecoverable failure
    (no scenes, no voiceovers, ffmpeg missing).

    Args:
        topic: Subject of the video.
        duration: Target total seconds.
        tone: Style word ("energetic", "chill", "dramatic", "cinematic",
            "minimal"). Drives both script tone and music selection.
        prefer_real_visuals: Try the real image generator before PIL
            fallback.
        add_music: If True, mix background music. If False (or no track
            available), output stays voice-only.
        aspects: Aspect-ratio variants to render in addition to the
            master 9x16. Accepted values: "9x16", "1x1", "16x9". The
            master is always written; extras are written as
            <slug>_1x1.mp4 / <slug>_16x9.mp4.
    """
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

        # Visual — pass tone so PIL fallback can pick a tone-matched palette
        v = visual_generator.visual_generate(
            sc["visual"], png_path,
            scene_no=n,
            prefer_real=prefer_real_visuals,
            tone=tone,
        )
        if v.get("was_fallback"):
            fallback_count += 1

        # Per-scene clip with Ken Burns + burned caption
        if _build_scene_clip(
            png_path, wav_path, clip_path,
            voiceover_text=sc["voiceover"],
            scene_index=n - 1,
        ):
            scene_clips.append(clip_path)
        else:
            log.warning("scene %d clip build failed — skipping", n)

    if not scene_clips:
        raise RuntimeError("All scene clip builds failed — no final video produced")

    # 4. Concat with xfade transitions → master (no music yet)
    master_no_music = STILLS_DIR / f"_{slug}_master_nomusic.mp4"
    if not _concat_with_xfade(scene_clips, master_no_music):
        raise RuntimeError("ffmpeg concat failed — see stderr in journal")

    # 5. Mix in background music (cleanly skip if unavailable)
    final_master = FINAL_DIR / f"{slug}.mp4"
    music_path: Optional[Path] = None
    music_used = False
    if add_music:
        music_path = music_picker.select_music(tone, duration_seconds=duration, seed=slug)
    if music_path:
        if _mix_music(master_no_music, music_path, final_master):
            music_used = True
        else:
            log.warning("music mix failed — copying voice-only master as final")
            shutil.copy2(master_no_music, final_master)
    else:
        # No music available — final = master
        shutil.copy2(master_no_music, final_master)

    # Clean up the intermediate.
    try:
        master_no_music.unlink()
    except FileNotFoundError:
        pass

    # 6. Aspect-ratio variants
    variants_built: dict[str, str] = {"9x16": str(final_master)}
    for ar in aspects:
        ar = ar.lower().replace(":", "x")
        if ar == "9x16":
            continue  # already the master
        if ar == "1x1":
            out = FINAL_DIR / f"{slug}_1x1.mp4"
            if _render_square(final_master, out):
                variants_built["1x1"] = str(out)
        elif ar == "16x9":
            out = FINAL_DIR / f"{slug}_16x9.mp4"
            if _render_widescreen(final_master, out):
                variants_built["16x9"] = str(out)
        else:
            log.warning("unknown aspect %r — skipping", ar)

    duration_actual = ffmpeg_tool.get_video_duration(str(final_master))
    elapsed = time.monotonic() - started

    return {
        "final_video_path": str(final_master),
        "aspect_variants": variants_built,
        "script_path": script.path,
        "scene_count": len(scenes),
        "scene_clips_built": len(scene_clips),
        "duration_target_seconds": float(duration),
        "duration_actual_seconds": round(duration_actual, 2),
        "voiceover_total_seconds": round(voiceover_seconds, 2),
        "visuals_fallback_count": fallback_count,
        "music_used": music_used,
        "music_track": str(music_path) if music_path else None,
        "script_backend": script.backend,
        "cost_usd": script.cost_usd,
        "wall_seconds": round(elapsed, 2),
    }


@tool
def content_create(
    topic: str,
    duration: int = 30,
    tone: str = "energetic",
    aspects: str = "9x16",
) -> str:
    """Generate an original short-form vertical video from a topic.

    SLOW tier — wall clock is typically 3-7 minutes for a 30s clip
    depending on TTS speed and aspect-variant rendering. Output goes to
    content/final/<slug>.mp4 at 1080x1920 / 30 fps. Background music,
    Ken Burns zoom, scene crossfades, and burned captions are applied
    automatically.

    Args:
        topic: What the video is about. Be specific.
        duration: Target total duration in seconds. Default 30.
        tone: One word ("energetic", "chill", "dramatic", "cinematic",
            "minimal"). Drives both script style and background music.
        aspects: Comma-separated aspect ratios to render. Master is
            always 9x16; add "1x1" or "16x9" for additional cuts.
            Example: "9x16,1x1,16x9".

    Returns:
        Multi-line summary with paths, scene count, fallback counts, and
        whether background music was applied.
    """
    aspect_tuple = tuple(a.strip() for a in aspects.split(",") if a.strip())
    info = content_create_core(
        topic, duration=duration, tone=tone, aspects=aspect_tuple,
    )
    visuals_note = ""
    if info["visuals_fallback_count"]:
        visuals_note = (
            f"  visuals : {info['visuals_fallback_count']}/{info['scene_count']} "
            f"used PIL fallback (no real image API key)\n"
        )
    music_line = (
        f"  music   : {Path(info['music_track']).name}\n"
        if info["music_used"] else "  music   : (none — pipeline ran voice-only)\n"
    )
    variants = info["aspect_variants"]
    if len(variants) > 1:
        v_line = "  aspects : " + ", ".join(f"{k}→{Path(v).name}" for k, v in variants.items()) + "\n"
    else:
        v_line = ""
    cost_str = f"${info['cost_usd']:.4f}" if info['cost_usd'] else "free (local script)"
    return (
        f"Content created: {info['final_video_path']}\n"
        f"  scenes  : {info['scene_clips_built']}/{info['scene_count']} built "
        f"(target {info['duration_target_seconds']:.0f}s, "
        f"actual {info['duration_actual_seconds']:.1f}s)\n"
        f"{visuals_note}"
        f"{music_line}"
        f"{v_line}"
        f"  script  : {info['script_path']}\n"
        f"  backend : {info['script_backend']} | cost: {cost_str}\n"
        f"  wall    : {info['wall_seconds']:.1f}s"
    )


CONTENT_CREATE_TOOLS = [content_create]

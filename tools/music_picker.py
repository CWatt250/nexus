"""Phase 21.2 Part 2.1 — background music selector for content_create.

Maps a tone keyword to a track in content/music/<bucket>/. If the
requested duration exceeds track length, callers can loop in ffmpeg
(`-stream_loop -1`) — this picker doesn't try to splice tracks.

Adding more tracks: drop additional .mp3/.wav files in any bucket
folder. select_music() picks one at pseudo-random per call (stable for a
given seed) so multiple videos don't all use the same first file.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

ROOT = Path.home() / "AI_Agent"
MUSIC_DIR = ROOT / "content" / "music"

# Tone → bucket. Aliases collapse synonyms onto the same folder.
TONE_TO_BUCKET = {
    "energetic": "energetic",
    "energy": "energetic",
    "upbeat": "energetic",
    "hype": "energetic",
    "punchy": "energetic",
    "chill": "chill",
    "relaxed": "chill",
    "calm": "chill",
    "ambient": "chill",
    "dramatic": "dramatic",
    "intense": "dramatic",
    "serious": "dramatic",
    "cinematic": "cinematic",
    "epic": "cinematic",
    "grand": "cinematic",
    "minimal": "minimal",
    "simple": "minimal",
    "sparse": "minimal",
}

DEFAULT_BUCKET = "energetic"

log = logging.getLogger("nexus.music_picker")


def _list_tracks(bucket: str) -> list[Path]:
    folder = MUSIC_DIR / bucket
    if not folder.is_dir():
        return []
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".ogg", ".m4a"}
        and not p.name.startswith(".")
    )


def select_music(tone: str, duration_seconds: float = 30.0,
                 seed: Optional[str] = None) -> Optional[Path]:
    """Pick a music track matching `tone`. Returns None if no usable
    track found — callers should treat that as "skip music cleanly."

    Args:
        tone: Tone keyword. Maps to a bucket via TONE_TO_BUCKET; unknown
            tones fall back to `energetic`.
        duration_seconds: Hint for how long the video is. Currently
            unused for filtering (all our tracks are 60s and we expect
            callers to loop with ffmpeg) — kept in the signature for
            future "track must be ≥ duration" filtering.
        seed: Optional stable selection seed (e.g. the script slug). If
            given, the same tone+seed always returns the same file —
            useful for deterministic re-runs.

    Returns:
        Path to a track file, or None if the bucket is empty.
    """
    bucket = TONE_TO_BUCKET.get(tone.strip().lower(), DEFAULT_BUCKET)
    tracks = _list_tracks(bucket)
    if not tracks and bucket != DEFAULT_BUCKET:
        # Fall back to default bucket if requested one is empty.
        log.info("music bucket %s empty, falling back to %s", bucket, DEFAULT_BUCKET)
        bucket = DEFAULT_BUCKET
        tracks = _list_tracks(bucket)
    if not tracks:
        log.warning("no music tracks available — pipeline will skip music")
        return None

    if seed:
        h = int(hashlib.sha1(seed.encode("utf-8", errors="replace")).hexdigest(), 16)
        return tracks[h % len(tracks)]
    return tracks[0]


def list_buckets() -> dict[str, list[str]]:
    """Returns {bucket_name: [track_filenames]} for all populated
    buckets. Useful for sanity-checking the library."""
    out: dict[str, list[str]] = {}
    if not MUSIC_DIR.is_dir():
        return out
    for folder in sorted(MUSIC_DIR.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        names = [p.name for p in _list_tracks(folder.name)]
        if names:
            out[folder.name] = names
    return out

"""Text-to-speech for Nexus.

Two engines are supported:
  * Kokoro-82M (kokoro-onnx) — fast local CPU synth, default.
  * Microsoft Edge neural voices (edge-tts) — fallback if Kokoro can't
    produce the requested voice, or when SPARKY_VOICE points at a
    "en-US-*Neural" style id.

Voice selection
---------------
The default voice is read from the `SPARKY_VOICE` env var (falling back
to `~/AI_Agent/.env`). If unset, the module tries a preferred list of
Kokoro voices in order — `af_sky`, `af_nova`, `bf_emma`, `af_heart` —
and caches whichever synthesizes successfully first.

Public functions:
  * `speak(text, voice=None)` — synthesize and play through the default
    audio device.
  * `save_audio(text, path, voice=None)` — synthesize and write to WAV."""
from __future__ import annotations

import logging
import os
import sys
import urllib.request
from pathlib import Path

from langchain_core.tools import tool

MODEL_DIR = Path.home() / "AI_Agent" / "models" / "kokoro"
MODEL_PATH = MODEL_DIR / "kokoro-v1.0.onnx"
VOICES_PATH = MODEL_DIR / "voices-v1.0.bin"
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
ENV_FILE = Path.home() / "AI_Agent" / ".env"

# Kokoro voice preference order when SPARKY_VOICE is not set.
KOKORO_PREFS = ("af_sky", "af_nova", "bf_emma", "af_heart")

# If the selected voice looks like an Edge Neural voice, route via edge-tts.
EDGE_NEURAL_MARKER = "neural"
EDGE_FALLBACK_VOICE = "en-US-JennyNeural"

log = logging.getLogger("nexus.tts")

_engine = None
_resolved_voice: str | None = None


# ---------------------------------------------------------------------------
# Env / voice selection
# ---------------------------------------------------------------------------

def _load_env_file() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_FILE.exists():
        return out
    try:
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            out[k.strip()] = v
    except OSError:
        pass
    return out


def _configured_voice() -> str | None:
    v = os.environ.get("SPARKY_VOICE")
    if v:
        return v.strip()
    return _load_env_file().get("SPARKY_VOICE") or None


def _is_edge_voice(name: str) -> bool:
    return EDGE_NEURAL_MARKER in (name or "").lower()


# ---------------------------------------------------------------------------
# Kokoro engine
# ---------------------------------------------------------------------------

def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    log.info("downloading %s → %s", url, dest)
    with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(dest)


def _ensure_files() -> None:
    if not MODEL_PATH.exists():
        _download(MODEL_URL, MODEL_PATH)
    if not VOICES_PATH.exists():
        _download(VOICES_URL, VOICES_PATH)


def _get_engine():
    global _engine
    if _engine is not None:
        return _engine
    _ensure_files()
    from kokoro_onnx import Kokoro
    _engine = Kokoro(str(MODEL_PATH), str(VOICES_PATH))
    return _engine


def _synth_kokoro(text: str, voice: str, speed: float):
    eng = _get_engine()
    return eng.create(text=text, voice=voice, speed=speed, lang="en-us")


# ---------------------------------------------------------------------------
# Edge-TTS fallback
# ---------------------------------------------------------------------------

def _synth_edge(text: str, voice: str):
    """Synthesize via Microsoft Edge neural TTS. Returns (audio float32,
    sample_rate). Decodes the MP3 stream with PyAV (already installed)."""
    import asyncio
    import tempfile

    try:
        import edge_tts  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "edge-tts not installed — run: ~/AI_Agent/venv/bin/pip install edge-tts"
        ) from exc

    tmp = Path(tempfile.mkstemp(prefix="sparky-edge-", suffix=".mp3")[1])
    try:
        async def _run():
            c = edge_tts.Communicate(text, voice)
            await c.save(str(tmp))
        asyncio.run(_run())
        return _decode_mp3(tmp)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _decode_mp3(path: Path):
    """MP3 → (float32 np.ndarray, sample_rate) via PyAV."""
    import numpy as np
    import av

    container = av.open(str(path))
    stream = container.streams.audio[0]
    sr = stream.rate or 24000
    resampler = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=sr)
    chunks = []
    for frame in container.decode(stream):
        resampled = resampler.resample(frame)
        if resampled is None:
            continue
        for rs in (resampled if isinstance(resampled, list) else [resampled]):
            arr = rs.to_ndarray().flatten().astype(np.float32, copy=False)
            chunks.append(arr)
    container.close()
    if not chunks:
        return np.zeros(0, dtype=np.float32), sr
    return np.concatenate(chunks, axis=0), sr


# ---------------------------------------------------------------------------
# Voice resolution
# ---------------------------------------------------------------------------

def _try_kokoro(voice: str) -> bool:
    """Return True if Kokoro can synth a short sample in `voice`."""
    try:
        audio, _ = _synth_kokoro("hi", voice, 1.0)
        return audio is not None and getattr(audio, "size", 1) > 0
    except Exception as exc:
        log.info("kokoro voice %s unavailable: %s", voice, exc)
        return False


def resolved_voice() -> str:
    """Pick (and cache) a voice for this process.

    Order:
      1. SPARKY_VOICE env / .env — used verbatim.
      2. Kokoro preferred list (af_sky → af_nova → bf_emma → af_heart).
      3. Edge neural fallback (en-US-JennyNeural)."""
    global _resolved_voice
    if _resolved_voice:
        return _resolved_voice
    configured = _configured_voice()
    if configured:
        _resolved_voice = configured
        log.info("SPARKY_VOICE=%s — using configured voice", configured)
        return _resolved_voice
    for voice in KOKORO_PREFS:
        if _try_kokoro(voice):
            _resolved_voice = voice
            log.info("resolved Kokoro voice: %s", voice)
            return _resolved_voice
    _resolved_voice = EDGE_FALLBACK_VOICE
    log.warning("no Kokoro voice worked; falling back to edge-tts %s", _resolved_voice)
    return _resolved_voice


# ---------------------------------------------------------------------------
# Public synth / speak / save
# ---------------------------------------------------------------------------

def synthesize(text: str, *, voice: str | None = None, speed: float = 1.0):
    """Return (audio float32, sample_rate) for `text`."""
    v = voice or resolved_voice()
    if _is_edge_voice(v):
        return _synth_edge(text, v)
    try:
        return _synth_kokoro(text, v, speed)
    except Exception as exc:
        # If the configured Kokoro voice was stale, reprobe once.
        log.warning("kokoro synth failed for %s: %s — reprobing", v, exc)
        global _resolved_voice
        _resolved_voice = None
        v2 = resolved_voice()
        if _is_edge_voice(v2):
            return _synth_edge(text, v2)
        return _synth_kokoro(text, v2, speed)


def speak(text: str, *, voice: str | None = None, speed: float = 1.0) -> str:
    """Synthesize + play through the default audio device."""
    if not text or not text.strip():
        return "(nothing to speak)"
    try:
        audio, sr = synthesize(text, voice=voice, speed=speed)
    except Exception as exc:
        return f"ERROR: TTS synth failed — {type(exc).__name__}: {exc}"
    try:
        import sounddevice as sd
    except OSError as exc:
        return (
            f"ERROR: audio backend missing ({exc}). "
            "Install with: sudo apt install -y libportaudio2"
        )
    try:
        sd.play(audio, sr)
        sd.wait()
    except Exception as exc:
        return f"ERROR: playback failed — {type(exc).__name__}: {exc}"
    return f"spoke {len(text)} chars as {voice or resolved_voice()} @ {sr}Hz"


def save_audio(text: str, path: str, *, voice: str | None = None, speed: float = 1.0) -> str:
    """Synthesize `text` and write to `path` as a WAV file."""
    if not text or not text.strip():
        return "(nothing to save)"
    try:
        audio, sr = synthesize(text, voice=voice, speed=speed)
    except Exception as exc:
        return f"ERROR: TTS synth failed — {type(exc).__name__}: {exc}"
    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        import soundfile as sf
        sf.write(str(out), audio, sr)
    except Exception as exc:
        return f"ERROR: write failed — {type(exc).__name__}: {exc}"
    return str(out)


# ---------------------------------------------------------------------------
# LangGraph tools
# ---------------------------------------------------------------------------

@tool
def tts_speak(text: str, voice: str | None = None) -> str:
    """Speak `text` out loud through the default audio device. Voice
    defaults to SPARKY_VOICE (or the best available Kokoro voice)."""
    return speak(text, voice=voice)


@tool
def tts_save(text: str, path: str, voice: str | None = None) -> str:
    """Synthesize `text` to a WAV file at `path`. Voice defaults to
    SPARKY_VOICE (or the best available Kokoro voice)."""
    return save_audio(text, path, voice=voice)


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) or "Hello, this is Nexus speaking."
    print("Resolved voice:", resolved_voice())
    print(speak(msg))

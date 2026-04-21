"""Kokoro-82M text-to-speech for Nexus.

Uses the kokoro-onnx runtime (CPU-friendly, ~82M parameters). Model and
voice files are cached under ~/AI_Agent/models/kokoro/ and downloaded on
first use.

Public functions:
  - `speak(text, voice="af_heart")` — synthesize and play through speakers.
  - `save_audio(text, path, voice="af_heart")` — synthesize and write to WAV.

Both are wrapped as LangGraph tools (`tts_speak`, `tts_save`)."""
from __future__ import annotations

import logging
import sys
import urllib.request
from pathlib import Path

from langchain_core.tools import tool

MODEL_DIR = Path.home() / "AI_Agent" / "models" / "kokoro"
MODEL_PATH = MODEL_DIR / "kokoro-v1.0.onnx"
VOICES_PATH = MODEL_DIR / "voices-v1.0.bin"
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

DEFAULT_VOICE = "af_heart"

log = logging.getLogger("nexus.tts")

_engine = None


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


def synthesize(text: str, *, voice: str = DEFAULT_VOICE, speed: float = 1.0):
    """Return (audio: np.ndarray float32, sample_rate: int)."""
    eng = _get_engine()
    return eng.create(text=text, voice=voice, speed=speed, lang="en-us")


def speak(text: str, *, voice: str = DEFAULT_VOICE, speed: float = 1.0) -> str:
    """Synthesize `text` and play it through the default audio device.
    Returns a short status string."""
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
    return f"spoke {len(text)} chars as {voice} @ {sr}Hz"


def save_audio(text: str, path: str, *, voice: str = DEFAULT_VOICE, speed: float = 1.0) -> str:
    """Synthesize `text` and write to `path` as a WAV file. Returns the
    absolute path of the file written."""
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
def tts_speak(text: str, voice: str = DEFAULT_VOICE) -> str:
    """Speak `text` out loud through the default audio device using
    Kokoro-82M. Default voice is `af_heart` (warm female)."""
    return speak(text, voice=voice)


@tool
def tts_save(text: str, path: str, voice: str = DEFAULT_VOICE) -> str:
    """Synthesize `text` to a WAV file at `path` using Kokoro-82M.
    Default voice is `af_heart`."""
    return save_audio(text, path, voice=voice)


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) or "Hello, this is Nexus speaking."
    print(speak(msg))

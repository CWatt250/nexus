"""Whisper speech-to-text for Nexus.

Uses faster-whisper with the `base` model. Model files are cached under
~/AI_Agent/models/whisper/ so we aren't re-downloading the CT2 weights on
every cold start.

Two entry points:
  - `record_and_transcribe(...)` — blocks up to `max_seconds`, cuts off on
    sustained silence, and returns the transcribed text.
  - `transcribe_file(path)` — runs whisper on an existing audio file.

Both are wrapped as LangGraph tools (`whisper_record`, `whisper_transcribe`)
at the bottom of the file."""
from __future__ import annotations

import logging
import sys
import wave
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

MODEL_NAME = "base"
MODEL_DIR = Path.home() / "AI_Agent" / "models" / "whisper"
SAMPLE_RATE = 16_000
SILENCE_THRESHOLD = 0.012        # normalized RMS; tune if the mic is noisy
SILENCE_HANG_MS = 1_500          # end recording after this much continuous silence
MIN_RECORD_MS = 800              # require at least this much audio before cutting

log = logging.getLogger("nexus.whisper")

_model = None


def _get_model():
    """Lazy-load faster-whisper so importing this file is free."""
    global _model
    if _model is not None:
        return _model
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    from faster_whisper import WhisperModel
    # int8 on CPU — small footprint, fast enough for interactive use.
    _model = WhisperModel(
        MODEL_NAME,
        device="cpu",
        compute_type="int8",
        download_root=str(MODEL_DIR),
    )
    return _model


def _rms(block) -> float:
    import numpy as np
    if block.size == 0:
        return 0.0
    arr = block.astype("float32")
    return float(np.sqrt(np.mean(arr * arr)))


def record_and_transcribe(
    max_seconds: int = 30,
    *,
    silence_hang_ms: int = SILENCE_HANG_MS,
    sample_rate: int = SAMPLE_RATE,
) -> str:
    """Record from the default microphone, stop on silence (or after
    `max_seconds`), transcribe with whisper, return the text."""
    try:
        import numpy as np
        import sounddevice as sd
    except OSError as exc:
        return (
            f"ERROR: audio backend missing ({exc}). "
            "Install with: sudo apt install -y libportaudio2"
        )
    except ImportError as exc:
        return f"ERROR: missing Python dep — {exc}"

    block_ms = 100
    block_frames = int(sample_rate * block_ms / 1000)
    max_blocks = int((max_seconds * 1000) / block_ms)
    min_blocks = int(MIN_RECORD_MS / block_ms)
    hang_blocks = max(1, int(silence_hang_ms / block_ms))

    recorded: list = []
    silence_run = 0
    started_voice = False

    try:
        with sd.InputStream(
            samplerate=sample_rate, channels=1, dtype="float32", blocksize=block_frames
        ) as stream:
            for i in range(max_blocks):
                block, _ = stream.read(block_frames)
                recorded.append(block.copy())
                level = _rms(block[:, 0])
                if level > SILENCE_THRESHOLD:
                    started_voice = True
                    silence_run = 0
                else:
                    silence_run += 1
                if started_voice and i >= min_blocks and silence_run >= hang_blocks:
                    break
    except Exception as exc:
        return f"ERROR: recording failed — {type(exc).__name__}: {exc}"

    if not recorded:
        return ""

    import numpy as np
    audio = np.concatenate(recorded, axis=0).flatten()

    try:
        model = _get_model()
        segments, _info = model.transcribe(audio, language="en", beam_size=1)
        text = "".join(seg.text for seg in segments).strip()
        return text
    except Exception as exc:
        return f"ERROR: transcription failed — {type(exc).__name__}: {exc}"


def transcribe_file(path: str) -> str:
    """Transcribe an existing audio file (wav, mp3, m4a, …)."""
    p = Path(path).expanduser()
    if not p.exists():
        return f"ERROR: no such file: {p}"
    try:
        model = _get_model()
        segments, _info = model.transcribe(str(p), language="en", beam_size=1)
        return "".join(seg.text for seg in segments).strip()
    except Exception as exc:
        return f"ERROR: transcription failed — {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# LangGraph tools
# ---------------------------------------------------------------------------

@tool
def whisper_record(max_seconds: int = 30) -> str:
    """Record audio from the default microphone and transcribe it with
    faster-whisper. Cuts off on ~1.5s of silence after speech starts, or
    when `max_seconds` elapses.

    Returns the transcribed text, or a message starting with 'ERROR:' if
    recording/transcription failed."""
    return record_and_transcribe(max_seconds=max_seconds)


@tool
def whisper_transcribe(path: str) -> str:
    """Transcribe an existing audio file (wav / mp3 / m4a / flac / ogg)
    using faster-whisper. Returns the transcribed text."""
    return transcribe_file(path)


if __name__ == "__main__":
    # Quick CLI: `python3 whisper_tool.py` to record, `... <path>` to transcribe.
    if len(sys.argv) > 1:
        print(transcribe_file(sys.argv[1]))
    else:
        print("Recording (speak now)…")
        print(record_and_transcribe())

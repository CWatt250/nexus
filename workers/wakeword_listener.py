#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Wake-word listener (Phase 16.6).

Listens to the default mic via `openwakeword` for the 'hey nexus' / 'hey
sparky' wake words. On a detection, kicks off a Whisper recording and
hands the transcribed text to the conversation handler / task queue.

`openwakeword` is an optional dependency (~50 MB of TFLite models). If
it isn't installed the listener exits cleanly with an actionable hint
— the systemd service is configured RestartSec=300 so we don't busy-
loop crashloop while the dependency is missing.

Bundled openwakeword models include 'hey_jarvis' which we use as a
placeholder for 'hey nexus' until Colton trains a custom model. The
hand-off path is the same.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import task_queue  # noqa: E402

log = logging.getLogger("nexus.wakeword")

WAKE_MODELS = ("hey_jarvis_v0.1", "alexa_v0.1")  # placeholder; swap to custom 'hey_nexus' when trained.
SAMPLE_RATE = 16000
CHUNK = 1280  # 80ms @16kHz — what openwakeword expects


def _import_or_hint():
    try:
        import openwakeword  # noqa: F401
        from openwakeword.model import Model  # noqa: F401
        import sounddevice  # noqa: F401
        import numpy  # noqa: F401
        return None
    except Exception as exc:
        return (
            f"openwakeword not available ({type(exc).__name__}: {exc}). "
            f"Install with: ~/AI_Agent/venv/bin/pip install openwakeword sounddevice "
            f"numpy && python3 -c 'import openwakeword; openwakeword.utils.download_models()'"
        )


def _on_wake(detection_label: str) -> None:
    """Capture a Whisper transcription and route it through the handler."""
    log.info("wake detected (%s) — starting Whisper recording", detection_label)
    try:
        from tools.whisper_tool import whisper_record
        text = whisper_record.invoke({"max_seconds": 12})
    except Exception as exc:
        log.exception("whisper failed: %s", exc)
        return
    if not text or text.strip() in ("", "[no speech]"):
        log.info("no speech captured — back to listening")
        return
    log.info("transcribed: %s", text[:160])
    # Route via the fast handler first so commands answer instantly.
    try:
        from workers.conversation_handler import fast_handle
        fast = fast_handle(text, allow_llm_chat=False)
        if fast is not None:
            log.info("handler reply: %s", fast[:200])
            return
    except Exception:
        pass
    # Otherwise enqueue as a heavy task.
    tid = task_queue.enqueue(text)
    log.info("enqueued heavy task %s for the task_worker", tid)


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    hint = _import_or_hint()
    if hint:
        log.error(hint)
        # Sleep a long while so systemd's RestartSec=300 doesn't tight-loop.
        time.sleep(600)
        return 1

    import numpy as np
    import sounddevice as sd
    from openwakeword.model import Model

    log.info("loading wake models: %s", WAKE_MODELS)
    try:
        model = Model(wakeword_models=list(WAKE_MODELS))
    except Exception as exc:
        log.exception("failed to load wakeword models: %s", exc)
        time.sleep(600)
        return 2

    log.info("listening for wake words (press Ctrl+C to stop)")
    threshold = 0.5
    cooldown_until = 0.0
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                        blocksize=CHUNK) as stream:
        while True:
            audio_chunk, _ = stream.read(CHUNK)
            audio_arr = np.array(audio_chunk).flatten().astype(np.int16)
            preds = model.predict(audio_arr)
            now = time.monotonic()
            if now < cooldown_until:
                continue
            for label, score in preds.items():
                if score >= threshold:
                    _on_wake(label)
                    cooldown_until = time.monotonic() + 5.0
                    break


if __name__ == "__main__":
    sys.exit(main())

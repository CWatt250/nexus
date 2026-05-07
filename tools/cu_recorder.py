"""Phase 36 — FFmpeg session recorder for the Computer Use agent.

Captures the full :99 X display to MP4 so a finished session can be
replayed and audited. The recording starts when the agent loop begins
and stops when it exits (clean or crashed). Failure to record never
blocks the agent — it just logs a warning.
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger("nexus.cu_recorder")

DISPLAY = ":99"
WINDOW_W, WINDOW_H = 1920, 1080


class Recorder:
    """Context-manager wrapper around `ffmpeg x11grab`.

    Usage:
        with Recorder(out_path) as rec:
            ... run agent ...
        # ffmpeg gets SIGINT on exit, finalizes the MP4
    """

    def __init__(self, out_path: Path, framerate: int = 4) -> None:
        self.out_path = Path(out_path)
        self.framerate = framerate
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> bool:
        if not shutil.which("ffmpeg"):
            log.warning("ffmpeg not installed — skipping session recording")
            return False
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "x11grab",
            "-framerate", str(self.framerate),
            "-video_size", f"{WINDOW_W}x{WINDOW_H}",
            "-i", DISPLAY,
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            str(self.out_path),
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "DISPLAY": DISPLAY},
        )
        log.info("recording → %s (pid=%s)", self.out_path, self._proc.pid)
        return True

    def stop(self) -> None:
        if not self._proc:
            return
        try:
            # `q` on stdin is the polite way to ask ffmpeg to finalize.
            if self._proc.stdin:
                try:
                    self._proc.stdin.write(b"q")
                    self._proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("ffmpeg didn't exit in 5s — sending SIGINT")
            self._proc.send_signal(signal.SIGINT)
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        finally:
            self._proc = None

    def __enter__(self) -> "Recorder":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

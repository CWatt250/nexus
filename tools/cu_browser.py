"""Phase 36 — browser launcher for the Computer Use agent.

Launches Chromium (preferred) or Firefox (fallback) on the headless Xvfb
display at :99 with a persistent profile so logged-in sessions survive
restarts. Idempotent — if a browser is already attached to :99 with the
expected profile, reuse it instead of starting a duplicate.

The persistent profile lives at ~/AI_Agent/cu_profile/ (gitignored). The
operator does the manual login the first time per service; subsequent
agent runs reuse the cookie jar.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("nexus.cu_browser")

PROFILE_ROOT = Path.home() / "AI_Agent" / "cu_profile"
PROFILE_ROOT.mkdir(parents=True, exist_ok=True)

DISPLAY = ":99"
WINDOW_W, WINDOW_H = 1920, 1080


def _which(*candidates: str) -> Optional[str]:
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None


def detect_browser() -> tuple[str, str]:
    """Return (binary_path, family) where family is 'chromium' or 'firefox'.

    Raises RuntimeError if neither is installed. Caller can wrap in a
    try/except and surface the apt-install hint to the user."""
    chromium = _which("chromium", "chromium-browser", "google-chrome", "chrome")
    if chromium:
        return chromium, "chromium"
    firefox = _which("firefox", "firefox-esr")
    if firefox:
        return firefox, "firefox"
    raise RuntimeError(
        "No browser found. Install one: "
        "`sudo apt install -y chromium-browser` (preferred) or `firefox`."
    )


def _is_running_on_display() -> bool:
    """Probe :99 for any window owned by chromium or firefox."""
    try:
        out = subprocess.run(
            ["xdotool", "search", "--name", "."],
            env={**os.environ, "DISPLAY": DISPLAY},
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode != 0:
            return False
        # Cheap check — if anything has a window, assume ours is up.
        # Tighter check would need wm-class lookup; not worth the complexity.
        return bool(out.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def launch(start_url: str = "about:blank") -> dict:
    """Launch the browser on :99 with the persistent profile.

    Returns dict: {pid, family, profile_path, reused}. `reused=True` if
    we found an existing instance and skipped a fresh launch."""
    if _is_running_on_display():
        log.info("browser already on %s — reusing", DISPLAY)
        return {"pid": None, "family": "unknown", "profile_path": str(PROFILE_ROOT), "reused": True}

    binary, family = detect_browser()
    env = {**os.environ, "DISPLAY": DISPLAY}

    if family == "chromium":
        profile_dir = PROFILE_ROOT / "chromium"
        profile_dir.mkdir(exist_ok=True)
        cmd = [
            binary,
            f"--user-data-dir={profile_dir}",
            f"--window-size={WINDOW_W},{WINDOW_H}",
            "--window-position=0,0",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=TranslateUI",
            "--password-store=basic",
            start_url,
        ]
    else:  # firefox
        profile_dir = PROFILE_ROOT / "firefox"
        profile_dir.mkdir(exist_ok=True)
        cmd = [
            binary,
            "--profile", str(profile_dir),
            "--width", str(WINDOW_W),
            "--height", str(WINDOW_H),
            "--no-remote",
            start_url,
        ]

    log.info("launching %s on %s with profile %s", family, DISPLAY, profile_dir)
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Give it a moment to map a window before the agent screenshots.
    time.sleep(3)
    return {
        "pid": proc.pid,
        "family": family,
        "profile_path": str(profile_dir),
        "reused": False,
    }

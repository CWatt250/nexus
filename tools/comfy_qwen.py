"""Qwen-Image-2.1 via ComfyUI — the quality tier for image generation.

sd.cpp (`image_gen_tool`) is the fast default at ~40s. This is the
escalation: better prompt adherence on complex scenes, ~2.5x slower.

ComfyUI is a *server* holding ~30-42 GB resident, so it is started on
demand and torn down when the request finishes. If it was already up
(someone ran launch.sh by hand) we use it and leave it running.

Requires `--disable-mmap` in launch.sh; without it weight loading never
finishes on this box. See memory/lessons.md 2026-09-20.
"""
from __future__ import annotations

import json
import logging
import os
import random
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger("nexus.comfy_qwen")

COMFY_DIR = Path.home() / "Dev" / "ComfyUI"
LAUNCH = COMFY_DIR / "launch.sh"
WORKFLOW = COMFY_DIR / "my_workflows" / "wf_qwen_2_1_t2i_api.json"
COMFY_OUT = COMFY_DIR / "output"
URL = "http://127.0.0.1:8188"
OUTPUT_DIR = Path.home() / "AI_Agent" / "output" / "images"

BOOT_TIMEOUT = 180      # server up and answering
RUN_TIMEOUT = 900       # queue -> PNG, incl. first-time weight load
DEFAULT_STEPS = 25


def _up(timeout: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(f"{URL}/system_stats", timeout=timeout).read()
        return True
    except Exception:
        return False


def _start() -> Optional[subprocess.Popen]:
    """Launch ComfyUI detached. Returns the Popen we own, or None on failure."""
    if not LAUNCH.exists():
        return None
    logf = open(COMFY_DIR / "comfy-ondemand.log", "ab")
    proc = subprocess.Popen(
        ["bash", str(LAUNCH)], cwd=str(COMFY_DIR),
        stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True,      # survives our shell, killable as a group
    )
    deadline = time.monotonic() + BOOT_TIMEOUT
    while time.monotonic() < deadline:
        if _up():
            return proc
        if proc.poll() is not None:   # died during boot
            return None
        time.sleep(2)
    _kill(proc)
    return None


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        return
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


def _patch(wf: dict, prompt: str, steps: int, seed: int,
           width: Optional[int], height: Optional[int]) -> None:
    """Fill the workflow by class_type, not by node id -- ids move."""
    for node in wf.values():
        ct = node.get("class_type")
        inp = node.setdefault("inputs", {})
        if ct == "TextEncodeQwenImage21":
            inp["prompt"] = prompt
        elif ct == "KSampler":
            inp["seed"] = seed
            inp["steps"] = steps
        elif ct == "EmptyLatentImage" and width and height:
            # replaces the ResolutionSelector links with literals
            inp["width"] = int(width)
            inp["height"] = int(height)


def _submit(wf: dict) -> str:
    req = urllib.request.Request(
        f"{URL}/prompt", data=json.dumps({"prompt": wf}).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]


def _await(pid: str) -> tuple[Optional[str], Optional[str]]:
    """Poll history. Returns (comfy_filename, error)."""
    deadline = time.monotonic() + RUN_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(3)
        try:
            h = json.loads(urllib.request.urlopen(
                f"{URL}/history/{pid}", timeout=30).read())
        except Exception:
            continue
        if pid not in h:
            continue
        st = h[pid].get("status", {})
        if st.get("status_str") == "error":
            for m in st.get("messages", []):
                if m[0] == "execution_error":
                    return None, str(m[1].get("exception_message"))[:300]
            return None, "execution failed"
        if st.get("completed"):
            for out in h[pid].get("outputs", {}).values():
                for im in out.get("images", []):
                    return os.path.join(im.get("subfolder", ""), im["filename"]), None
            return None, "completed but produced no image"
    return None, f"timed out (>{RUN_TIMEOUT}s)"


def generate(prompt: str, *, steps: Optional[int] = None, seed: int = -1,
             width: Optional[int] = None, height: Optional[int] = None,
             filename: Optional[str] = None) -> dict:
    """Generate one image. Same return shape as image_gen_tool.generate_image_core."""
    if not WORKFLOW.exists():
        return {"ok": False, "error": f"workflow missing at {WORKFLOW}", "path": None}
    if not (prompt or "").strip():
        return {"ok": False, "error": "empty prompt", "path": None}

    if seed is None or seed < 0:
        seed = random.randint(1, 2_147_483_646)
    steps = steps or DEFAULT_STEPS

    owned = None
    if not _up():
        owned = _start()
        if owned is None:
            return {"ok": False, "path": None,
                    "error": "ComfyUI failed to start (see Dev/ComfyUI/comfy-ondemand.log)"}

    t0 = time.monotonic()
    try:
        wf = json.load(open(WORKFLOW))
        _patch(wf, prompt.strip(), steps, seed, width, height)
        try:
            pid = _submit(wf)
        except urllib.error.HTTPError as e:
            return {"ok": False, "path": None,
                    "error": "workflow rejected: " + e.read().decode()[:300]}
        rel, err = _await(pid)
        if err:
            return {"ok": False, "error": err, "path": None}

        src = COMFY_OUT / rel
        if not src.exists():
            return {"ok": False, "error": f"output missing at {src}", "path": None}
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        base = filename or f"img-{time.strftime('%Y%m%d-%H%M%S')}-qwen21-{seed}"
        dst = OUTPUT_DIR / f"{base}.png"
        shutil.copy2(src, dst)          # keep every generator's output in one place
        return {"ok": True, "path": str(dst), "seconds": round(time.monotonic() - t0, 1),
                "seed": seed, "model": "qwen21", "error": None}
    finally:
        if owned is not None:
            _kill(owned)

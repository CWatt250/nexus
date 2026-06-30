"""Image generation for Nexus — LOCAL stable-diffusion.cpp on Vulkan.

Replaces the old dead ERNIE cloud stub. Generation runs entirely on
WattBott's Radeon 8060S iGPU (gfx1151) via the prebuilt sd.cpp Vulkan
binary — $0, offline, ~10s for a 512x512 SD1.5 image.

Backend assets (gitignored, not in the repo):
  models/sdcpp/sd-cli                    prebuilt Vulkan binary + .so libs
  models/sdcpp/models/sd15.safetensors   the diffusion model
Swap the model file (e.g. SDXL-Turbo) and point SD_MODEL at it for higher
quality; nothing else changes.
"""
from __future__ import annotations

import logging
import os
import random
import subprocess
import time
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

log = logging.getLogger("nexus.image_gen")

ROOT = Path.home() / "AI_Agent"
SDCPP_DIR = ROOT / "models" / "sdcpp"
SD_BIN = SDCPP_DIR / "sd-cli"
SD_MODEL = SDCPP_DIR / "models" / "sd15.safetensors"
OUTPUT_DIR = ROOT / "output" / "images"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_MAX_DIM = 1024  # SD1.5 is trained at 512; cap larger requests for speed/safety
_DEFAULT_NEGATIVE = ("lowres, blurry, deformed, disfigured, bad anatomy, "
                     "extra limbs, watermark, text, jpeg artifacts")


def _parse_size(size: str) -> tuple[int, int]:
    try:
        w, h = (int(x) for x in str(size).lower().split("x", 1))
        return min(w, _MAX_DIM), min(h, _MAX_DIM)
    except Exception:
        return 512, 512


def generate_image_core(
    prompt: str, *, width: int = 512, height: int = 512, steps: int = 20,
    cfg_scale: float = 7.0, seed: int = -1, negative: str = _DEFAULT_NEGATIVE,
    filename: Optional[str] = None,
) -> dict:
    """Generate one image locally. Returns {ok, path, seconds, seed, error}."""
    if not SD_BIN.exists():
        return {"ok": False, "error": f"sd.cpp binary missing at {SD_BIN}",
                "path": None}
    if not SD_MODEL.exists():
        return {"ok": False, "error": f"model missing at {SD_MODEL}", "path": None}
    if not (prompt or "").strip():
        return {"ok": False, "error": "empty prompt", "path": None}

    if seed is None or seed < 0:
        seed = random.randint(1, 2_147_483_646)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = filename or f"img-{stamp}-{seed}"
    out_path = OUTPUT_DIR / f"{base}.png"

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = f"{SDCPP_DIR}:{env.get('LD_LIBRARY_PATH', '')}"
    cmd = [
        str(SD_BIN), "-m", str(SD_MODEL), "-p", prompt.strip(),
        "-n", negative, "--steps", str(steps), "--cfg-scale", str(cfg_scale),
        "-W", str(width), "-H", str(height), "--seed", str(seed),
        "-o", str(out_path),
    ]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=str(SDCPP_DIR), env=env,
                              capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "generation timed out (>300s)", "path": None}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "path": None}
    secs = round(time.monotonic() - t0, 1)

    if not out_path.exists():
        tail = (proc.stderr or proc.stdout or "")[-400:]
        return {"ok": False, "error": f"no image produced. {tail}", "path": None}
    return {"ok": True, "path": str(out_path), "seconds": secs, "seed": seed,
            "error": None}


@tool
def generate_image(
    prompt: str, size: str = "512x512", style: str = "",
    negative_prompt: str = "", steps: int = 20,
    filename: Optional[str] = None,
) -> str:
    """Generate an image from a text prompt using the LOCAL Stable Diffusion
    backend (sd.cpp on the Vulkan iGPU — free, offline, ~10s).

    Args:
        prompt: Description of the image. Be specific; add style words
            ("photorealistic", "watercolor", "3d render") for control.
        size: "WxH" — 512x512 (fastest), 768x512, etc. SD1.5 is best at 512.
        style: Optional style hint appended to the prompt.
        negative_prompt: What to avoid (defaults to a quality-boosting set).
        steps: Sampling steps (20 is a good default; fewer = faster).
        filename: Optional output filename (no extension).

    Returns:
        Path to the saved PNG, or an error string.
    """
    width, height = _parse_size(size)
    full_prompt = f"{prompt.strip()}, {style.strip()}" if style.strip() else prompt
    res = generate_image_core(
        full_prompt, width=width, height=height, steps=steps,
        negative=negative_prompt.strip() or _DEFAULT_NEGATIVE,
        filename=filename,
    )
    if not res["ok"]:
        return f"image generation failed: {res['error']}"
    return f"Image saved to {res['path']} ({res['seconds']}s, seed={res['seed']})"


@tool
def list_generated_images(limit: int = 10) -> str:
    """List recently generated images.

    Args:
        limit: Maximum number of images to list

    Returns:
        List of image paths with timestamps
    """
    try:
        images = sorted(OUTPUT_DIR.glob("*.png"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        if not images:
            return "No generated images found."
        result = f"Recent images (showing {min(len(images), limit)} of {len(images)}):\n"
        for img in images[:limit]:
            mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(img.stat().st_mtime))
            result += f"  {mtime} - {img.name}\n"
        return result
    except Exception as e:
        return f"Error listing images: {type(e).__name__}: {e}"


IMAGE_GEN_TOOLS = [generate_image, list_generated_images]

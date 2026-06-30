"""Image generation for Nexus — LOCAL stable-diffusion.cpp on Vulkan.

Replaces the old dead ERNIE cloud stub. Generation runs entirely on
WattBott's Radeon 8060S iGPU (gfx1151) via the prebuilt sd.cpp Vulkan
binary — $0, offline.

Three local models (pick via `model=`):
  flux  — FLUX.1-schnell Q4 (DEFAULT). Best quality, real in-image TEXT,
          strong prompt adherence. 1024px, ~37s (12B model).
  sdxl  — SDXL-Turbo. Detailed, 1024px, ~21s.
  sd15  — SD1.5. Soft/cute, 512px, ~10s — fastest.

Backend assets are gitignored (large) — see docs/image-gen-setup.md to
re-provision: models/sdcpp/{sd-cli,*.so}, models/sdcpp/models/*.safetensors,
models/sdcpp/flux/{flux1-schnell-Q4_K_S.gguf,t5xxl_fp8.safetensors,
clip_l.safetensors,ae.safetensors}.
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
MODELS_DIR = SDCPP_DIR / "models"
FLUX_DIR = SDCPP_DIR / "flux"
OUTPUT_DIR = ROOT / "output" / "images"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_DEFAULT_NEGATIVE = ("lowres, blurry, deformed, disfigured, bad anatomy, "
                     "extra limbs, watermark, text, jpeg artifacts")

# Per-model invocation profiles. `model_args` are the sd.cpp flags that
# select/feed the checkpoint; the rest are the sampler settings each model
# wants. `uses_negative` is False for the cfg=1 distilled models (they
# ignore CFG, so a negative prompt does nothing).
MODELS: dict[str, dict] = {
    "flux": {
        "check": FLUX_DIR / "flux1-schnell-Q4_K_S.gguf",
        "model_args": [
            "--diffusion-model", str(FLUX_DIR / "flux1-schnell-Q4_K_S.gguf"),
            "--vae", str(FLUX_DIR / "ae.safetensors"),
            "--clip_l", str(FLUX_DIR / "clip_l.safetensors"),
            "--t5xxl", str(FLUX_DIR / "t5xxl_fp8.safetensors"),
            "--diffusion-fa",
        ],
        "steps": 4, "cfg": 1.0, "sampler": "euler", "dim": 1024,
        "vae_tiling": True, "uses_negative": False,
    },
    "sdxl": {
        "check": MODELS_DIR / "sdxl-turbo.safetensors",
        "model_args": ["-m", str(MODELS_DIR / "sdxl-turbo.safetensors")],
        "steps": 8, "cfg": 1.0, "sampler": "euler", "dim": 1024,
        "vae_tiling": True, "uses_negative": False,
    },
    "sd15": {
        "check": MODELS_DIR / "sd15.safetensors",
        "model_args": ["-m", str(MODELS_DIR / "sd15.safetensors")],
        "steps": 20, "cfg": 7.0, "sampler": None, "dim": 512,
        "vae_tiling": False, "uses_negative": True,
    },
}
DEFAULT_MODEL = "flux"


def _resolve_model(model: str) -> str:
    m = (model or DEFAULT_MODEL).strip().lower()
    if m in MODELS and MODELS[m]["check"].exists():
        return m
    # Fall back to the best model whose assets are actually present.
    for cand in ("flux", "sdxl", "sd15"):
        if MODELS[cand]["check"].exists():
            return cand
    return DEFAULT_MODEL


def generate_image_core(
    prompt: str, *, model: str = DEFAULT_MODEL,
    width: Optional[int] = None, height: Optional[int] = None,
    steps: Optional[int] = None, cfg_scale: Optional[float] = None,
    seed: int = -1, negative: str = _DEFAULT_NEGATIVE,
    filename: Optional[str] = None,
) -> dict:
    """Generate one image locally. Returns {ok, path, seconds, seed, model, error}."""
    if not SD_BIN.exists():
        return {"ok": False, "error": f"sd.cpp binary missing at {SD_BIN}", "path": None}
    if not (prompt or "").strip():
        return {"ok": False, "error": "empty prompt", "path": None}

    name = _resolve_model(model)
    cfg = MODELS[name]
    if not cfg["check"].exists():
        return {"ok": False, "error": f"no image model installed (see "
                f"docs/image-gen-setup.md)", "path": None}

    w = width or cfg["dim"]
    h = height or cfg["dim"]
    steps = steps or cfg["steps"]
    cfg_scale = cfg["cfg"] if cfg_scale is None else cfg_scale
    if seed is None or seed < 0:
        seed = random.randint(1, 2_147_483_646)
    base = filename or f"img-{time.strftime('%Y%m%d-%H%M%S')}-{name}-{seed}"
    out_path = OUTPUT_DIR / f"{base}.png"

    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = f"{SDCPP_DIR}:{env.get('LD_LIBRARY_PATH', '')}"
    cmd = [str(SD_BIN), *cfg["model_args"], "-p", prompt.strip(),
           "--steps", str(steps), "--cfg-scale", str(cfg_scale),
           "-W", str(w), "-H", str(h), "--seed", str(seed), "-o", str(out_path)]
    if cfg["sampler"]:
        cmd += ["--sampling-method", cfg["sampler"]]
    if cfg["vae_tiling"]:
        cmd += ["--vae-tiling"]
    if cfg["uses_negative"]:
        cmd += ["-n", negative]

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
            "model": name, "error": None}


@tool
def generate_image(
    prompt: str, model: str = "flux", size: str = "",
    style: str = "", steps: int = 0, filename: Optional[str] = None,
) -> str:
    """Generate an image from a text prompt using a LOCAL Stable Diffusion
    model (sd.cpp on the Vulkan iGPU — free, offline).

    Args:
        prompt: Description of the image. Be specific; FLUX follows detailed
            prompts well and can render readable text (logos, signs).
        model: "flux" (default, best quality + text, ~37s), "sdxl" (detailed,
            ~21s), or "sd15" (soft/cute, fastest ~10s).
        size: "WxH" override (else the model's native size — 1024 for
            flux/sdxl, 512 for sd15).
        style: Optional style hint appended to the prompt.
        steps: Override sampling steps (0 = model default).
        filename: Optional output filename (no extension).

    Returns:
        Path to the saved PNG, or an error string.
    """
    w = h = None
    if size:
        try:
            w, h = (int(x) for x in size.lower().split("x", 1))
        except Exception:
            w = h = None
    full = f"{prompt.strip()}, {style.strip()}" if style.strip() else prompt
    res = generate_image_core(full, model=model, width=w, height=h,
                              steps=steps or None, filename=filename)
    if not res["ok"]:
        return f"image generation failed: {res['error']}"
    return (f"Image saved to {res['path']} "
            f"(model={res['model']}, {res['seconds']}s, seed={res['seed']})")


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

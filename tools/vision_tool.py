"""Phase 16.7 / Phase 3 — vision tool on the brain's built-in projector.

Exposes describe_image(path) and ask_about_image(path, question) as
LangGraph tools. Sibling to tools/computer_use_tool.find_on_screen_vision
(which is screen-coordinate-specific); this one handles arbitrary
image files.

Backend (Phase 3): the resident brain (Ornith-1.5, `core.brain.get_brain_model()`)
which ships a CLIP projector — no separate VL model to load. Override with
NEXUS_VISION_MODEL. The projector runs on CPU on this iGPU, so every
image is downscaled to ≤1024 px long edge / JPEG q85 before the call
(a 919x2000 raw screenshot = 3,015 image tokens = 196 s; downscaled it
is ~220 tokens). Returns a clear 'vision ...' string on any failure.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

_ROOT = Path.home() / "AI_Agent"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.brain import get_brain_model, num_ctx_for, think_param  # noqa: E402

# Resolved at call time (models.json / env may change); None = brain.
DEFAULT_VISION_MODEL: Optional[str] = None
DEFAULT_NUM_PREDICT = 200
VISION_MAX_EDGE = 1024
VISION_JPEG_QUALITY = 85

log = logging.getLogger("nexus.vision_tool")


def vision_model() -> str:
    """Model used for vision calls: NEXUS_VISION_MODEL env, else the brain."""
    return os.environ.get("NEXUS_VISION_MODEL") or get_brain_model()


def downscale_bytes(raw: bytes, max_edge: int = VISION_MAX_EDGE,
                    quality: int = VISION_JPEG_QUALITY) -> bytes:
    """≤max_edge on the long side (LANCZOS), JPEG q85. Falls back to the
    raw bytes if PIL can't decode them."""
    try:
        from PIL import Image  # noqa: PLC0415
        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGB")
            w, h = im.size
            scale = max(w, h) / float(max_edge)
            if scale > 1.0:
                im = im.resize((round(w / scale), round(h / scale)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True)
            return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        log.warning("vision_tool: downscale failed (%s) — sending raw", exc)
        return raw


def _read_image_bytes(path: str) -> Optional[bytes]:
    """Read an image and return raw bytes, or None if missing/unreadable."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return None
    try:
        return p.read_bytes()
    except OSError as exc:
        log.warning("vision_tool: read failed for %s: %s", path, exc)
        return None


def _vision_chat(prompt: str, image_b64: str, *,
                 model: Optional[str] = DEFAULT_VISION_MODEL,
                 num_ctx: Optional[int] = None,
                 num_predict: int = DEFAULT_NUM_PREDICT) -> str:
    """Single Ollama VL call. Returns the response text or a clear
    error string starting with 'vision'. Never raises.
    num_ctx is always `core.brain.num_ctx_for(model)` (one ctx per model —
    a different value forces an Ollama runner reload); the param is kept
    for signature compatibility and ignored."""
    try:
        import ollama  # noqa: PLC0415
    except Exception as exc:
        return f"vision unavailable: ollama package missing ({exc})"
    model = model or vision_model()
    # Downscale here so EVERY caller (incl. tools/visual_verify) obeys the
    # ≤1024px rule — a raw screenshot costs minutes on the CPU projector.
    try:
        image_b64 = base64.b64encode(downscale_bytes(base64.b64decode(image_b64))).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        log.warning("vision_tool: could not downscale payload (%s)", exc)
    try:
        resp = ollama.Client(host="http://localhost:11434", timeout=300).chat(
            model=model,
            messages=[{"role": "user", "content": prompt, "images": [image_b64]}],
            stream=False,
            think=think_param(model),
            options={"temperature": 0.2, "num_ctx": num_ctx_for(model),
                     "num_predict": num_predict},
            keep_alive=-1,
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "not found" in msg or "no such model" in msg:
            return (
                f"vision model {model!r} not installed. "
                f"Run: ollama pull {model}"
            )
        return f"vision call failed: {type(exc).__name__}: {exc}"
    return (resp.get("message", {}) or {}).get("content", "").strip() or "(no response)"


def describe_image_core(path: str, *, model: Optional[str] = DEFAULT_VISION_MODEL) -> str:
    """Direct entry point used by ask_about_image and unit tests."""
    raw = _read_image_bytes(path)
    if raw is None:
        return f"vision: image not found at {path}"
    image_b64 = base64.b64encode(raw).decode("ascii")
    return _vision_chat(
        "Describe this image in 1-2 sentences. Be concrete about what's "
        "visible — colors, shapes, text, subjects. Do not speculate about "
        "context or backstory.",
        image_b64,
        model=model,
    )


@tool
def describe_image(path: str) -> str:
    """Describe what's in an image using a vision-language model.

    Loads the image at `path`, downscales it, sends it to the local brain's
    vision projector, and returns a 1-2 sentence description of the actual visual
    content (colors, shapes, text, subjects). For screen coordinates
    use `find_on_screen_vision` instead.

    Args:
        path: Filesystem path to the image. ~ is expanded.

    Returns:
        Short description, or a clear error message starting with
        'vision' (e.g., 'vision: image not found at /tmp/foo.png').
    """
    return describe_image_core(path)


def ask_about_image_core(path: str, question: str, *,
                         model: Optional[str] = DEFAULT_VISION_MODEL) -> str:
    """Direct entry point used by the @tool and the Telegram photo handler."""
    raw = _read_image_bytes(path)
    if raw is None:
        return f"vision: image not found at {path}"
    if not question or not question.strip():
        return "vision: question is empty"
    image_b64 = base64.b64encode(raw).decode("ascii")
    return _vision_chat(
        question.strip() + "\n\nAnswer in 1-3 short sentences.",
        image_b64,
        model=model,
    )


@tool
def ask_about_image(path: str, question: str) -> str:
    """Ask a free-form question about an image using a vision model.

    Use this when you need more than a generic description — e.g.,
    'What button is highlighted in this screenshot?' or 'Is the chart
    going up or down?'.

    Args:
        path: Filesystem path to the image.
        question: Natural-language question about the image content.

    Returns:
        Model's answer, or a vision-error string.
    """
    return ask_about_image_core(path, question)


VISION_TOOLS = [describe_image, ask_about_image]

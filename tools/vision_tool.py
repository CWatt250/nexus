"""Phase 16.7 — vision tool wrapping qwen2.5vl:7b.

Exposes describe_image(path) and ask_about_image(path, question) as
LangGraph tools. Sibling to tools/computer_use_tool.find_on_screen_vision
(which is screen-coordinate-specific); this one handles arbitrary
image files.

Backend: Ollama-served qwen2.5vl:7b. Loads in ~5s warm, ~13 GB on the
GPU partition. Returns a clear 'vision unavailable' string if the
model isn't pulled, so callers don't need to handle exceptions.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

DEFAULT_VISION_MODEL = "qwen2.5vl:7b"
DEFAULT_NUM_CTX = 4096
DEFAULT_NUM_PREDICT = 200

log = logging.getLogger("nexus.vision_tool")


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
                 model: str = DEFAULT_VISION_MODEL,
                 num_ctx: int = DEFAULT_NUM_CTX,
                 num_predict: int = DEFAULT_NUM_PREDICT) -> str:
    """Single Ollama VL call. Returns the response text or a clear
    error string starting with 'vision'. Never raises."""
    try:
        import ollama  # noqa: PLC0415
    except Exception as exc:
        return f"vision unavailable: ollama package missing ({exc})"
    try:
        resp = ollama.Client(host="http://localhost:11434").chat(
            model=model,
            messages=[{"role": "user", "content": prompt, "images": [image_b64]}],
            stream=False,
            options={"temperature": 0.2, "num_ctx": num_ctx, "num_predict": num_predict},
            keep_alive=300,  # keep VL model warm for ~5min between calls
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


def describe_image_core(path: str, *, model: str = DEFAULT_VISION_MODEL) -> str:
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

    Loads the image at `path`, sends it to Ollama-served qwen2.5vl:7b,
    and returns a 1-2 sentence description of the actual visual
    content (colors, shapes, text, subjects). For screen coordinates
    use `find_on_screen_vision` instead.

    Args:
        path: Filesystem path to the image. ~ is expanded.

    Returns:
        Short description, or a clear error message starting with
        'vision' (e.g., 'vision: image not found at /tmp/foo.png').
    """
    return describe_image_core(path)


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
    raw = _read_image_bytes(path)
    if raw is None:
        return f"vision: image not found at {path}"
    if not question or not question.strip():
        return "vision: question is empty"
    image_b64 = base64.b64encode(raw).decode("ascii")
    return _vision_chat(
        question.strip() + "\n\nAnswer in 1-3 short sentences.",
        image_b64,
    )


VISION_TOOLS = [describe_image, ask_about_image]

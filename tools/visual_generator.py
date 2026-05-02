"""Phase 21.3 — visual generator for content_create.

Tries the existing image_gen_tool (ERNIE) first; falls back to a PIL
solid-color rectangle with the scene description rendered as overlay
text when the API key is missing or the call fails.

Output is always 1080x1920 vertical PNG so the rest of the pipeline
(ffmpeg assembly) can pin to a single canvas size.
"""
from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

CANVAS_W = 1080
CANVAS_H = 1920

# A small palette tuned for vertical short-form. Index picked by hashing
# the scene description so each scene gets a stable, distinct background.
PALETTE = [
    ("#0A0E27", "#6366F1"),   # navy + indigo
    ("#1A1F4F", "#AF52DE"),   # deep purple + violet
    ("#0E1A1A", "#34D399"),   # blackish + emerald
    ("#1F0E1A", "#EC4899"),   # plum + pink
    ("#0E1A1F", "#38BDF8"),   # blue-grey + sky
    ("#1A1A0E", "#FACC15"),   # warm dark + yellow
    ("#1A0E0E", "#F87171"),   # crimson + red
]


def _pick_palette(text: str) -> tuple[str, str]:
    h = int(hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest(), 16)
    return PALETTE[h % len(PALETTE)]


def _find_font(preferred_sizes: list[int]) -> list[ImageFont.FreeTypeFont]:
    """Walk the FS for a usable TTF; fall back to PIL default. Caller
    passes target sizes; we return a parallel list of font objects."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    ]
    chosen: Optional[str] = None
    for c in candidates:
        if Path(c).exists():
            chosen = c
            break
    if not chosen:
        return [ImageFont.load_default() for _ in preferred_sizes]
    return [ImageFont.truetype(chosen, sz) for sz in preferred_sizes]


def _gradient_bg(size: tuple[int, int], dark_hex: str, accent_hex: str) -> Image.Image:
    """Diagonal gradient from dark → accent. Cheap radial effect via
    bilinear-ish stretch from a 4x4 numpy-free patch."""
    w, h = size
    img = Image.new("RGB", size, dark_hex)
    draw = ImageDraw.Draw(img, "RGBA")
    dr = int(dark_hex[1:3], 16)
    dg = int(dark_hex[3:5], 16)
    db = int(dark_hex[5:7], 16)
    ar = int(accent_hex[1:3], 16)
    ag = int(accent_hex[3:5], 16)
    ab = int(accent_hex[5:7], 16)
    # Diagonal stripes — paint top-left → bottom-right gradient with
    # narrow horizontal bands. 60 bands looks smooth at 1920 tall.
    bands = 60
    for i in range(bands):
        t = i / (bands - 1)
        r = int(dr + (ar - dr) * t)
        g = int(dg + (ag - dg) * t)
        b = int(db + (ab - db) * t)
        y0 = int(h * i / bands)
        y1 = int(h * (i + 1) / bands)
        draw.rectangle([0, y0, w, y1], fill=(r, g, b))
    # Glow blob in upper-left for depth.
    draw.ellipse([-200, -200, w * 0.55, h * 0.35],
                 fill=(ar, ag, ab, 60))
    return img


def _render_pil_fallback(scene_description: str, output_path: Path,
                          scene_no: Optional[int] = None) -> None:
    """Render a 1080x1920 vertical card with the scene description
    overlaid in big bold text. Used when no real image generator is
    available."""
    dark_hex, accent_hex = _pick_palette(scene_description)
    img = _gradient_bg((CANVAS_W, CANVAS_H), dark_hex, accent_hex)
    draw = ImageDraw.Draw(img, "RGBA")

    title_font, body_font, label_font = _find_font([88, 56, 36])

    # Optional scene label in top-left.
    if scene_no:
        draw.text((60, 80), f"SCENE {scene_no}",
                  fill=(255, 255, 255, 200), font=label_font)

    # Wrap scene description into the canvas. ~14 chars per line
    # at title size feels readable on a phone screen.
    lines = textwrap.wrap(scene_description.strip(), width=20)[:6]
    line_h = 110
    block_h = line_h * len(lines)
    y = (CANVAS_H - block_h) // 2

    # Drop-shadow text for legibility on the gradient.
    for line in lines:
        # Shadow
        draw.text((62, y + 4), line,
                  fill=(0, 0, 0, 180), font=title_font)
        # Foreground
        draw.text((60, y), line,
                  fill=(255, 255, 255, 255), font=title_font)
        y += line_h

    # Bottom watermark — Nexus brand stamp.
    stamp_y = CANVAS_H - 100
    draw.text((60, stamp_y), "NEXUS",
              fill=(255, 255, 255, 180), font=body_font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, format="PNG", optimize=True)


def visual_generate(
    scene_description: str,
    output_path: str | Path,
    scene_no: Optional[int] = None,
    prefer_real: bool = True,
) -> dict:
    """Produce one 1080x1920 vertical image for a scene.

    Args:
        scene_description: The [VISUAL]: text for the scene.
        output_path: Where to write the PNG.
        scene_no: Optional scene number rendered as a label on the
            fallback card (helps debugging when a script has many
            similar-looking scenes).
        prefer_real: If True, attempt the real image generator first.
            Set False to skip straight to PIL fallback (deterministic,
            no API cost — useful for unit tests and CI).

    Returns:
        {image_path, was_fallback (bool), generator (str), error (str|None)}.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if prefer_real:
        try:
            from tools.image_gen_tool import generate_image  # noqa: PLC0415
            result = generate_image.invoke({
                "prompt": scene_description,
                "size": "1024x1024",
                "style": "realistic",
            })
            # The wrapped tool returns an error STRING when the API key
            # is missing or the call fails — never raises. Detect that.
            if isinstance(result, str) and result.startswith("Error"):
                pass  # fall through to PIL
            elif isinstance(result, str) and result.startswith("Image saved:"):
                src = Path(result.replace("Image saved:", "").strip())
                if src.exists():
                    img = Image.open(src).convert("RGB")
                    img = img.resize((CANVAS_W, CANVAS_H), Image.LANCZOS)
                    img.save(out, format="PNG", optimize=True)
                    return {
                        "image_path": str(out),
                        "was_fallback": False,
                        "generator": "image_gen_tool/ernie",
                        "error": None,
                    }
        except Exception as exc:  # noqa: BLE001 — fall back on any error
            error_str = f"image_gen_tool failed: {type(exc).__name__}: {exc}"
        else:
            error_str = None
    else:
        error_str = None

    # PIL fallback — solid gradient with overlaid text. Always succeeds.
    _render_pil_fallback(scene_description, out, scene_no=scene_no)
    return {
        "image_path": str(out),
        "was_fallback": True,
        "generator": "pil_card",
        "error": error_str,
    }

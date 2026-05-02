"""Phase 21.3 + Phase 21 Part 2.5 — visual generator for content_create.

Tries the existing image_gen_tool (ERNIE) first; falls back to a PIL
gradient card with the scene description rendered as overlay text when
the API key is missing or the call fails.

Output is always 1080x1920 vertical PNG so the rest of the pipeline
(ffmpeg assembly) can pin to a single canvas size.

Phase 21 Part 2.5 improvements:
  - Tone-aware palette (energetic = warm, chill = cool, etc.)
  - Smarter scene text rendering — extracts 1-2 keywords, big typography
  - Subtle film-grain noise overlay so cards don't look completely flat
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
import textwrap
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CANVAS_W = 1080
CANVAS_H = 1920

log = logging.getLogger("nexus.visual_generator")

# Tone-keyed palettes. Each palette is a list of (dark, accent) hex
# pairs; the picker hashes the scene text to choose between them so
# multiple scenes with the same tone still get visually distinct cards.
TONE_PALETTES: dict[str, list[tuple[str, str]]] = {
    "energetic": [
        ("#1A0A02", "#FB923C"),   # deep brown + orange
        ("#2A0E0E", "#F87171"),   # crimson + red
        ("#1F1A0E", "#FACC15"),   # warm dark + yellow
        ("#26100A", "#FB7185"),   # rust + rose
    ],
    "chill": [
        ("#0E1A2A", "#38BDF8"),   # deep navy + sky
        ("#0E1F1A", "#34D399"),   # forest + emerald
        ("#0F1424", "#818CF8"),   # midnight + indigo
        ("#0A1F2A", "#22D3EE"),   # ocean + cyan
    ],
    "dramatic": [
        ("#0A0A0A", "#DC2626"),   # near-black + red
        ("#0E0A1A", "#A855F7"),   # plum + violet
        ("#1A0A0E", "#EF4444"),   # blood + red
        ("#0A0A1F", "#6366F1"),   # midnight + indigo
    ],
    "cinematic": [
        ("#0A0E27", "#FBBF24"),   # navy + gold
        ("#0E1A2A", "#FB923C"),   # ocean + amber
        ("#1A0E27", "#F472B6"),   # plum + pink
        ("#0E1424", "#60A5FA"),   # midnight + steel
    ],
    "minimal": [
        ("#0F0F0F", "#FFFFFF"),   # near-black + white
        ("#1A1A1A", "#A3A3A3"),   # charcoal + grey
        ("#0E0E0E", "#E5E5E5"),   # graphite + chalk
        ("#141414", "#D4D4D4"),   # off-black + silver
    ],
}

DEFAULT_TONE = "energetic"

# Common stop-words filtered out when extracting scene keywords.
_STOPWORDS = {
    "a", "an", "the", "of", "and", "or", "in", "on", "at", "to", "for",
    "with", "from", "by", "as", "is", "are", "was", "were", "be", "been",
    "being", "this", "that", "these", "those", "it", "its", "they", "them",
    "their", "we", "our", "you", "your", "i", "my", "he", "she", "his",
    "her", "shot", "scene", "view", "image", "shows", "showing", "showing",
    "displays", "displayed", "while", "during", "into", "out", "up", "down",
    "over", "under", "above", "below", "across", "very", "really", "just",
    "some", "any", "all", "more", "most", "some", "than", "then",
}


def _pick_palette(text: str, tone: str) -> tuple[str, str]:
    """Pick a (dark, accent) hex pair stable for the given text+tone."""
    palettes = TONE_PALETTES.get(tone.strip().lower(), TONE_PALETTES[DEFAULT_TONE])
    h = int(hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest(), 16)
    return palettes[h % len(palettes)]


def _find_font(preferred_sizes: list[int]) -> list[ImageFont.FreeTypeFont]:
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


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _gradient_bg(size: tuple[int, int], dark_hex: str, accent_hex: str) -> Image.Image:
    """Diagonal gradient with a glow blob."""
    w, h = size
    img = Image.new("RGB", size, dark_hex)
    draw = ImageDraw.Draw(img, "RGBA")
    dr, dg, db = _hex_to_rgb(dark_hex)
    ar, ag, ab = _hex_to_rgb(accent_hex)
    bands = 80
    for i in range(bands):
        t = i / (bands - 1)
        # ease-out so the gradient pools at the bottom
        t = t * t
        r = int(dr + (ar - dr) * t)
        g = int(dg + (ag - dg) * t)
        b = int(db + (ab - db) * t)
        y0 = int(h * i / bands)
        y1 = int(h * (i + 1) / bands)
        draw.rectangle([0, y0, w, y1], fill=(r, g, b))
    # Glow blob in upper area for depth.
    draw.ellipse([-200, -200, w * 0.55, h * 0.35],
                 fill=(ar, ag, ab, 70))
    return img


def _add_noise(img: Image.Image, amount: float = 0.04) -> Image.Image:
    """Subtle film-grain. amount ∈ [0,1] = max per-channel offset (frac)."""
    rng = random.Random(0xC0FFEE)
    w, h = img.size
    # Generate a small noise tile then tile it — cheaper than per-pixel.
    tile_size = 256
    noise = Image.new("L", (tile_size, tile_size))
    px = noise.load()
    for x in range(tile_size):
        for y in range(tile_size):
            px[x, y] = int(128 + (rng.random() - 0.5) * 255 * amount * 2)
    # Tile noise to canvas
    full = Image.new("L", (w, h))
    for x in range(0, w, tile_size):
        for y in range(0, h, tile_size):
            full.paste(noise, (x, y))
    # Soft blur to make grain organic
    full = full.filter(ImageFilter.GaussianBlur(0.7))
    # Convert to RGB + blend
    full_rgb = Image.merge("RGB", (full, full, full))
    return Image.blend(img, full_rgb, amount * 1.5)


def _extract_keywords(text: str, max_words: int = 3) -> list[str]:
    """Pull 1-3 high-impact words out of a scene description. Drops
    stopwords and short tokens, ranks by length and position (earlier =
    more likely to be the subject)."""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9'\-]+", text)
    seen: set[str] = set()
    candidates: list[tuple[str, int]] = []  # (word, score)
    for i, w in enumerate(tokens):
        lw = w.lower()
        if lw in _STOPWORDS or len(w) < 4 or lw in seen:
            continue
        seen.add(lw)
        # Earlier words score higher; capitalised/proper-noun-ish bumps too
        score = max(1, 50 - i) + (5 if w[0].isupper() else 0) + min(len(w), 12)
        candidates.append((w.upper(), score))
    candidates.sort(key=lambda x: -x[1])
    return [w for w, _ in candidates[:max_words]]


def _render_pil_fallback(scene_description: str, output_path: Path,
                          scene_no: Optional[int] = None,
                          tone: str = DEFAULT_TONE) -> None:
    """Render a 1080x1920 vertical card with a few large keywords drawn
    over a tone-matched gradient. Used when no real image generator is
    available."""
    dark_hex, accent_hex = _pick_palette(scene_description, tone)
    img = _gradient_bg((CANVAS_W, CANVAS_H), dark_hex, accent_hex)
    img = _add_noise(img, amount=0.035)
    draw = ImageDraw.Draw(img, "RGBA")

    # Keyword-driven typography: pull 1-3 strong words and stack them
    # large + bold. Beats wrapping the whole scene description into 6
    # lines of medium text.
    keywords = _extract_keywords(scene_description, max_words=3)
    if not keywords:
        # Fallback to first 3 capitalised tokens
        keywords = scene_description.upper().split()[:3]

    # Heuristic font sizing: fewer keywords → larger.
    if len(keywords) == 1:
        title_size, body_size, label_size = 240, 56, 36
    elif len(keywords) == 2:
        title_size, body_size, label_size = 200, 56, 36
    else:
        title_size, body_size, label_size = 160, 56, 36

    title_font, body_font, label_font = _find_font([title_size, body_size, label_size])

    # Optional scene label in top-left.
    if scene_no:
        draw.text((60, 80), f"SCENE {scene_no}",
                  fill=(255, 255, 255, 200), font=label_font)

    # Stack keywords vertically, centered. Some keywords may be longer
    # than the canvas at the chosen size — wrap if needed.
    lines: list[str] = []
    for kw in keywords:
        # If keyword is too long for canvas, split-wrap at chars
        wrapped = textwrap.wrap(kw, width=12) or [kw]
        lines.extend(wrapped[:2])  # cap each kw at 2 lines

    line_h = int(title_size * 1.1)
    block_h = line_h * len(lines)
    y = (CANVAS_H - block_h) // 2 - 100  # bias toward upper-middle

    # Drop-shadow text for legibility on the gradient.
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        text_w = bbox[2] - bbox[0]
        x = (CANVAS_W - text_w) // 2
        # Shadow
        draw.text((x + 4, y + 6), line,
                  fill=(0, 0, 0, 200), font=title_font)
        # Foreground
        draw.text((x, y), line,
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
    tone: str = DEFAULT_TONE,
) -> dict:
    """Produce one 1080x1920 vertical image for a scene.

    Args:
        scene_description: The [VISUAL]: text for the scene.
        output_path: Where to write the PNG.
        scene_no: Optional scene number rendered as a label on the
            fallback card.
        prefer_real: If True, attempt the real image generator first.
        tone: Drives palette selection in the PIL fallback. Ignored
            when the real image generator succeeds.

    Returns:
        {image_path, was_fallback (bool), generator (str), error (str|None)}.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    error_str: Optional[str] = None

    if prefer_real:
        try:
            from tools.image_gen_tool import generate_image  # noqa: PLC0415
            result = generate_image.invoke({
                "prompt": scene_description,
                "size": "1024x1024",
                "style": "realistic",
            })
            if isinstance(result, str) and result.startswith("Error"):
                error_str = result.splitlines()[0][:200]
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

    # PIL fallback — tone-matched gradient + keyword card. Always succeeds.
    _render_pil_fallback(scene_description, out, scene_no=scene_no, tone=tone)
    return {
        "image_path": str(out),
        "was_fallback": True,
        "generator": "pil_card",
        "error": error_str,
    }

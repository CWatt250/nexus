"""Telegram rendering helpers — markdown tables → PNG.

Telegram shows a GitHub-style pipe table as raw pipes, which is
unreadable on a phone. `prepare_for_telegram` swaps every table for a
one-line placeholder and hands back PNGs the caller sends as photos.

Pure text/PIL — no network. Fonts: DejaVu (system install).
"""
from __future__ import annotations

import re
import textwrap
import time
from pathlib import Path

PLACEHOLDER = "📊 table below"
MAX_CELL_CHARS = 28
MAX_ROWS = 60
SCALE = 2                # render at 2x for retina …
MAX_WIDTH_PX = 1600      # … then cap the final width here
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")

_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_MARKERS_RE = re.compile(r"\*\*|__|`|(?<!\w)\*(?!\s)|(?<!\s)\*(?!\w)")

THEMES = {
    "dark": {"bg": "#0f172a", "head_bg": "#1e293b", "alt_bg": "#162033",
             "grid": "#334155", "text": "#e2e8f0", "head_text": "#ffffff"},
    "light": {"bg": "#ffffff", "head_bg": "#e2e8f0", "alt_bg": "#f1f5f9",
              "grid": "#cbd5e1", "text": "#0f172a", "head_text": "#0f172a"},
}


# ── detection ────────────────────────────────────────────────────────
def _is_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _find_tables(text: str) -> list[tuple[int, int]]:
    """(start, end) line spans of pipe tables: header row, separator, ≥0 rows."""
    lines = text.splitlines()
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(lines) - 1:
        if _is_row(lines[i]) and _SEP_RE.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and _is_row(lines[j]):
                j += 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def _replace_tables(text: str, replacement: str) -> tuple[str, list[str]]:
    lines = text.splitlines()
    tables: list[str] = []
    out: list[str] = []
    pos = 0
    for start, end in _find_tables(text):
        out.extend(lines[pos:start])
        tables.append("\n".join(lines[start:end]))
        if replacement:
            out.append(replacement)
        pos = end
    out.extend(lines[pos:])
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return cleaned, tables


def has_table(text: str) -> bool:
    return bool(text) and bool(_find_tables(text))


def split_tables(text: str) -> tuple[str, list[str]]:
    """Return (text with tables removed, [table markdown, ...])."""
    if not text:
        return "", []
    return _replace_tables(text, "")


def prepare_with_captions(text: str, out_dir: str | Path | None = None,
                          theme: str = "dark") -> tuple[str, list[tuple[str, str]]]:
    """Swap each table for PLACEHOLDER and render it to a PNG.
    Returns (text, [(png path, caption), ...]). A render failure leaves
    that table's markdown in the text so nothing is lost."""
    if not text or not _find_tables(text):
        return text or "", []
    out_dir = Path(out_dir) if out_dir else Path.home() / "AI_Agent" / "output" / "tables"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time() * 1000)
    cleaned, tables = _replace_tables(text, PLACEHOLDER)
    pngs: list[tuple[str, str]] = []
    for n, md in enumerate(tables, start=1):
        try:
            path = render_table_png(md, out_dir / f"table-{stamp}-{n}.png", theme=theme)
            pngs.append((str(path), caption_for(md)))
        except Exception:  # noqa: BLE001 — keep the raw table in the text instead
            cleaned = cleaned.replace(PLACEHOLDER, md, 1)
    return cleaned, pngs


def prepare_for_telegram(text: str, out_dir: str | Path | None = None,
                         theme: str = "dark") -> tuple[str, list[str]]:
    """`prepare_with_captions` returning bare PNG paths."""
    cleaned, pngs = prepare_with_captions(text, out_dir, theme)
    return cleaned, [p for p, _ in pngs]


# ── parsing ──────────────────────────────────────────────────────────
def strip_markers(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    return _MARKERS_RE.sub("", s).strip()


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells = re.split(r"(?<!\\)\|", s)
    return [strip_markers(c.replace("\\|", "|")) for c in cells]


def parse_table(table_md: str) -> tuple[list[str], list[list[str]]]:
    lines = [ln for ln in table_md.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError("not a table")
    headers = _split_row(lines[0])
    rows = [_split_row(ln) for ln in lines[2:MAX_ROWS + 2]]
    width = len(headers)
    rows = [(r + [""] * width)[:width] for r in rows]
    return headers, rows


def caption_for(table_md: str, max_cells: int = 4) -> str:
    try:
        headers, _ = parse_table(table_md)
    except ValueError:
        return ""
    return " · ".join(h for h in headers[:max_cells] if h)[:100]


def _wrap_cell(text: str, width: int = MAX_CELL_CHARS) -> list[str]:
    out: list[str] = []
    for para in (text or "").split("\n"):
        out.extend(textwrap.wrap(para, width=width, break_long_words=True) or [""])
    return out or [""]


# ── drawing ──────────────────────────────────────────────────────────
def render_table_png(table_md: str, out_path: str | Path, theme: str = "dark") -> Path:
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    colors = THEMES.get(theme, THEMES["dark"])
    headers, rows = parse_table(table_md)
    grid = [headers] + rows

    size = 15 * SCALE
    pad_x, pad_y = 10 * SCALE, 7 * SCALE
    font = ImageFont.truetype(str(FONT_DIR / "DejaVuSans.ttf"), size)
    bold = ImageFont.truetype(str(FONT_DIR / "DejaVuSans-Bold.ttf"), size)
    line_h = int(size * 1.35)

    wrapped = [[_wrap_cell(c) for c in row] for row in grid]
    ncols = len(headers)

    def _w(s: str, f) -> int:
        return int(f.getlength(s)) if s else 0

    col_w = [0] * ncols
    for ri, row in enumerate(wrapped):
        f = bold if ri == 0 else font
        for ci, lines in enumerate(row):
            col_w[ci] = max(col_w[ci], max(_w(ln, f) for ln in lines) + 2 * pad_x)
    row_h = [max(len(c) for c in row) * line_h + 2 * pad_y for row in wrapped]

    width, height = sum(col_w) + 1, sum(row_h) + 1
    img = Image.new("RGB", (width, height), colors["bg"])
    draw = ImageDraw.Draw(img)

    y = 0
    for ri, row in enumerate(wrapped):
        fill = colors["head_bg"] if ri == 0 else (colors["alt_bg"] if ri % 2 == 0 else colors["bg"])
        draw.rectangle([0, y, width, y + row_h[ri]], fill=fill)
        x = 0
        f = bold if ri == 0 else font
        color = colors["head_text"] if ri == 0 else colors["text"]
        for ci, lines in enumerate(row):
            for li, ln in enumerate(lines):
                draw.text((x + pad_x, y + pad_y + li * line_h), ln, font=f, fill=color)
            x += col_w[ci]
            draw.line([x, y, x, y + row_h[ri]], fill=colors["grid"], width=1)
        y += row_h[ri]
        draw.line([0, y, width, y], fill=colors["grid"], width=1)
    draw.rectangle([0, 0, width - 1, height - 1], outline=colors["grid"], width=1)

    if width > MAX_WIDTH_PX:
        ratio = MAX_WIDTH_PX / width
        img = img.resize((MAX_WIDTH_PX, max(1, int(height * ratio))), Image.LANCZOS)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path

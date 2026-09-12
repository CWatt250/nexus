"""tools/telegram_render — pipe-table detection + PNG rendering (no network)."""
from __future__ import annotations

from tools import telegram_render as tr

SIX_COL = """\
| Model | Type | Best For | Strengths | Tradeoffs | Where to Get It |
|---|---|---|---|---|---|
| **Qwen3-VL (235B A22B / 8B)** | Open-source VLM, general-purpose | Best overall open alternative to GPT-4o; runs locally down to 8B | Native image/region grounding, strong on OSWorld-G (~0.68), huge ecosystem | Largest variant needs multi-GPU | `ollama run qwen3` · HF/Qwen |
| **UI-TARS-2** (ByteDance) | Open-source VLM *trained specifically for GUI control* | Best at actual screen grounding | SOTA on OSWorld & ScreenSpot-family | Heavier to run (7B/27B/67B) | HF: ByteDance-Seed/UI-TARS |
"""

DOC = f"Here's where things stand:\n\n{SIX_COL}\n### Quick take\n- pick UI-TARS-2\n"


def test_split_tables_detects_and_removes():
    text, tables = tr.split_tables(DOC)
    assert len(tables) == 1
    assert tables[0].startswith("| Model |")
    assert "|" not in text
    assert "Quick take" in text and "Here's where things stand:" in text


def test_split_tables_ignores_pipes_without_separator():
    text, tables = tr.split_tables("a | b\nc | d\n")
    assert tables == [] and "a | b" in text


def test_prepare_for_telegram_placeholder_and_png(tmp_path):
    text, pngs = tr.prepare_for_telegram(DOC, out_dir=tmp_path)
    assert tr.PLACEHOLDER in text and "|" not in text
    assert len(pngs) == 1 and pngs[0].endswith(".png")


def test_prepare_with_captions_uses_header_cells(tmp_path):
    _, pngs = tr.prepare_with_captions(DOC, out_dir=tmp_path)
    assert pngs[0][1] == "Model · Type · Best For · Strengths"


def test_parse_strips_markers():
    headers, rows = tr.parse_table(SIX_COL)
    assert headers[0] == "Model"
    assert rows[0][0] == "Qwen3-VL (235B A22B / 8B)"
    assert rows[1][1] == "Open-source VLM trained specifically for GUI control"
    assert rows[0][5] == "ollama run qwen3 · HF/Qwen"


def test_wrap_cell_respects_max_width():
    lines = tr._wrap_cell("x" * 100)
    assert all(len(ln) <= tr.MAX_CELL_CHARS for ln in lines) and len(lines) >= 4
    assert tr._wrap_cell("a<br>b".replace("<br>", "\n")) == ["a", "b"]


def test_six_col_renders_under_cap_and_non_blank(tmp_path):
    from PIL import Image
    out = tr.render_table_png(SIX_COL, tmp_path / "t.png", theme="dark")
    img = Image.open(out)
    assert img.width <= tr.MAX_WIDTH_PX and img.height > 50
    colors = img.convert("RGB").getcolors(maxcolors=1_000_000)
    assert colors and len(colors) > 10  # text + grid + shading, not a flat fill


def test_light_theme_renders(tmp_path):
    out = tr.render_table_png(SIX_COL, tmp_path / "l.png", theme="light")
    assert out.exists()

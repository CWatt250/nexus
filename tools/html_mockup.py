"""HTML mockup generator — describe a UI, get a standalone HTML file + a
rendered PNG preview.

Fully local: the brain (Ornith via core/brain) writes a single self-contained
HTML file (inline CSS, no external deps), then headless Chromium (Playwright,
already used by browser_render) screenshots it so Nexus can SHOW the mockup,
not just hand over code.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from langchain_core.tools import tool

from core import brain

log = logging.getLogger("nexus.html_mockup")

OUTPUT_DIR = Path.home() / "AI_Agent" / "output" / "mockups"

_SYSTEM = (
    "You are a senior product designer + front-end engineer. Given a UI "
    "description, output ONE complete, self-contained HTML document that "
    "implements it as a polished, modern mockup. Rules: all CSS inline in a "
    "<style> tag, NO external resources (no CDN, no <img src=http...>, no web "
    "fonts) — use system fonts, CSS gradients, emoji, and inline SVG for any "
    "imagery. Realistic placeholder copy, sensible spacing, accessible "
    "contrast. Output ONLY the HTML, starting at <!DOCTYPE html>. No markdown, "
    "no commentary."
)

_FENCE_RE = re.compile(r"```(?:html)?\s*(.*?)\s*```", re.DOTALL)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "mockup").lower()).strip("-")
    return (s or "mockup")[:40]


def _extract_html(body: str) -> str:
    """Pull the HTML document out of the model reply, tolerating fences/prose."""
    if not body:
        return ""
    m = _FENCE_RE.search(body)
    if m:
        body = m.group(1)
    i = body.lower().find("<!doctype")
    if i == -1:
        i = body.lower().find("<html")
    return body[i:].strip() if i != -1 else body.strip()


def _generate_html(description: str) -> str:
    raw = brain.chat(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Mockup to build:\n{description}"},
        ],
        options={"temperature": 0.4, "num_ctx": 8192, "num_predict": 6144},
        timeout=180.0,
    )
    return _extract_html(raw)


def _screenshot_html(html_path: Path, png_path: Path,
                     width: int = 1280, height: int = 900) -> bool:
    """Render a local HTML file to a full-page PNG via headless Chromium.
    Returns True on success."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=2).new_page()
                page.goto(html_path.as_uri(), wait_until="networkidle",
                          timeout=20000)
                page.screenshot(path=str(png_path), full_page=True)
            finally:
                browser.close()
        return png_path.exists()
    except Exception as exc:
        log.warning("mockup screenshot failed: %s", exc)
        return False


def html_mockup_core(description: str, name: str | None = None) -> dict:
    """Generate an HTML mockup + PNG preview. Returns
    {ok, html_path, png_path, error}. Core entrypoint for the @tool and for
    callers (e.g. Telegram) that want to send the files."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html = _generate_html(description)
    if not html or "<" not in html:
        return {"ok": False, "error": "model did not return HTML",
                "html_path": None, "png_path": None}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{_slug(name or description)}"
    html_path = OUTPUT_DIR / f"{base}.html"
    png_path = OUTPUT_DIR / f"{base}.png"
    html_path.write_text(html, encoding="utf-8")
    shot_ok = _screenshot_html(html_path, png_path)
    return {
        "ok": True,
        "html_path": str(html_path),
        "png_path": str(png_path) if shot_ok else None,
        "error": None if shot_ok else "screenshot failed (HTML still saved)",
    }


@tool
def html_mockup(description: str, name: str = "") -> str:
    """Generate a standalone HTML UI mockup from a text description and render
    a PNG preview of it.

    Use for wireframes, landing pages, dashboards, component mockups, slide
    layouts — anything you'd sketch in HTML/CSS. The output is a single
    self-contained .html file (no external deps) plus a screenshot.

    Args:
        description: What to mock up (layout, sections, style, content).
        name: Optional short name for the output files.

    Returns:
        The saved .html path and .png preview path, or an error string.
    """
    res = html_mockup_core(description, name or None)
    if not res["ok"]:
        return f"mockup failed: {res['error']}"
    out = f"Mockup created:\n- HTML: {res['html_path']}"
    if res["png_path"]:
        out += f"\n- Preview PNG: {res['png_path']}"
    else:
        out += f"\n- (preview render failed: {res['error']})"
    return out


HTML_MOCKUP_TOOLS = [html_mockup]

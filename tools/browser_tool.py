"""Browser tool for Nexus — Playwright-backed fetch that returns visible text.

Two modes:

  1. Default fast path: `wait_until="domcontentloaded"`. Cheap. Works for
     server-rendered pages.

  2. Auto-escalate: when the URL is on the known JS-heavy SPA list (X,
     LinkedIn, Instagram, Threads, Facebook, TikTok, ...) OR the fast
     path returns a suspiciously empty body (TITLE empty AND <200 chars
     of text), retry with `browser_render`'s networkidle path.

The escalation calls `browser_render.render_url` directly (not the
@tool-wrapped fallback path) so we can't loop back into `browser_tool`.
"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

log = logging.getLogger("nexus.browser_tool")

MAX_TEXT_CHARS = 20_000
TIMEOUT_MS = 20_000

# An output looks empty when the title is blank AND there's almost no
# visible text. Tuned conservatively so we don't escalate for tiny but
# legitimate static pages.
EMPTY_BODY_THRESHOLD = 200


def _fast_fetch(url: str) -> tuple[str, str]:
    """Single-shot Playwright fetch that waits only for DOMContentLoaded.
    Returns (title, text). Raises on connection / nav errors."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            title = page.title()
            text = page.inner_text("body")
        finally:
            browser.close()
    return title, text


def _format(url: str, title: str, text: str) -> str:
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + f"\n\n[truncated — page has {len(text)} chars]"
    return f"URL: {url}\nTITLE: {title}\n\n{text}"


def _looks_empty(title: str, text: str) -> bool:
    return not (title or "").strip() and len((text or "").strip()) < EMPTY_BODY_THRESHOLD


def _escalate_to_render(url: str, reason: str) -> str:
    """Call browser_render's plain render path. If THAT raises (Playwright
    missing, navigation error, etc.) return a plain error string — never
    re-enter browser_tool from here, that would loop."""
    log.info("browser_tool escalating to browser_render: %s url=%s", reason, url)
    try:
        from tools.browser_render import render_url  # noqa: PLC0415
        out = render_url(url)
        return f"[browser_tool auto-escalated to browser_render: {reason}]\n\n{out}"
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


@tool
def browser_tool(url: str) -> str:
    """Open `url` in a headless Chromium browser and return the visible page
    text (truncated to 20k chars). Fast path waits for DOMContentLoaded.

    Auto-escalates to `browser_render` (Playwright + networkidle) when:
    - the URL is on the known JS-heavy SPA list (X/Twitter, LinkedIn,
      Instagram, Threads, Facebook, TikTok), or
    - the fast fetch returns an empty title and < 200 chars of body.

    Use `browser_render` directly when you already know the page is a SPA.
    """
    from tools.browser_render import is_js_heavy_url  # noqa: PLC0415

    # Short-circuit: known JS-heavy domain — skip the doomed fast path.
    if is_js_heavy_url(url):
        return _escalate_to_render(url, "JS-heavy domain")

    try:
        title, text = _fast_fetch(url)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"

    if _looks_empty(title, text):
        return _escalate_to_render(url, "empty fast-fetch (likely client-rendered)")

    return _format(url, title, text)

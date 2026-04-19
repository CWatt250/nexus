"""Browser tool for Nexus — Playwright-backed fetch that returns visible text."""
from __future__ import annotations

from langchain_core.tools import tool

MAX_TEXT_CHARS = 20_000
TIMEOUT_MS = 20_000


@tool
def browser_tool(url: str) -> str:
    """Open `url` in a headless Chromium browser and return the visible page
    text (truncated to 20k chars). Use this to read web pages, docs, or
    anything that requires JavaScript to render.
    """
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
                title = page.title()
                text = page.inner_text("body")
            finally:
                browser.close()
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"

    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + f"\n\n[truncated — page has {len(text)} chars]"
    return f"URL: {url}\nTITLE: {title}\n\n{text}"

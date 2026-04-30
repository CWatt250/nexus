"""Playwright-rendered fetch for JS-heavy sites (X/Twitter, LinkedIn,
Instagram, Threads, Facebook, TikTok, ...).

`browser_tool` already runs Chromium but only waits for `domcontentloaded`,
which fires before any client-rendered SPA paints its first byte. That's
why the X.com lookup that triggered Fix #2 came back with TITLE empty
and zero body content. `browser_render` waits for `networkidle` (or a
caller-supplied selector), then pulls visible text + meta + final URL.

Falls back to `browser_tool` if Playwright isn't installed or the
render call raises so the agent always gets *some* answer.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

from langchain_core.tools import tool

log = logging.getLogger("nexus.browser_render")

# Domains where the basic browser_tool path (DOMContentLoaded only)
# returns an empty body. browser_tool consults this to short-circuit
# straight to the Playwright render path.
JS_HEAVY_DOMAINS: frozenset[str] = frozenset({
    "x.com", "twitter.com", "mobile.twitter.com",
    "linkedin.com",
    "instagram.com", "www.instagram.com",
    "threads.net", "www.threads.net",
    "facebook.com", "www.facebook.com", "m.facebook.com",
    "tiktok.com", "www.tiktok.com",
})


def is_js_heavy_url(url: str) -> bool:
    """True if the URL's host (or any parent host) is on the known
    JS-heavy SPA list. Strips `www.`, `m.`, and `mobile.` prefixes so
    `https://www.x.com/...` and `https://x.com/...` both match."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    if host in JS_HEAVY_DOMAINS:
        return True
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix) and host[len(prefix):] in JS_HEAVY_DOMAINS:
            return True
    # Also catch parent-of-subdomain matches: business.linkedin.com → linkedin.com
    parts = host.split(".")
    for i in range(len(parts) - 1):
        if ".".join(parts[i:]) in JS_HEAVY_DOMAINS:
            return True
    return False

MAX_TEXT_CHARS = 20_000
DEFAULT_TIMEOUT_MS = 30_000
# Pretend to be a real desktop Chrome — X/LinkedIn aggressively cloak the
# bot UA and serve a login wall to anything that smells like headless.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _truncate(text: str) -> str:
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS] + f"\n\n[truncated — page has {len(text)} chars]"
    return text


def _fallback(url: str, reason: str) -> str:
    """Best-effort fallback to the simpler browser_tool. If that also
    fails, return a plain error string so the agent can keep going."""
    log.info("browser_render falling back to browser_tool: %s", reason)
    try:
        from tools.browser_tool import browser_tool  # noqa: PLC0415
        out = browser_tool.invoke({"url": url})
        return f"[browser_render fell back: {reason}]\n\n{out}"
    except Exception as exc:
        return f"ERROR: browser_render failed ({reason}); fallback also failed: {exc}"


def _render_sync(url: str, wait_for_selector: Optional[str], timeout_ms: int) -> str:
    """Sync Playwright path. Returns formatted text or raises on failure
    so `browser_render` can decide whether to fall back."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=USER_AGENT, locale="en-US")
            page = context.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            # Wait for the network to quiet down — covers most SPAs. Don't
            # raise if it doesn't settle; we still want whatever rendered.
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:
                pass
            if wait_for_selector:
                try:
                    page.wait_for_selector(wait_for_selector, timeout=timeout_ms)
                except Exception:
                    pass
            title = page.title() or ""
            final_url = page.url
            description = ""
            try:
                description = page.locator(
                    'meta[property="og:description"], meta[name="description"]'
                ).first.get_attribute("content") or ""
            except Exception:
                description = ""
            try:
                body_text = page.inner_text("body")
            except Exception:
                body_text = page.content()
        finally:
            browser.close()

    body_text = _truncate(body_text or "")
    bits = [f"URL: {url}", f"FINAL_URL: {final_url}", f"TITLE: {title}"]
    if description:
        bits.append(f"DESCRIPTION: {description}")
    bits.append("")
    bits.append(body_text)
    return "\n".join(bits)


def render_url(url: str, *, wait_for_selector: Optional[str] = None,
               timeout_seconds: int = 30) -> str:
    """Plain (non-@tool) render entrypoint that raises on failure.

    `browser_tool` uses this when it auto-escalates a JS-heavy URL — it
    skips the @tool fallback path so we can't loop back into
    `browser_tool` from inside `browser_tool`.
    """
    timeout_ms = max(5, min(int(timeout_seconds), 120)) * 1000
    sel = (wait_for_selector or "").strip() or None
    return _render_sync(url, sel, timeout_ms)


@tool
def browser_render(url: str, wait_for_selector: str = "", timeout: int = 30) -> str:
    """Render a JS-heavy URL with headless Chromium and return visible text.

    Use this for X/Twitter, LinkedIn, Instagram, Threads, Facebook, TikTok,
    and any other client-rendered SPA where `browser_tool` returns an empty
    body. Waits for `networkidle` after `domcontentloaded` so React/Vue/etc
    has time to paint. Returns URL + final URL (after redirects) + TITLE +
    meta description + visible body text, truncated to ~20k chars.

    Args:
        url: page URL to fetch.
        wait_for_selector: optional CSS selector to wait for before reading
            text. Useful when you know which element holds the content
            (e.g. `article` for X posts).
        timeout: per-step timeout in seconds. Default 30.

    Falls back to `browser_tool` (and ultimately `web_fetch`-style behavior)
    if Playwright isn't available or the render raises. Always returns a
    string — never raises.
    """
    timeout_ms = max(5, min(int(timeout), 120)) * 1000
    sel = (wait_for_selector or "").strip() or None
    try:
        return _render_sync(url, sel, timeout_ms)
    except ImportError as exc:
        return _fallback(url, f"playwright not installed: {exc}")
    except Exception as exc:
        return _fallback(url, f"{type(exc).__name__}: {exc}")


BROWSER_RENDER_TOOLS = [browser_render]

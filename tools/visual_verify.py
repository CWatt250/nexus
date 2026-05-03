"""Phase 28 — visual verification for HTML artifacts.

After a /code, /pro, /real, or smart-routed cloud build produces an
HTML file, this module:

  1. Renders the file in headless Chromium via Playwright.
  2. Captures a 1280x720 screenshot to ~/AI_Agent/cc_artifacts/.
  3. Asks qwen2.5vl (vision_tool.describe_image_core) what's visible
     and whether anything looks broken.
  4. Returns a verdict dict the dispatcher folds into the
     DispatchResult so the reporter can flag needs_review + attach
     the screenshot to Telegram.

Best-effort: any failure (Playwright import, browser crash, vision
miss) returns needs_review=False with notes describing the skip.
The dispatcher catches exceptions on top of that, so this never
blocks the dispatch from completing normally.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("nexus.visual_verify")

ARTIFACT_DIR = Path.home() / "AI_Agent" / "cc_artifacts"

# We ask the vision model to end its response with exactly CLEAN or
# BROKEN on the last line. A loose-keyword scan was tried first but
# false-positived constantly because the model echoed the question's
# vocabulary back in negated form ("no missing text", "no overlapping").
_VERDICT_RE = re.compile(r"\b(CLEAN|BROKEN)\b\s*\.?\s*$", re.IGNORECASE)

# Override signals: positive descriptions of broken state (NOT the
# prompt's "look for missing/etc.") that should flip a CLEAN verdict
# to needs_review. These are unambiguous because they describe what's
# IN the image, not what to look for.
_BROKEN_DESCRIPTION_RE = re.compile(
    r"\b("
    r"blank\s+page|empty\s+page|no\s+visible\s+(?:content|elements|text)|"
    r"completely\s+white\s+(?:page|background)|completely\s+blank|"
    r"shows\s+nothing|page\s+is\s+empty|appears\s+(?:to\s+be\s+)?(?:blank|empty)|"
    r"garbage\s+characters?|garbled\s+text|unreadable\s+text|"
    r"severely\s+overlapping|broken\s+layout"
    r")\b",
    re.IGNORECASE,
)


def _screenshot(html_path: Path, out_path: Path,
                viewport=(1280, 720), wait_ms: int = 1500) -> Optional[str]:
    """Render the HTML in headless Chromium + save a PNG. Returns the
    saved path, or an error string starting with 'screenshot:'."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except Exception as exc:
        return f"screenshot: playwright unavailable ({exc})"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(viewport={
                    "width": viewport[0], "height": viewport[1],
                })
                page = ctx.new_page()
                page.goto(html_path.resolve().as_uri(), wait_until="domcontentloaded",
                          timeout=15000)
                # Give JS / CSS time to settle (clocks animate, charts render).
                page.wait_for_timeout(wait_ms)
                page.screenshot(path=str(out_path), full_page=False)
            finally:
                browser.close()
    except Exception as exc:
        return f"screenshot: {type(exc).__name__}: {exc}"
    return str(out_path)


def _verdict_from_text(vision_text: str) -> tuple[bool, str]:
    """Pluck the trailing CLEAN / BROKEN verdict the model is asked to
    emit. Returns (needs_review, verdict_string). Layered:
      1. Description-level override: if the prose contains an
         unambiguous broken signal ("blank page", "garbage characters",
         etc.), flip to needs_review regardless of the verdict — the
         vision model is inconsistent and sometimes describes a
         broken page then issues CLEAN anyway.
      2. Trailing CLEAN/BROKEN verdict from the last line(s).
      3. No verdict found → default clean (false-negatives beat
         chronic false-positives that train the user to ignore the flag)."""
    text = (vision_text or "").strip()
    if not text:
        return False, "empty"
    if _BROKEN_DESCRIPTION_RE.search(text):
        return True, "BROKEN-DESCRIPTION"
    tail = [l.strip() for l in text.splitlines() if l.strip()][-3:]
    for line in reversed(tail):
        m = _VERDICT_RE.search(line)
        if m:
            verdict = m.group(1).upper()
            return verdict == "BROKEN", verdict
    return False, "no-verdict"


def verify_html_artifact(html_path_str: str) -> dict:
    """Render + describe an HTML file. Returns:
      {
        "screenshot_path": str | "",      # absolute path or "" on failure
        "vision_summary":  str,           # qwen2.5vl description
        "needs_review":    bool,          # True when broken-signal hit
        "notes":           str,           # human-readable verdict
      }
    """
    html_path = Path(html_path_str).expanduser()
    out: dict = {
        "screenshot_path": "",
        "vision_summary": "",
        "needs_review": False,
        "notes": "",
    }
    if not html_path.exists():
        out["notes"] = f"verify: html missing at {html_path}"
        return out
    if not html_path.is_file():
        out["notes"] = f"verify: not a file at {html_path}"
        return out

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", html_path.stem)[:40]
    shot_path = ARTIFACT_DIR / f"{ts}_{safe_stem}.png"

    shot_result = _screenshot(html_path, shot_path)
    if shot_result is None or shot_result.startswith("screenshot:"):
        out["notes"] = shot_result or "screenshot: unknown failure"
        return out
    out["screenshot_path"] = shot_result

    try:
        from tools import vision_tool  # noqa: PLC0415
    except Exception as exc:
        out["notes"] = f"verify: vision_tool import failed ({exc})"
        return out

    prompt = (
        "Look at this screenshot of a web page and decide if it renders "
        "correctly. Briefly describe what's visible (1-2 sentences). "
        "Then on the LAST LINE of your response write exactly one word: "
        "CLEAN if the page has visible, readable, intentional content, "
        "or BROKEN for any of these problems:\n"
        "- The page is blank / empty / shows no content\n"
        "- Garbled or garbage characters\n"
        "- Severely overlapping text or controls\n"
        "- Obviously broken layout (elements off-screen, stacked wrong)\n"
        "- Visible error messages instead of the intended UI\n"
        "Minor cosmetic issues do not count as BROKEN. The last line "
        "must be only the word CLEAN or BROKEN, nothing else."
    )
    try:
        from tools.vision_tool import _vision_chat, _read_image_bytes  # noqa: PLC0415
        import base64
        raw = _read_image_bytes(shot_result)
        if raw is None:
            out["notes"] = "verify: screenshot saved but unreadable"
            return out
        b64 = base64.b64encode(raw).decode("ascii")
        vision_summary = _vision_chat(prompt, b64, num_predict=300)
    except Exception as exc:
        vision_summary = f"vision call failed: {type(exc).__name__}: {exc}"
    out["vision_summary"] = vision_summary

    needs_review, verdict = _verdict_from_text(vision_summary)
    out["needs_review"] = needs_review
    out["notes"] = (
        f"verdict={verdict} :: " + vision_summary[:240].replace("\n", " ")
    )
    return out


def verify_html_artifact_safe(html_path_str: str) -> dict:
    """Catch-all wrapper. Always returns the same shape so callers can
    treat it as infallible."""
    try:
        return verify_html_artifact(html_path_str)
    except Exception as exc:
        log.exception("visual_verify crashed on %s: %s", html_path_str, exc)
        return {
            "screenshot_path": "", "vision_summary": "",
            "needs_review": False,
            "notes": f"verify: crashed ({type(exc).__name__}: {exc})",
        }


__all__ = ["verify_html_artifact", "verify_html_artifact_safe"]

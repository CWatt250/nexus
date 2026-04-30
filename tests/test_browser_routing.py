"""Smart-routing tests for browser_tool / browser_render.

Covers:
- `is_js_heavy_url` recognises x.com, twitter.com, www.x.com, m.facebook.com,
  business subdomains, and rejects regular static hosts.
- `browser_tool` short-circuits straight to render for JS-heavy domains
  (no fast-fetch attempt at all).
- `browser_tool` escalates when the fast fetch returns an empty body.
- `browser_tool` does NOT escalate when the fast fetch returns content.
"""
from __future__ import annotations

import pytest


# --- 1. is_js_heavy_url -------------------------------------------------
@pytest.mark.parametrize("url,expected", [
    ("https://x.com/anyone/status/123", True),
    ("https://www.x.com/anyone/status/123", True),
    ("https://twitter.com/anyone/status/123", True),
    ("https://mobile.twitter.com/anyone", True),
    ("https://www.linkedin.com/in/colton", True),
    ("https://business.linkedin.com/talent-solutions", True),
    ("https://www.instagram.com/p/abc", True),
    ("https://threads.net/@user/post/1", True),
    ("https://www.facebook.com/page", True),
    ("https://m.facebook.com/page", True),
    ("https://www.tiktok.com/@user/video/1", True),
    # negatives
    ("https://example.com", False),
    ("https://github.com/CWatt250", False),
    ("https://wikipedia.org/wiki/X", False),
    ("https://news.ycombinator.com", False),
    ("not a url", False),
    ("", False),
])
def test_is_js_heavy_url(url: str, expected: bool) -> None:
    from tools.browser_render import is_js_heavy_url
    assert is_js_heavy_url(url) is expected


# --- 2. browser_tool short-circuits JS-heavy URLs to render ---------------
def test_browser_tool_skips_fast_path_for_js_heavy(monkeypatch) -> None:
    from tools import browser_tool as bt
    fast_calls = []
    render_calls = []

    def fake_fast(url: str):
        fast_calls.append(url)
        raise AssertionError("fast path should NOT run for JS-heavy domain")

    def fake_render(url: str):
        render_calls.append(url)
        return "URL: x.com/foo\nFINAL_URL: x.com/foo\nTITLE: Foo\n\nbody body body"

    monkeypatch.setattr(bt, "_fast_fetch", fake_fast)
    # Patch the import target inside the escalate helper.
    import tools.browser_render as br
    monkeypatch.setattr(br, "render_url", fake_render)

    out = bt.browser_tool.invoke({"url": "https://x.com/foo/status/1"})
    assert fast_calls == []
    assert render_calls == ["https://x.com/foo/status/1"]
    assert "auto-escalated" in out
    assert "body body body" in out


# --- 3. browser_tool escalates on empty fast-fetch result -----------------
def test_browser_tool_escalates_on_empty_body(monkeypatch) -> None:
    from tools import browser_tool as bt
    import tools.browser_render as br

    monkeypatch.setattr(bt, "_fast_fetch", lambda url: ("", ""))
    captured: list[str] = []

    def fake_render(url: str):
        captured.append(url)
        return "URL: foo\nFINAL_URL: foo\nTITLE: After Render\n\nfull rendered body"

    monkeypatch.setattr(br, "render_url", fake_render)

    out = bt.browser_tool.invoke({"url": "https://some-spa.example/page"})
    assert captured == ["https://some-spa.example/page"]
    assert "auto-escalated" in out
    assert "empty fast-fetch" in out
    assert "full rendered body" in out


# --- 4. browser_tool does NOT escalate when content is real ---------------
def test_browser_tool_uses_fast_path_when_content_present(monkeypatch) -> None:
    from tools import browser_tool as bt
    import tools.browser_render as br

    monkeypatch.setattr(bt, "_fast_fetch", lambda url: ("Example Domain", "x" * 500))

    def must_not_call(url):
        raise AssertionError("render_url should not be called for healthy fast-fetch")

    monkeypatch.setattr(br, "render_url", must_not_call)

    out = bt.browser_tool.invoke({"url": "https://example.com"})
    assert "TITLE: Example Domain" in out
    assert "auto-escalated" not in out

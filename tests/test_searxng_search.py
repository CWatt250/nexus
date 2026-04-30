"""Tests for tools/searxng_tool.py and tools/search_router.py.

The container itself is not assumed to be running — every network seam
is monkeypatched. The integration smoke is a separate manual run.
"""
from __future__ import annotations

import os

import pytest


# --- 1. searxng_search formats results ----------------------------------
def test_searxng_search_formats_results(monkeypatch) -> None:
    from tools import searxng_tool

    fake_payload = {
        "query": "weather Pasco WA",
        "results": [
            {
                "url": "https://weather.com/x",
                "title": "Pasco WA Forecast",
                "content": "Sunny, 72F",
                "engine": "google",
            },
            {
                "url": "https://weather.gov/y",
                "title": "NWS Pasco",
                "content": "Latest observations",
                "engine": "duckduckgo",
            },
        ],
    }
    monkeypatch.setattr(searxng_tool, "_request", lambda q, c, **kw: fake_payload)

    out = searxng_tool.searxng_search.invoke({"query": "weather Pasco WA", "count": 5})
    assert "Pasco WA Forecast" in out
    assert "https://weather.com/x" in out
    assert "[google]" in out
    assert "[duckduckgo]" in out


# --- 2. searxng_search returns ERROR on connect failure ------------------
def test_searxng_search_handles_connect_error(monkeypatch) -> None:
    from tools import searxng_tool

    def boom(*args, **kwargs):
        import httpx
        raise httpx.ConnectError("conn refused")

    # Patch the http client so the @tool wrapper sees a connect failure.
    monkeypatch.setattr(searxng_tool.httpx, "Client", lambda **kw: _BadClient())

    out = searxng_tool.searxng_search.invoke({"query": "anything", "count": 3})
    assert out.startswith("ERROR")
    assert "127.0.0.1:8888" in out


class _BadClient:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def get(self, *a, **kw):
        import httpx
        raise httpx.ConnectError("conn refused")


# --- 3. searxng_health returns 'ok' on healthy ---------------------------
def test_searxng_health_ok(monkeypatch) -> None:
    from tools import searxng_tool

    class _Resp:
        status_code = 200
        def json(self): return {"results": [{"title": "x", "url": "y", "content": "z"}]}

    class _OkClient:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, *a, **kw): return _Resp()

    monkeypatch.setattr(searxng_tool.httpx, "Client", lambda **kw: _OkClient())
    assert searxng_tool.searxng_health.invoke({}) == "ok"


def test_searxng_health_reports_down(monkeypatch) -> None:
    from tools import searxng_tool
    monkeypatch.setattr(searxng_tool.httpx, "Client", lambda **kw: _BadClient())
    out = searxng_tool.searxng_health.invoke({})
    assert out.startswith("down")


# --- 4. web_search router prefers SearXNG when no keys present -----------
def test_web_search_picks_searxng_when_no_api_keys(monkeypatch) -> None:
    from tools import search_router

    monkeypatch.setattr(search_router, "_has_tavily", lambda: False)
    monkeypatch.setattr(search_router, "_has_brave", lambda: False)
    called = []
    monkeypatch.setattr(
        search_router, "_call_searxng",
        lambda q, c: (called.append("sx") or "- result via searxng"),
    )
    out = search_router.web_search.invoke({"query": "anything", "count": 3})
    assert called == ["sx"]
    assert out.startswith("[search:searxng]")
    assert "result via searxng" in out


# --- 5. web_search router prefers Brave over SearXNG when key present ----
def test_web_search_prefers_brave_when_key_present(monkeypatch) -> None:
    from tools import search_router

    monkeypatch.setattr(search_router, "_has_tavily", lambda: False)
    monkeypatch.setattr(search_router, "_has_brave", lambda: True)
    monkeypatch.setattr(search_router, "_call_brave", lambda q, c: "- result via brave")
    must_not_call = lambda q, c: pytest.fail("searxng must not be called when brave is up")
    monkeypatch.setattr(search_router, "_call_searxng", must_not_call)

    out = search_router.web_search.invoke({"query": "x", "count": 3})
    assert out.startswith("[search:brave]")


# --- 6. web_search falls through Brave error to SearXNG ------------------
def test_web_search_falls_through_brave_error_to_searxng(monkeypatch) -> None:
    from tools import search_router

    monkeypatch.setattr(search_router, "_has_tavily", lambda: False)
    monkeypatch.setattr(search_router, "_has_brave", lambda: True)
    monkeypatch.setattr(
        search_router, "_call_brave",
        lambda q, c: "ERROR: Brave API rate-limited (429). Back off and retry.",
    )
    monkeypatch.setattr(
        search_router, "_call_searxng",
        lambda q, c: "- searxng saved the day",
    )

    out = search_router.web_search.invoke({"query": "x", "count": 3})
    assert out.startswith("[search:searxng]")
    assert "saved the day" in out


# --- 7. web_search prefers Tavily when key present (even though stubbed) -
def test_web_search_tavily_first_then_falls_to_brave(monkeypatch) -> None:
    """Today the Tavily slot returns ERROR (no client wired). The router
    should still try it first then fall through to Brave."""
    from tools import search_router

    monkeypatch.setattr(search_router, "_has_tavily", lambda: True)
    monkeypatch.setattr(search_router, "_has_brave", lambda: True)

    real_tavily = search_router._call_tavily
    order = []

    def tav(q, c):
        order.append("tav")
        return real_tavily(q, c)

    def br(q, c):
        order.append("br")
        return "- brave wins"

    monkeypatch.setattr(search_router, "_call_tavily", tav)
    monkeypatch.setattr(search_router, "_call_brave", br)

    out = search_router.web_search.invoke({"query": "x", "count": 3})
    assert order == ["tav", "br"]
    assert out.startswith("[search:brave]")


# --- 8. NEXUS_WEB_SEARCH_FORCE env var bypasses chain ---------------------
def test_web_search_force_env_pins_backend(monkeypatch) -> None:
    from tools import search_router

    monkeypatch.setenv("NEXUS_WEB_SEARCH_FORCE", "searxng")
    monkeypatch.setattr(search_router, "_has_tavily", lambda: True)
    monkeypatch.setattr(search_router, "_has_brave", lambda: True)
    monkeypatch.setattr(search_router, "_call_searxng", lambda q, c: "- forced searxng")

    out = search_router.web_search.invoke({"query": "x", "count": 1})
    assert out.startswith("[search:searxng]")
    assert "forced searxng" in out

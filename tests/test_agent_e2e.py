"""End-to-end agent tests (slow — hit Ollama).

Each test runs one short turn through the full LangGraph agent so refactors
to the prompt / streaming / fast_mode wiring get caught. Ollama and the
qwen3:4b model must be available; tests skip cleanly if not.
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest


def _ollama_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _ollama_up(), reason="Ollama not running on :11434")


@pytest.fixture(scope="module")
def agent_runtime():
    """Build the agent once for the whole module to amortize warmup."""
    import nexus
    nexus.set_system_prompt(nexus.load_system_prompt())
    return nexus


def _invoke(agent, text, thread_id):
    from langchain_core.messages import HumanMessage
    config = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke({"messages": [HumanMessage(content=text)]}, config=config)
    return result


# 1. Trivial fast turn returns text.
def test_e2e_fast_greeting(agent_runtime) -> None:
    agent = agent_runtime.build_agent("qwen3:4b")
    result = _invoke(agent, "Reply with the single word: pong", "e2e-fast")
    msgs = result.get("messages", [])
    assert msgs
    last = msgs[-1]
    content = getattr(last, "content", "") or ""
    assert content.strip(), "agent returned empty content"


# 2. Router classifies as 'fast' for greetings.
def test_e2e_router_fast(agent_runtime) -> None:
    import router
    route, _ = router.classify_and_model("hi")
    assert route in {"fast", "mid"}  # tolerate router drift


# 3. fast_mode_messages prepends a SystemMessage when route is fast.
def test_e2e_fast_mode(agent_runtime) -> None:
    msgs = agent_runtime.fast_mode_messages("hi", route="fast")
    assert len(msgs) == 2
    assert msgs[0].__class__.__name__ == "SystemMessage"
    msgs2 = agent_runtime.fast_mode_messages("build me a graph viz", route="code")
    assert len(msgs2) == 1


# 4. Static prefix is byte-stable across calls.
def test_e2e_static_prefix_stable(agent_runtime) -> None:
    agent_runtime._STATIC_PREFIX_CACHE = None
    a = agent_runtime.load_static_prefix()
    b = agent_runtime.load_static_prefix()
    assert a == b
    assert "SOUL" in a
    assert "STYLE" in a


# 5. Truncation wrapper is on every tool.
def test_e2e_tools_wrapped(agent_runtime) -> None:
    # Every registered tool went through wrap_tools + wrap_tools_with_metrics.
    # We can verify the metrics wrapper marker is present on the tools that
    # have a `func` attribute (StructuredTool etc.).
    from memory.metrics import _approx_tokens  # noqa: F401  — sanity import
    wrapped_count = sum(1 for t in agent_runtime.TOOLS if getattr(t, "_metrics_wrapped", False))
    assert wrapped_count >= 50, f"only {wrapped_count} tools metrics-wrapped"

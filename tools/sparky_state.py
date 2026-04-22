"""Sparky state-bridge client.

Fire-and-forget POST to the local state bridge at :11437/state so the
Electron overlay reflects agent activity without the caller having to
wait on HTTP. Also ships a LangChain `BaseCallbackHandler` so Sparky
follows tool-call lifecycle automatically.

If the bridge is down (nothing listening on :11437), calls fail
silently — the overlay is optional; the agent should never break
because of it."""
from __future__ import annotations

import logging
import threading
from typing import Any

import httpx
from langchain_core.callbacks import BaseCallbackHandler

STATE_URL = "http://localhost:11437/state"
POST_TIMEOUT = 2.0

log = logging.getLogger("nexus.sparky")


def post_state(state: str, message: str | None = None) -> None:
    """Async POST `{state, message?}` to the bridge. Errors swallowed."""
    body: dict[str, Any] = {"state": state}
    if message:
        body["message"] = str(message)[:200]

    def _send() -> None:
        try:
            with httpx.Client(timeout=POST_TIMEOUT) as client:
                client.post(STATE_URL, json=body)
        except Exception:
            pass

    threading.Thread(target=_send, name=f"sparky-{state}", daemon=True).start()


class SparkyCallbackHandler(BaseCallbackHandler):
    """Reflects LangGraph/LangChain agent activity to Sparky.

    Mapping:
      - top-level chain start → `thinking`
      - tool start            → `working` (with tool name)
      - tool error            → `error`
      - top-level chain end   → `happy`
      - top-level chain error → `error`

    Intermediate tool-end events are intentionally left alone — a multi-
    tool turn should stay in `working`, not flip back to `happy` between
    each step. The final `happy` / `idle` comes from `on_chain_end` or an
    explicit caller-side post at the end of the turn."""

    def _is_top_level(self, kwargs: dict) -> bool:
        return kwargs.get("parent_run_id") is None

    def on_chain_start(self, serialized: dict, inputs: Any, **kwargs: Any) -> None:
        if self._is_top_level(kwargs):
            post_state("thinking")

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:
        name = (serialized or {}).get("name") or "tool"
        post_state("working", message=name)

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        # Stay in working; next tool_start / chain_end will transition us.
        return None

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        post_state("error", message=str(error))

    def on_chain_end(self, outputs: Any, **kwargs: Any) -> None:
        if self._is_top_level(kwargs):
            post_state("happy")

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        if self._is_top_level(kwargs):
            post_state("error", message=str(error))

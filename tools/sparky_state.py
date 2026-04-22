"""Sparky state-bridge client.

Fire-and-forget POST to the local state bridge at :11437 so the Electron
overlay reflects agent activity without the caller having to wait on HTTP.
Also ships a LangChain `BaseCallbackHandler` that:

  * drives the state (thinking / working / error / happy) across the
    tool-call lifecycle, and
  * emits a deliverable preview `/card` when specific tools finish
    successfully (github_create_pr, github_commit_file, vercel_deploy,
    opengame_create, file_write_tool on "important" files, …).

If the bridge is down (nothing listening on :11437), calls fail silently
— the overlay is optional; the agent should never break because of it."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

import httpx
from langchain_core.callbacks import BaseCallbackHandler

BRIDGE = "http://localhost:11437"
STATE_URL = f"{BRIDGE}/state"
CARD_URL = f"{BRIDGE}/card"
POST_TIMEOUT = 2.0

log = logging.getLogger("nexus.sparky")

URL_RE = re.compile(r"https?://\S+")

# Paths beneath these roots are treated as "important" file-write targets.
_IMPORTANT_ROOTS = (
    Path.home() / "AI_Agent",
    Path.home() / "Dev",
    Path.home() / "Documents",
)
# Paths beneath these are uninteresting (runtime state, caches).
_NOISY_SUFFIXES = ("memory/", "chroma/", "__pycache__/", ".cache/", "venv/", "node_modules/")
_IMPORTANT_EXTS = {
    ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml",
    ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss",
    ".sh", ".sql", ".svg", ".go", ".rs",
}


# ---------------------------------------------------------------------------
# Fire-and-forget POST helpers
# ---------------------------------------------------------------------------

def _post(url: str, body: dict) -> None:
    def _send() -> None:
        try:
            with httpx.Client(timeout=POST_TIMEOUT) as client:
                client.post(url, json=body)
        except Exception:
            pass

    threading.Thread(target=_send, name="sparky-post", daemon=True).start()


def post_state(state: str, message: str | None = None) -> None:
    body: dict[str, Any] = {"state": state}
    if message:
        body["message"] = str(message)[:200]
    _post(STATE_URL, body)


def post_card(
    card_type: str,
    title: str,
    *,
    subtitle: str | None = None,
    action_url: str | None = None,
    action_label: str | None = None,
) -> None:
    body: dict[str, Any] = {"type": card_type, "title": title}
    if subtitle:
        body["subtitle"] = str(subtitle)[:200]
    if action_url:
        body["action_url"] = str(action_url)[:500]
    if action_label:
        body["action_label"] = str(action_label)[:40]
    _post(CARD_URL, body)


# ---------------------------------------------------------------------------
# Tool-output → card mapping
# ---------------------------------------------------------------------------

def _first_url(s: str) -> str | None:
    if not s:
        return None
    m = URL_RE.search(s)
    return m.group(0).rstrip(").,;\"'") if m else None


def _first_line(s: str, limit: int = 120) -> str:
    return (s or "").strip().splitlines()[0][:limit] if (s or "").strip() else ""


def _parse_file_write_input(input_str: str) -> str | None:
    """file_write_tool input looks like either a JSON dict or `path=..., content=...`
    — pull the path if we can."""
    if not input_str:
        return None
    s = input_str.strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            for k in ("path", "file_path", "filename"):
                v = obj.get(k)
                if isinstance(v, str) and v:
                    return v
    except (json.JSONDecodeError, ValueError):
        pass
    m = re.search(r"path\s*[:=]\s*['\"]?([^'\",\n]+)", s)
    if m:
        return m.group(1).strip()
    # Fallback: first token that looks like a path
    m = re.search(r"(/[^\s,'\"]+|~/[^\s,'\"]+)", s)
    return m.group(1) if m else None


def _is_important_path(path: str) -> bool:
    if not path:
        return False
    p = Path(path).expanduser().resolve() if not path.startswith("~") else Path(os.path.expanduser(path)).resolve()
    if any(part in _NOISY_SUFFIXES or (part + "/") in _NOISY_SUFFIXES for part in p.parts):
        return False
    if p.suffix.lower() not in _IMPORTANT_EXTS:
        return False
    return any(str(p).startswith(str(root)) for root in _IMPORTANT_ROOTS)


def _looks_like_error(output: str) -> bool:
    head = (output or "")[:40].upper()
    return head.startswith("ERROR") or head.startswith("BLOCKED") or head.startswith("TRACEBACK")


def _card_for(tool_name: str, input_str: str, output: str) -> dict | None:
    if not isinstance(output, str):
        output = str(output)
    if _looks_like_error(output):
        return None
    name = (tool_name or "").lower()
    subtitle = _first_line(output)
    url = _first_url(output)

    if name == "github_create_pr":
        return {"card_type": "github", "title": "✅ PR opened",
                "subtitle": subtitle, "action_url": url, "action_label": "View"}
    if name == "github_commit_file":
        return {"card_type": "github", "title": "✅ Code committed", "subtitle": subtitle}
    if name == "github_create_repo":
        return {"card_type": "github", "title": "✅ Repo created",
                "subtitle": subtitle, "action_url": url, "action_label": "View"}
    if name == "github_create_issue":
        return {"card_type": "github", "title": "✅ Issue opened",
                "subtitle": subtitle, "action_url": url, "action_label": "View"}
    if name in ("vercel_deploy", "vercel_ship"):
        return {"card_type": "url", "title": "✅ Deployed",
                "subtitle": subtitle, "action_url": url, "action_label": "Play"}
    if name in ("opengame_create", "game_pipeline_build"):
        return {"card_type": "url", "title": "✅ Game ready",
                "subtitle": subtitle, "action_url": url, "action_label": "Play"}
    if name == "file_write_tool":
        path = _parse_file_write_input(input_str)
        if path and _is_important_path(path):
            return {"card_type": "file", "title": "✅ File saved",
                    "subtitle": path, "action_url": str(Path(path).expanduser()),
                    "action_label": "Open"}
    return None


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

class SparkyCallbackHandler(BaseCallbackHandler):
    """Reflects LangGraph/LangChain agent activity to Sparky.

    State mapping:
      - top-level chain start → thinking
      - tool start            → working (with tool name)
      - tool error            → error
      - top-level chain end   → happy
      - top-level chain error → error

    Intermediate tool-end events keep the state in `working` (so a
    multi-tool turn doesn't flicker happy/working) but they do fire a
    deliverable preview `/card` when the tool name matches a known
    result-producing action (see `_card_for`)."""

    def __init__(self) -> None:
        super().__init__()
        # run_id → {"name": ..., "input": ...} so on_tool_end can attribute
        # the output to the tool that was invoked.
        self._runs: dict[str, dict[str, Any]] = {}

    def _is_top_level(self, kwargs: dict) -> bool:
        return kwargs.get("parent_run_id") is None

    def on_chain_start(self, serialized: dict, inputs: Any, **kwargs: Any) -> None:
        if self._is_top_level(kwargs):
            post_state("thinking")

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:
        name = (serialized or {}).get("name") or "tool"
        run_id = kwargs.get("run_id")
        if run_id is not None:
            self._runs[str(run_id)] = {"name": name, "input": input_str or ""}
        post_state("working", message=name)

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        meta = self._runs.pop(str(run_id), None) if run_id is not None else None
        if not meta:
            return None
        try:
            card = _card_for(meta["name"], meta.get("input", ""), output)
            if card:
                post_card(**card)
        except Exception as exc:
            log.debug("card emission failed for %s: %s", meta.get("name"), exc)
        return None

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        self._runs.pop(str(run_id), None) if run_id is not None else None
        post_state("error", message=str(error))

    def on_chain_end(self, outputs: Any, **kwargs: Any) -> None:
        if self._is_top_level(kwargs):
            post_state("happy")

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        if self._is_top_level(kwargs):
            post_state("error", message=str(error))

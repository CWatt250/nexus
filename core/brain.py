"""Phase 39 — central brain model access.

One place that knows (a) which local model is the conversation/routing
brain, (b) how to suppress chain-of-thought for each model family, and
(c) how to degrade to qwen3:4b when the big model is unavailable
(evicted under memory pressure, mid-pull, Ollama restart).

Think suppression contract (verified on Ollama 0.21.0, 2026-06-11):
  - gpt-oss models can NOT disable thinking; they accept effort levels
    ("low"/"medium"/"high"). With think set, reasoning arrives in the
    separate `message.thinking` field — we read `message.content` only,
    so the monologue is discarded at the source.
  - qwen3:4b with think=False leaks raw CoT INTO content (the old
    in-code claim is confirmed). think=True diverts CoT to the
    `thinking` field but burns the whole num_predict budget on it and
    500s when combined with format=json — so the degraded path keeps
    think=False + the Phase 30 scrubber pipeline as backstop.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("nexus.brain")

ROOT = Path.home() / "AI_Agent"
MODELS_FILE = ROOT / "models.json"
OLLAMA_URL = "http://localhost:11434"

# Set after the Phase 39 acceptance benchmark (>=25 t/s decode, TTFT <4s
# on router prompts, no OOM with qwen2.5vl co-resident). models.json
# "brain" key overrides so the value can change without a code deploy.
DEFAULT_BRAIN_MODEL = "gpt-oss:120b"
# Explicit offline/degraded fallback ONLY (big model evicted, Ollama
# mid-restart). Not a quality tier — a liveness tier.
DEGRADED_MODEL = "qwen3:4b"


def get_brain_model() -> str:
    """Brain model id — models.json `brain` key, else the default."""
    try:
        data = json.loads(MODELS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("brain"):
            return str(data["brain"])
    except (OSError, json.JSONDecodeError):
        pass
    return DEFAULT_BRAIN_MODEL


def think_param(model: str):
    """Per-family CoT suppression value for the Ollama `think` param.

    gpt-oss → "low" (can't disable; lowest effort, separate field).
    Everything else → False (and the scrubber backstop catches leaks).
    """
    if (model or "").startswith("gpt-oss"):
        return "low"
    return False


def chat(messages: list[dict], *, model: str | None = None,
         fmt=None, options: dict | None = None, timeout: float = 120.0,
         allow_degraded: bool = True) -> str:
    """One-shot chat against the brain with CoT discarded at the source.

    Returns message.content ONLY — message.thinking is never read into
    the result. On any primary-model failure, retries once on
    DEGRADED_MODEL (qwen3:4b) unless allow_degraded=False.
    """
    primary = model or get_brain_model()
    try:
        return _call(primary, messages, fmt=fmt, options=options, timeout=timeout)
    except Exception as exc:
        if not allow_degraded or primary == DEGRADED_MODEL:
            raise
        log.warning("brain %s failed (%s: %s) — degrading to %s",
                    primary, type(exc).__name__, exc, DEGRADED_MODEL)
        return _call(DEGRADED_MODEL, messages, fmt=fmt, options=options,
                     timeout=timeout)


def _call(model: str, messages: list[dict], *, fmt, options, timeout) -> str:
    import ollama  # noqa: PLC0415
    client = ollama.Client(host=OLLAMA_URL, timeout=timeout)
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": -1,
        "think": think_param(model),
        "options": options or {"temperature": 0.2, "num_ctx": 8192,
                               "num_predict": 512},
    }
    if fmt is not None:
        kwargs["format"] = fmt
    try:
        resp = client.chat(**kwargs)
    except Exception as exc:
        # Some model/format/think combinations 500 on Ollama (observed:
        # qwen3:4b + think=True + format=json). Retry once with the
        # model's default think behavior — content is scrubbed by the
        # Phase 30 backstop downstream either way.
        log.warning("brain chat %s failed with think=%r (%s) — retrying "
                    "without think param", model, think_param(model), exc)
        kwargs.pop("think", None)
        resp = client.chat(**kwargs)
    msg = resp.get("message", {}) or {}
    # Contract: content only. msg may carry a `thinking` field — discard.
    return (msg.get("content") or "").strip()

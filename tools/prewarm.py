#!/usr/bin/env python3
"""Pre-warm Ollama models so the first user request never pays cold-start cost.

Sends a tiny chat to each model with `keep_alive=-1` (router) so the model
stays resident, plus a normal touch on the heavy model so its weights are
already mapped when the first heavy route fires.

Run as a oneshot via nexus-prewarm.service after nexus-api comes up.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import ollama

ROOT = Path(__file__).resolve().parent.parent
MODELS_FILE = ROOT / "models.json"
OLLAMA_URL = "http://localhost:11434"


def _models() -> dict:
    if MODELS_FILE.exists():
        try:
            return json.loads(MODELS_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    # Fallback only when models.json is unreadable. qwen3:4b is always
    # present; never default `heavy` to the retired qwen3.6.
    return {"router": "qwen3:4b", "heavy": "qwen3:4b"}


def _think_for(model: str):
    """Per-family think suppression — reuse the brain's contract so a future
    gpt-oss switch (think='low', not False) doesn't make prewarm hang/fail
    the way the old gpt-oss:120b warm did."""
    try:
        from core import brain  # noqa: PLC0415
        return brain.think_param(model)
    except Exception:
        return False


def _warm(client: ollama.Client, model: str, *, keep_alive: int | str) -> tuple[bool, float, str]:
    """Send one chat. Returns (ok, elapsed_seconds, message)."""
    started = time.monotonic()
    try:
        client.chat(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            stream=False,
            think=_think_for(model),
            options={"num_predict": 1, "temperature": 0.0, "num_ctx": 256},
            keep_alive=keep_alive,
        )
    except Exception as exc:
        return False, time.monotonic() - started, f"{type(exc).__name__}: {exc}"
    return True, time.monotonic() - started, "ok"


def main() -> int:
    cfg = _models()
    router = cfg.get("router", "qwen3:4b")
    heavy = cfg.get("heavy", "qwen3.6")
    client = ollama.Client(host=OLLAMA_URL)

    targets = [
        (router, -1),     # pin router resident forever
        (heavy, -1),      # pin heavy too — perf-guardian flags it as missing
                          # if it unloads, and we have headroom (128GB UMA)
    ]
    seen = set()
    failures = 0
    for model, keep_alive in targets:
        if model in seen:
            continue
        seen.add(model)
        ok, dt, msg = _warm(client, model, keep_alive=keep_alive)
        tag = "ok" if ok else "fail"
        print(f"[prewarm] {model} keep_alive={keep_alive!r} {tag} {dt:.2f}s {msg}", flush=True)
        if not ok:
            failures += 1
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

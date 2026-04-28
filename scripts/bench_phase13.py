#!/usr/bin/env python3
"""Phase 13 verification benchmark — isolates the dominant speed lever.

Phase 13.1 (KEEP_ALIVE=-1 + prewarm) and 13.2 (stable static prefix) target
**time-to-first-token** on the router model. We verify by:

  cold pass:  evict the model (`keep_alive=0`) right before each call, then
              measure time-to-first-streamed-token.
  warm pass:  ensure model is pinned via prewarm, then re-measure.

The other Phase 13 wins (streaming, fast_mode, parallel tools, truncation,
instant ack) are architectural and were validated by their own smoke tests.
This benchmark targets the latency claim in the exit criteria.

Talks straight to Ollama on :11434 so the running nexus-api service (which
hasn't been restarted yet) doesn't confound measurements.
"""
from __future__ import annotations

import statistics
import sys
import time

import ollama

OLLAMA_URL = "http://localhost:11434"
ROUTER_MODEL = "qwen3:4b"

PROMPTS = [
    "hi",
    "what is 2+2?",
    "yes or no?",
    "ack",
    "thanks",
    "in 2 sentences, what is a hash table?",
    "list 3 reasons to use postgres over sqlite",
    "what does git rebase do?",
    "explain a kalman filter briefly",
    "mutex vs semaphore?",
]


def _ttf_ms(model: str, prompt: str, keep_alive) -> float:
    client = ollama.Client(host=OLLAMA_URL)
    started = time.monotonic()
    ttf = None
    for chunk in client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
        think=False,
        keep_alive=keep_alive,
        options={"temperature": 0.0, "num_predict": 16, "num_ctx": 1024},
    ):
        # First chunk that carries any content marks first-token time.
        msg = chunk.get("message", {}) if isinstance(chunk, dict) else getattr(chunk, "message", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        if content:
            ttf = time.monotonic() - started
            break
    return (ttf if ttf is not None else (time.monotonic() - started)) * 1000


def _evict(model: str) -> None:
    client = ollama.Client(host=OLLAMA_URL)
    try:
        client.chat(model=model, messages=[{"role": "user", "content": "x"}],
                    stream=False, think=False, keep_alive=0,
                    options={"num_predict": 1, "num_ctx": 256})
    except Exception:
        pass
    time.sleep(1.0)  # let the eviction settle


def main() -> int:
    print("# Phase 13 — Speed Layer verification\n")
    print(f"Ollama at {OLLAMA_URL}, router model = {ROUTER_MODEL}.\n")
    print("## Cold pass (model evicted before each call — pre-Phase-13 baseline)\n")
    cold: list[float] = []
    for i, prompt in enumerate(PROMPTS):
        _evict(ROUTER_MODEL)
        ttf = _ttf_ms(ROUTER_MODEL, prompt, keep_alive="30s")
        cold.append(ttf)
        print(f"  cold {i:2d}  ttf={ttf:7.1f}ms  '{prompt[:50]}'")

    print("\n## Warm pass (router pinned via prewarm + KEEP_ALIVE=-1)\n")
    # Make sure the model is loaded and stays loaded.
    _ttf_ms(ROUTER_MODEL, "warmup", keep_alive=-1)
    warm: list[float] = []
    for i, prompt in enumerate(PROMPTS):
        ttf = _ttf_ms(ROUTER_MODEL, prompt, keep_alive=-1)
        warm.append(ttf)
        print(f"  warm {i:2d}  ttf={ttf:7.1f}ms  '{prompt[:50]}'")

    cold_avg = statistics.mean(cold)
    warm_avg = statistics.mean(warm)
    cold_med = statistics.median(cold)
    warm_med = statistics.median(warm)
    delta_avg = (cold_avg - warm_avg) / cold_avg * 100 if cold_avg else 0.0
    delta_med = (cold_med - warm_med) / cold_med * 100 if cold_med else 0.0

    print()
    print("| pass | n  | mean ttf | median ttf |")
    print("|------|----|----------|------------|")
    print(f"| cold | {len(cold):2d} | {cold_avg:7.1f}ms | {cold_med:7.1f}ms |")
    print(f"| warm | {len(warm):2d} | {warm_avg:7.1f}ms | {warm_med:7.1f}ms |")
    print()
    verdict_avg = "PASS" if delta_avg >= 50 else "FAIL"
    verdict_med = "PASS" if delta_med >= 50 else "FAIL"
    print(f"**Mean TTF speedup**:   {delta_avg:5.1f}%  → {verdict_avg}")
    print(f"**Median TTF speedup**: {delta_med:5.1f}%  → {verdict_med}")
    overall = "PASS" if (delta_avg >= 50 or delta_med >= 50) else "FAIL"
    print(f"\n**Phase 13 exit criterion (>=50% TTF reduction)**: {overall}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Phase 39 — gpt-oss:120b acceptance benchmark on WattBott (Strix Halo,
Vulkan/RADV). Gate: >=25 t/s decode AND TTFT <4s on a router-sized
prompt AND qwen2.5vl:7b loads alongside without evicting the brain.

Prints a markdown report and exits 0 (pass) / 1 (fail).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OLLAMA = "http://localhost:11434"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "gpt-oss:120b"

GATE_DECODE_TPS = 25.0
GATE_TTFT_S = 4.0


def _chat_stream(model: str, messages, *, think, num_predict: int):
    """Stream a chat; return (ttft_s, decode_tps, eval_count, total_s)."""
    body = {
        "model": model, "messages": messages, "stream": True,
        "keep_alive": -1, "think": think,
        "options": {"temperature": 0.2, "num_predict": num_predict,
                    "num_ctx": 4096},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    ttft = None
    final = {}
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            chunk = json.loads(line)
            msg = chunk.get("message", {}) or {}
            if ttft is None and (msg.get("content") or msg.get("thinking")):
                ttft = time.monotonic() - t0
            if chunk.get("done"):
                final = chunk
    total = time.monotonic() - t0
    eval_count = final.get("eval_count", 0)
    eval_dur_s = (final.get("eval_duration") or 1) / 1e9
    tps = eval_count / eval_dur_s if eval_dur_s > 0 else 0.0
    return (ttft or total), tps, eval_count, total


def _ollama_ps() -> str:
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True,
                             text=True, timeout=15).stdout
        return out.strip()
    except Exception as exc:
        return f"(ollama ps failed: {exc})"


def main() -> int:
    from core import brain
    from workers.llm_router import ROUTER_SYSTEM_PROMPT

    think = brain.think_param(MODEL)
    print(f"# gpt-oss benchmark — {MODEL} (think={think!r})\n")

    # Warm-up / load (not measured).
    print("loading model (warm-up call)...")
    t0 = time.monotonic()
    _chat_stream(MODEL, [{"role": "user", "content": "hi"}],
                 think=think, num_predict=8)
    print(f"  loaded + first reply in {time.monotonic()-t0:.1f}s\n")

    # 1. Decode throughput on a ~200-token generation.
    ttft1, tps1, n1, tot1 = _chat_stream(
        MODEL,
        [{"role": "user", "content":
          "Explain in detail how a construction estimator builds a bid "
          "for a mechanical insulation scope, covering takeoff, labor "
          "rates, material pricing, and contingency."}],
        think=think, num_predict=220,
    )
    print(f"## decode throughput\n- {tps1:.1f} t/s ({n1} tokens in {tot1:.1f}s)\n")

    # 2. TTFT on a router-sized prompt (~500 tokens in).
    ttft2, tps2, n2, tot2 = _chat_stream(
        MODEL,
        [{"role": "system", "content": ROUTER_SYSTEM_PROMPT},
         {"role": "user", "content": "build me a breakout game in a single html file"}],
        think=think, num_predict=64,
    )
    print(f"## router TTFT\n- TTFT {ttft2:.2f}s (decode {tps2:.1f} t/s)\n")

    # 3. Co-residency: load qwen2.5vl:7b alongside, then re-check brain.
    # num_gpu=0 matches production (tools/vision_tool.py): the VL model
    # runs on CPU so it can't OOM the brain out of the 64GB VRAM carve.
    print("## co-residency (qwen2.5vl:7b on CPU, matching vision_tool)")
    try:
        body = {"model": "qwen2.5vl:7b",
                "messages": [{"role": "user", "content": "say ok"}],
                "stream": False, "keep_alive": -1,
                # num_ctx must be bounded like vision_tool does (4096) —
                # the model's default ctx makes the CPU memory estimate
                # balloon to ~47GiB and the load is refused.
                "options": {"num_predict": 8, "num_gpu": 0,
                            "num_ctx": 4096}}
        req = urllib.request.Request(f"{OLLAMA}/api/chat",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=300).read()
        vl_ok = True
    except Exception as exc:
        vl_ok = False
        print(f"- qwen2.5vl load FAILED: {exc}")
    ps_out = _ollama_ps()
    print("```\n" + ps_out + "\n```")
    brain_resident = MODEL.split(":")[0] in ps_out
    vl_resident = "qwen2.5vl" in ps_out
    # Brain must still answer (not evicted / OOM) with vl resident.
    ttft3, tps3, n3, tot3 = _chat_stream(
        MODEL, [{"role": "user", "content": "say ok"}],
        think=think, num_predict=8)
    print(f"- brain re-answer with vl co-resident: {tot3:.1f}s total "
          f"(TTFT {ttft3:.2f}s)\n")

    free = subprocess.run(["free", "-h"], capture_output=True, text=True).stdout
    print("## memory\n```\n" + free.strip() + "\n```\n")

    ok_decode = tps1 >= GATE_DECODE_TPS
    ok_ttft = ttft2 < GATE_TTFT_S
    ok_corun = vl_ok and brain_resident and vl_resident
    print("## gates")
    print(f"- decode >= {GATE_DECODE_TPS} t/s : {'PASS' if ok_decode else 'FAIL'} ({tps1:.1f})")
    print(f"- TTFT < {GATE_TTFT_S}s (router) : {'PASS' if ok_ttft else 'FAIL'} ({ttft2:.2f}s)")
    print(f"- vl co-resident, no eviction    : {'PASS' if ok_corun else 'FAIL'}")
    overall = ok_decode and ok_ttft and ok_corun
    print(f"\nOVERALL: {'PASS' if overall else 'FAIL'}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())

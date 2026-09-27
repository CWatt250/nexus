"""Score a router model on the gold sets.

  venv/bin/python training/eval_router.py [ollama-model]

Reports two accuracies per set:
  raw   — the model's own JSON route (no deterministic guards)
  final — route_llm() end to end, guards included (what Nexus actually does)
Gold: training/data/router_test_real.jsonl (Colton's real messages, hand-
labeled) + tests/evals/cases/routing_live.yaml. Never train on these.
"""
from __future__ import annotations

import json
import os
os.environ.setdefault("NEXUS_NO_DECISION_LOG", "1")  # keep router traffic log real-only
import sys
import time
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import brain  # noqa: E402
from workers import llm_router  # noqa: E402


def gold() -> dict[str, list[dict]]:
    real = [json.loads(l) for l in open(ROOT / "training/data/router_test_real.jsonl")]
    evals = [{"text": c["input"], "route": c["expect"].get("route"),
              "recon_mode": c["expect"].get("recon_mode")}
             for c in yaml.safe_load(open(ROOT / "tests/evals/cases/routing_live.yaml"))]
    return {"real": real, "evals": evals}


def main(model: str | None) -> None:
    if model:
        brain.get_router_model = lambda: model
    print("model:", brain.get_router_model())
    for name, rows in gold().items():
        raw_ok = fin_ok = n = 0
        misses: list[str] = []
        confusion: Counter = Counter()
        t0 = time.monotonic()
        for r in rows:
            if r["route"] is None:
                continue
            n += 1
            raw = json.loads(brain.chat(
                [{"role": "system", "content": llm_router.ROUTER_SYSTEM_PROMPT},
                 {"role": "user", "content": r["text"][:4000]}],
                model=brain.get_router_model(), fmt=llm_router.ROUTER_SCHEMA,
                options={"temperature": 0.0, "num_predict": 200}, timeout=30.0) or "{}")
            fin = llm_router.route_llm(r["text"])
            raw_ok += raw.get("route") == r["route"]
            fin_ok += fin["route"] == r["route"]
            if fin["route"] != r["route"]:
                confusion[(r["route"], fin["route"])] += 1
                misses.append(f"  want {r['route']:<10} got {fin['route']:<10} {r['text'][:70]!r}")
        dt = time.monotonic() - t0
        print(f"[{name}] n={n}  raw={raw_ok / n:.1%}  final={fin_ok / n:.1%}  "
              f"({dt / n:.2f}s/msg incl. 2 calls)")
        print("  confusions (want→got):", dict(confusion.most_common(8)))
        print("\n".join(misses[:40]))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)

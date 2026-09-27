"""Join Telegram turns + 👍/👎 ratings into a KTO dataset (step 6).

  venv/bin/python training/export_feedback.py [--out training/data/brain_kto.jsonl]

KTO learns from unpaired thumbs up/down, so every rated reply is one row:
  {"prompt": [...messages], "completion": [{"role":"assistant",...}], "label": bool}

A reaction on bot message M is credited to the latest turn in that chat
whose user message id is below M (see tools/telegram_feedback.py). The
latest reaction on a message wins; a removed reaction (0) un-rates it.
"""
from __future__ import annotations

import argparse
import bisect
import json
from collections import defaultdict
from pathlib import Path

FEEDBACK = Path.home() / "AI_Agent" / "memory" / "feedback"


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def build(turns: list[dict], ratings: list[dict]) -> list[dict]:
    by_chat: dict[int, list[dict]] = defaultdict(list)
    for t in turns:
        by_chat[t["chat_id"]].append(t)
    for ts in by_chat.values():
        ts.sort(key=lambda t: t["user_msg_id"])

    final: dict[tuple[int, int], int] = {}          # (chat, bot msg) -> rating
    for r in sorted(ratings, key=lambda r: r["ts"]):
        final[(r["chat_id"], r["message_id"])] = r["rating"]

    per_turn: dict[tuple[int, int], int] = {}       # (chat, user msg) -> rating
    for (chat, mid), rating in final.items():
        ts = by_chat.get(chat, [])
        i = bisect.bisect_left([t["user_msg_id"] for t in ts], mid) - 1
        if i < 0:
            continue
        per_turn[(chat, ts[i]["user_msg_id"])] = rating   # later chunk wins; same reply

    rows = []
    for chat, ts in by_chat.items():
        for t in ts:
            rating = per_turn.get((chat, t["user_msg_id"]), 0)
            if rating == 0 or not t["prompt"].strip():
                continue
            rows.append({"prompt": [{"role": "user", "content": t["prompt"]}],
                         "completion": [{"role": "assistant", "content": t["reply"]}],
                         "label": rating > 0})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=FEEDBACK)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).parent / "data" / "brain_kto.jsonl")
    a = ap.parse_args()
    rows = build(_read(a.dir / "turns.jsonl"), _read(a.dir / "ratings.jsonl"))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    up = sum(r["label"] for r in rows)
    print(f"{len(rows)} rated turns ({up} 👍 / {len(rows) - up} 👎) → {a.out}")


if __name__ == "__main__":
    main()

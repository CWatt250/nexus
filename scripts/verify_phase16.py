"""Phase 16 verification driver.

Three architectural gates that we can prove offline:

  1. Scheduler fires a 'once' trigger 30s out → task lands in the queue.
  2. Performance Guardian writes at least one sample to perf-guardian.jsonl
     and reports the pinned-model status.
  3. Conversation handler still answers status/queue intents under <1s
     (smoke from 15.7 still holds after the 16.x changes).

Live two-way Telegram exchange is a Colton-side step (sudo restart of
nexus-telegram); the architectural pieces are exercised here.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path.home() / "AI_Agent"
sys.path.insert(0, str(ROOT))

from core import scheduler, task_queue  # noqa: E402
from safety import perf_guardian  # noqa: E402
from workers.conversation_handler import fast_handle  # noqa: E402


def main() -> int:
    print("Phase 16 verification\n")

    # ---- Gate 1: scheduler fires a 30s-out 'once' trigger ----
    from datetime import datetime, timedelta, timezone
    fire_at = (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()
    sid = scheduler.add_schedule("once", fire_at, "phase16.9 scheduler probe")
    print(f"scheduled task (once) for {fire_at} → schedule_id={sid}")

    fired_ids: list[str] = []
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        fired = scheduler.tick_once()
        if fired:
            fired_ids.extend(fired)
            break
        time.sleep(2)
    if fired_ids:
        print(f"scheduler fired {fired_ids}")
        # Cancel them so the task_worker (if running) doesn't process them.
        for tid in fired_ids:
            task_queue.cancel(tid, note="phase16.9 cleanup")
    sched_ok = bool(fired_ids)

    # ---- Gate 2: perf guardian samples + pinned-model report ----
    rec = perf_guardian.tick_once()
    perf_ok = "ram_pct" in rec and "pinned" in rec
    print(f"perf_guardian sample wrote keys={sorted(rec.keys())[:5]}…")
    print(f"  pinned: {rec.get('pinned')}")

    log_path = ROOT / "memory" / "perf-guardian.jsonl"
    log_count = (
        sum(1 for _ in log_path.read_text().splitlines() if _.strip()) if log_path.exists() else 0
    )
    print(f"perf-guardian.jsonl has {log_count} record(s)")

    # ---- Gate 3: conversation handler still snappy ----
    started = time.monotonic()
    reply = fast_handle("list the recent tasks", allow_llm_chat=False)
    handler_ms = (time.monotonic() - started) * 1000
    handler_ok = reply is not None and handler_ms < 1000
    print(f"conversation handler list-intent: {handler_ms:.1f}ms reply head: {(reply or '')[:80]!r}")

    # Verdict
    print()
    print("Gates:")
    print(f"  scheduler fired a 'once' trigger:                {'PASS' if sched_ok else 'FAIL'}")
    print(f"  perf-guardian samples + pinned report:           {'PASS' if perf_ok and log_count >= 1 else 'FAIL'}")
    print(f"  conversation handler list-intent <1s no LLM:     {'PASS' if handler_ok else 'FAIL'}")
    overall = sched_ok and perf_ok and log_count >= 1 and handler_ok
    print(f"\n**Phase 16 architectural verification**: {'PASS' if overall else 'FAIL'}")

    # Markdown report
    report = ROOT / "PHASE_16_VERIFY.md"
    report.write_text(
        "# Phase 16 — Capability Expansion verification\n\n"
        f"_Date: {time.strftime('%Y-%m-%d %H:%M %Z')}_\n\n"
        "## Architectural gates\n"
        f"- Scheduler fires 'once' trigger: **{'PASS' if sched_ok else 'FAIL'}** (fired {fired_ids})\n"
        f"- Perf-guardian samples: **{'PASS' if perf_ok and log_count >= 1 else 'FAIL'}** ({log_count} records)\n"
        f"- Conversation handler <1s no-LLM list intent: **{'PASS' if handler_ok else 'FAIL'}** ({handler_ms:.1f}ms)\n\n"
        f"**Verdict: {'PASS — Phase 16 COMPLETE' if overall else 'FAIL'}**\n\n"
        "Live two-way Telegram exchange is a Colton-side gate "
        "(sudo systemctl restart nexus-telegram after the worker is up).\n",
        encoding="utf-8",
    )
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())

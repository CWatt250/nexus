"""Phase 15 verification driver.

Proves the architectural exit criterion ("Telegram while long task runs,
response <10s, task not interrupted") without depending on the live
Telegram service. Procedure:

  1. Start workers/task_worker.py as a subprocess.
  2. Enqueue a heavy task that will take 30-60s.
  3. While it runs, fire 5 conversation_handler.handle_async calls
     covering: status, modify, queue_new_task, chat_offtopic, status_again.
  4. Each handler reply must arrive in <10s.
  5. The original task must finish (status=done) without being interrupted.

Cancellation mid-flight is intentionally NOT exercised here — the queue's
cancel() flips the row but the worker doesn't yet check status between
turns (cooperative cancellation is a follow-up). Unit tests already cover
the queue-side behaviour.

Writes a markdown report to PHASE_15_VERIFY.md.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path.home() / "AI_Agent"
sys.path.insert(0, str(ROOT))

from core import task_queue  # noqa: E402
from workers import conversation_handler  # noqa: E402

REPORT_PATH = ROOT / "PHASE_15_VERIFY.md"
HEAVY_PROMPT = (
    "Write 5 distinct architectural tradeoffs (one paragraph each) between "
    "async event loops and threaded request handlers. Cite specifics."
)


def _start_worker() -> subprocess.Popen:
    return subprocess.Popen(
        [str(ROOT / "venv" / "bin" / "python3"), "-u", str(ROOT / "workers" / "task_worker.py")],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


def _stop_worker(proc: subprocess.Popen) -> None:
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


async def _fire(label: str, message: str, *, thread_id: str) -> tuple[float, str]:
    started = time.monotonic()
    try:
        reply = await asyncio.wait_for(
            conversation_handler.handle_async(message, thread_id=thread_id),
            timeout=15,
        )
    except asyncio.TimeoutError:
        return time.monotonic() - started, "[handler timeout]"
    return time.monotonic() - started, reply


async def main_async() -> int:
    print("Phase 15 verification — concurrent conversation + task execution\n", flush=True)

    # Cancel any existing pending rows so we measure cleanly.
    for r in task_queue.list_tasks():
        if r["status"] in ("pending", "running", "paused"):
            task_queue.cancel(r["task_id"], note="phase15.7 cleanup")

    print("starting workers/task_worker.py as subprocess...", flush=True)
    worker = _start_worker()
    try:
        # Let the worker import + start polling.
        await asyncio.sleep(4.0)

        long_task_id = task_queue.enqueue(HEAVY_PROMPT)
        print(f"enqueued long task {long_task_id}", flush=True)

        # Wait for the worker to claim it.
        for _ in range(30):
            row = task_queue.get_task(long_task_id)
            if row and row["status"] == "running":
                break
            await asyncio.sleep(0.5)
        snap = task_queue.get_task(long_task_id)
        if snap["status"] != "running":
            print(f"FAIL: long task did not start running (status={snap['status']})", flush=True)
            return 2
        print(f"long task running. firing 5 handler messages...\n", flush=True)

        scenarios = [
            ("status",        f"What's the status of task {long_task_id}?"),
            ("modify",        f"Note for task {long_task_id}: keep under 200 words total"),
            ("queue_new",     "Queue a fast task: 'just say queued-ok'"),
            ("chat_offtopic", "What's 2+2? answer in one word."),
            ("status_again",  "List the most recent tasks."),
        ]
        results = []
        for label, msg in scenarios:
            dt, reply = await _fire(label, msg, thread_id=f"handler:phase15.7:{label}")
            preview = (reply or "").replace("\n", " ")[:120]
            print(f"  [{label:14s}] {dt*1000:7.1f}ms  reply={preview!r}", flush=True)
            results.append((label, dt, reply))

        # Wait for the long task to settle.
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            row = task_queue.get_task(long_task_id)
            if row and row["status"] in ("done", "failed", "cancelled"):
                break
            await asyncio.sleep(2)
        long_final = task_queue.get_task(long_task_id)
        long_status = long_final["status"]
        long_chars = len(long_final.get("output") or "")
        print(f"\nlong task final: status={long_status} reply_chars={long_chars}", flush=True)

        per_call_ok = all(dt < 10 for _, dt, _ in results)
        all_replied = all(reply and reply.strip() for _, _, reply in results)
        long_ok = long_status == "done"

        print()
        print("Gates:", flush=True)
        print(f"  all 5 handler calls returned <10s:        {'PASS' if per_call_ok else 'FAIL'}", flush=True)
        print(f"  all 5 handler calls returned non-empty:   {'PASS' if all_replied else 'FAIL'}", flush=True)
        print(f"  long task finished cleanly:               {'PASS' if long_ok else 'FAIL'} ({long_status})", flush=True)
        overall = per_call_ok and all_replied and long_ok
        print(f"\n**Phase 15 architectural verification**: {'PASS' if overall else 'FAIL'}", flush=True)

        # Also write a markdown report.
        lines = ["# Phase 15 — Concurrent Conversation + Task Execution\n",
                 f"_Date: {time.strftime('%Y-%m-%d %H:%M %Z')}_\n",
                 f"Long task: `{long_task_id}` finished `{long_status}` "
                 f"({long_chars} reply chars).\n",
                 "## Handler timings\n",
                 "| label | latency | reply preview |",
                 "|-------|---------|----------------|"]
        for label, dt, reply in results:
            preview = (reply or "").replace("\n", " ").replace("|", "\\|")[:90]
            lines.append(f"| {label} | {dt*1000:.0f} ms | {preview} |")
        lines += ["", "## Gates", "",
                  f"- All 5 handler calls < 10s: **{'PASS' if per_call_ok else 'FAIL'}**",
                  f"- All 5 handler calls non-empty: **{'PASS' if all_replied else 'FAIL'}**",
                  f"- Long task finished cleanly: **{'PASS' if long_ok else 'FAIL'}** ({long_status})",
                  "",
                  f"**Verdict: {'PASS — Phase 15 COMPLETE' if overall else 'FAIL — needs investigation'}**",
                  ""]
        REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
        return 0 if overall else 1
    finally:
        print("\nstopping worker...", flush=True)
        _stop_worker(worker)


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())

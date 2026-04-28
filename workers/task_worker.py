#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Standalone task worker (Phase 15.3).

Polls `core.task_queue` for pending rows, runs each through the heavy
LangGraph agent in its own thread_id (per-task LangGraph checkpoint
isolation, Phase 15.6), and writes a live status snapshot to
`memory/active_tasks.jsonl` so the conversation handler can answer
"what's running?" without touching the long task's compute path.

Runs as `nexus-task-worker.service` (Restart=always). Stop with SIGTERM
— the worker finishes the current task before exiting.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import nexus  # noqa: E402  — registers tools, builds prompt, etc.
import router  # noqa: E402
from core import task_queue  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402
from memory import metrics as agent_metrics  # noqa: E402
from memory import retros as agent_retros  # noqa: E402

ACTIVE_LOG = ROOT / "memory" / "active_tasks.jsonl"
POLL_SECONDS = 1.0
log = logging.getLogger("nexus.task_worker")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _publish(snapshot: dict) -> None:
    """Append a status snapshot to active_tasks.jsonl. Best-effort."""
    try:
        ACTIVE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ACTIVE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("active_tasks log write failed: %s", exc)


async def _run_one(row: dict) -> None:
    task_id = row["task_id"]
    thread_id = row["thread_id"] or f"task:{task_id}"
    user_text = row["input"]
    started = time.monotonic()

    route, model = router.classify_and_model(user_text)
    agent = await nexus.build_agent_async(model)

    _publish({
        "ts": _now(), "event": "started", "task_id": task_id,
        "thread_id": thread_id, "route": route, "model": model,
        "input_preview": user_text[:200],
    })

    config = {"configurable": {"thread_id": thread_id}}
    lc_msgs = nexus.fast_mode_messages(user_text, route=route)

    ok = True
    err = ""
    reply = ""
    msgs: list = []
    agent_metrics._TASK_CTX.id = task_id
    try:
        result = await agent.ainvoke({"messages": lc_msgs}, config=config)
        msgs = result.get("messages", [])
        for m in reversed(msgs):
            if m.__class__.__name__ == "AIMessage" and getattr(m, "content", ""):
                reply = nexus.strip_thinking(m.content)
                break
    except Exception as exc:
        ok = False
        err = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            delattr(agent_metrics._TASK_CTX, "id")
        except AttributeError:
            pass

    elapsed = time.monotonic() - started
    tool_calls = sum(1 for m in msgs if m.__class__.__name__ == "ToolMessage")

    agent_metrics.record_agent_turn(
        task_id=task_id,
        started_at=started,
        ended_at=time.monotonic(),
        route=route,
        model=model,
        user_text=user_text,
        reply_text=reply,
        tool_calls=tool_calls,
        success=ok,
        error=err,
    )
    agent_retros.generate_retro_async(task_id)

    if ok:
        task_queue.update_status(task_id, "done", output=reply)
    else:
        task_queue.update_status(task_id, "failed", output=reply, error=err)

    _publish({
        "ts": _now(), "event": "finished", "task_id": task_id,
        "ok": ok, "elapsed_s": round(elapsed, 3), "tool_calls": tool_calls,
        "reply_preview": reply[:200], "error": err,
    })


async def _main_loop() -> None:
    nexus.set_system_prompt(nexus.load_system_prompt())
    nexus.extend_tools_with_mcp()
    log.info("task_worker ready (pid=%d)", os.getpid())
    stop = asyncio.Event()

    def _sig(_signum, _frame):
        log.info("signal received — finishing current task before exit")
        stop.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    while not stop.is_set():
        row = task_queue.claim_next()
        if not row:
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue
        log.info("running task %s (%s)", row["task_id"], row["status"])
        try:
            await _run_one(row)
        except Exception as exc:
            log.exception("task crashed: %s", exc)
            task_queue.update_status(row["task_id"], "failed", error=f"{type(exc).__name__}: {exc}")
            _publish({"ts": _now(), "event": "crashed", "task_id": row["task_id"], "error": str(exc)})

    log.info("task_worker exiting cleanly")


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    asyncio.run(_main_loop())
    return 0


if __name__ == "__main__":
    sys.exit(main())

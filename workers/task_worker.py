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
from core import event_bus, task_queue  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from memory import metrics as agent_metrics  # noqa: E402
from memory import retros as agent_retros  # noqa: E402

ACTIVE_LOG = ROOT / "memory" / "active_tasks.jsonl"
POLL_SECONDS = 1.0

# Per-task hard timeout (seconds). 30 minutes is the new floor — earlier
# 5-min default killed real coding tasks before they could finish even
# small features. Research/build/research-sweep types still get bumped
# higher via TIMEOUT_OVERRIDES below. Override per-task by including a
# `[timeout=600]` tag in the input — _resolve_timeout strips and parses.
DEFAULT_TIMEOUT_S = 1800

# Crude keyword routing for default budgets — short-circuits the parse
# tag for common shapes. The override tag still wins.
TIMEOUT_OVERRIDES = (
    (("research", "deep dive", "investigate", "comprehensive"), 900),
    (("build", "deploy", "scaffold", "implement", "refactor"), 900),
    (("index", "seed", "ingest", "import"), 900),
)

import re as _re  # noqa: E402
_TIMEOUT_TAG_RE = _re.compile(r"\[timeout=(\d+)\]\s*", _re.IGNORECASE)

log = logging.getLogger("nexus.task_worker")

# Heartbeat ping interval. First heartbeat fires at HEARTBEAT_INTERVAL_S
# of elapsed time, then every HEARTBEAT_INTERVAL_S after that — so a
# 4-min task gets none, a 15-minute task gets 2 pings (at 5m and 10m).
# Bumped from 120s — 2-minute pings were too noisy for long tasks. The
# 80%-of-budget warning still fires on top, so users still get a heads-
# up before a kill.
HEARTBEAT_INTERVAL_S = 300


async def _heartbeat_loop(task_id: str, started: float, tool_counter: list) -> None:
    """Sends `notify_heartbeat` every HEARTBEAT_INTERVAL_S until cancelled.

    `tool_counter` is a single-element list mutated by the callback
    handler. Reading [-1] is cheap and avoids a Lock for one int + str.
    Cancelled with CancelledError when _run_one finishes — we eat the
    cancel and exit silently.
    """
    from workers import task_notifier  # noqa: PLC0415
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            elapsed = time.monotonic() - started
            count, last_tool = (tool_counter[0], tool_counter[1]) if tool_counter else (0, "")
            try:
                await task_notifier.notify_heartbeat(
                    task_id, elapsed_s=elapsed,
                    step=last_tool, tool_calls=count,
                )
            except Exception as exc:
                log.warning("heartbeat send failed: %s", exc)
    except asyncio.CancelledError:
        return


def _make_tool_tracker():
    """Tiny LangChain callback handler that bumps a counter + records the
    most recent tool name. Used by the heartbeat loop for content."""
    from langchain_core.callbacks import BaseCallbackHandler  # noqa: PLC0415

    state = [0, ""]  # [count, last_tool_name]

    class ToolTracker(BaseCallbackHandler):
        def on_tool_start(self, serialized, input_str, **kwargs):  # noqa: D401
            name = (serialized or {}).get("name") or "(tool)"
            state[0] += 1
            state[1] = name

    return ToolTracker(), state


def _resolve_timeout(user_text: str) -> tuple[int, str]:
    """Pick a hard timeout for this task. Returns (seconds, cleaned_text).

    Priority: explicit `[timeout=N]` tag > keyword bucket > default.
    """
    m = _TIMEOUT_TAG_RE.search(user_text or "")
    if m:
        try:
            secs = max(30, min(int(m.group(1)), 7200))
            cleaned = _TIMEOUT_TAG_RE.sub("", user_text, count=1).strip()
            return secs, cleaned
        except ValueError:
            pass
    lower = (user_text or "").lower()
    for keywords, secs in TIMEOUT_OVERRIDES:
        if any(k in lower for k in keywords):
            return secs, user_text
    return DEFAULT_TIMEOUT_S, user_text


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
    raw_input = row["input"]
    timeout_s, user_text = _resolve_timeout(raw_input)
    started = time.monotonic()

    # G3 — goal-advance driver sentinel (fired by the recurring [goal-advance]
    # schedule). Runs the Ralph-loop one step per active goal instead of the
    # agent, and reports to Telegram from inside advance_all_goals().
    if user_text.strip() == "[goal-advance]":
        from core import goals, task_queue as _tq  # noqa: PLC0415
        try:
            report = await asyncio.to_thread(goals.advance_all_goals)
        except Exception as exc:
            report = f"goal-advance failed: {type(exc).__name__}: {exc}"
        _tq.update_status(task_id, "completed", output=report[:2000])
        _publish({"ts": _now(), "event": "completed", "task_id": task_id,
                  "thread_id": thread_id, "result_preview": report[:200]})
        return

    route, model = router.classify_and_model(user_text)
    try:  # G5 — session_start hooks (best-effort, never block the task)
        from core import hooks  # noqa: PLC0415
        await asyncio.to_thread(hooks.run_hooks, "session_start",
                                task_id=task_id, route=route, input=user_text[:500])
    except Exception:
        pass
    agent = await nexus.build_agent_async(model)

    _publish({
        "ts": _now(), "event": "started", "task_id": task_id,
        "thread_id": thread_id, "route": route, "model": model,
        "input_preview": user_text[:200], "timeout_s": timeout_s,
    })
    event_bus.publish_remote(
        "task_started", task_id=task_id, route=route, model=model,
        input_preview=user_text[:200],
    )

    tracker, tool_state = _make_tool_tracker()
    config = {"configurable": {"thread_id": thread_id}, "callbacks": [tracker]}
    # Phase 39 — the queue row stores the user's message VERBATIM (the
    # enqueue-time "[Current date and time: ...]" prefix is gone).
    # Wall-clock context is injected here, transiently, so the agent
    # still can't hallucinate "today" from training data.
    now = datetime.now().astimezone()
    dt_msg = SystemMessage(content=(
        f"Current date and time: {now.isoformat(timespec='seconds')}. "
        f"Current day of week: {now.strftime('%A')}. "
        "When asked about the current time, date, or day, use ONLY the "
        "datetime above. Never guess or use training data."
    ))
    # G1 — expand @file:/@diff/@git:/@url: refs into the agent input (routing
    # + logging stay on the original user_text above; unchanged when no refs).
    from core import context_refs  # noqa: PLC0415
    agent_text = context_refs.expand_refs(user_text)
    lc_msgs = [dt_msg] + nexus.fast_mode_messages(agent_text, route=route)

    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(task_id, started, tool_state)
    )

    ok = True
    err = ""
    reply = ""
    msgs: list = []
    timed_out = False
    agent_metrics._TASK_CTX.id = task_id
    try:
        result = await asyncio.wait_for(
            agent.ainvoke({"messages": lc_msgs}, config=config),
            timeout=timeout_s,
        )
        msgs = result.get("messages", [])
        for m in reversed(msgs):
            if m.__class__.__name__ == "AIMessage" and getattr(m, "content", ""):
                reply = nexus.clean_task_reply(m.content)
                break
    except asyncio.TimeoutError:
        ok = False
        timed_out = True
        err = f"TimeoutError: exceeded {timeout_s}s budget"
        log.warning("task %s timed out after %ds", task_id, timeout_s)
    except Exception as exc:
        ok = False
        err = f"{type(exc).__name__}: {exc}"
        try:  # G5 — on_error hooks
            from core import hooks  # noqa: PLC0415
            hooks.run_hooks("on_error", task_id=task_id, error=err,
                            input=user_text[:300])
        except Exception:
            pass
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
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
    try:  # G5 — session_end hooks
        from core import hooks  # noqa: PLC0415
        await asyncio.to_thread(hooks.run_hooks, "session_end",
                                task_id=task_id, success=ok, result=reply[:500])
    except Exception:
        pass

    if ok:
        task_queue.update_status(task_id, "done", output=reply)
    elif timed_out:
        task_queue.update_status(task_id, "failed", output=reply, error=err)
    else:
        task_queue.update_status(task_id, "failed", output=reply, error=err)

    _publish({
        "ts": _now(), "event": "finished", "task_id": task_id,
        "ok": ok, "elapsed_s": round(elapsed, 3), "tool_calls": tool_calls,
        "reply_preview": reply[:200], "error": err,
    })
    event_bus.publish_remote(
        "task_completed" if ok else "task_failed",
        task_id=task_id, elapsed_s=round(elapsed, 3),
        tool_calls=tool_calls, reply_preview=reply[:200], error=err,
    )

    # Lifecycle notification — every TASK enqueue MUST end with a
    # Telegram message. task_notifier handles formatting + 3000-char
    # chunking + Markdown fallback. Best-effort: never raises.
    try:
        from workers import task_notifier  # noqa: PLC0415
        last_step = ""
        if msgs:
            for m in reversed(msgs):
                if m.__class__.__name__ == "ToolMessage":
                    last_step = getattr(m, "name", "") or "(tool)"
                    break
        if ok:
            await task_notifier.notify_done(task_id, reply or "", elapsed_s=elapsed)
        elif timed_out:
            await task_notifier.notify_timeout(task_id, elapsed_s=elapsed, last_step=last_step)
        else:
            await task_notifier.notify_failed(task_id, err, elapsed_s=elapsed,
                                               output=reply or None)
    except Exception as exc:
        log.warning("task_notifier failed: %s", exc)


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

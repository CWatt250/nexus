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


def _make_tool_tracker(progress=None, *, think_on: bool = False,
                       started: float | None = None):
    """LangChain callback handler: bumps a counter + records the most
    recent tool name (heartbeat content), and — when a live bubble
    `progress` (workers.task_progress.TaskProgress) is attached — edits
    it with a rolling window of `🔧 tool args` lines, `💭 reasoning`
    glimpses (only when /think is on for that chat), and ✓ marks.
    Also measures each LLM step so reasoning=True overhead is logged."""
    from langchain_core.callbacks import AsyncCallbackHandler  # noqa: PLC0415
    from workers.task_progress import (ProgressWindow, arg_preview,  # noqa: PLC0415
                                       first_sentence)

    state = [0, ""]  # [count, last_tool_name]
    t0 = started if started is not None else time.monotonic()

    class ToolTracker(AsyncCallbackHandler):
        def __init__(self) -> None:
            super().__init__()
            self.window = ProgressWindow(progress.title) if progress else None
            self.last_activity = time.monotonic()
            self.reasoning: list[str] = []      # first sentence per LLM step
            self.step_stats: list[dict] = []    # per-step timing / token counts
            self._llm_t0: dict = {}

        def _push(self, *, idle: bool = False) -> None:
            if progress is None or self.window is None:
                return
            text = self.window.render(time.monotonic() - t0, idle=idle)
            asyncio.ensure_future(progress.stage(text))

        def _touch(self) -> None:
            self.last_activity = time.monotonic()

        async def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
            self._llm_t0[run_id] = time.monotonic()

        async def on_llm_start(self, serialized, prompts, *, run_id, **kw):
            self._llm_t0[run_id] = time.monotonic()

        async def on_llm_end(self, response, *, run_id, **kw):
            dur = time.monotonic() - self._llm_t0.pop(run_id, time.monotonic())
            gen = response.generations[0][0] if response.generations and response.generations[0] else None
            msg = getattr(gen, "message", None)
            reasoning = ""
            if msg is not None:
                reasoning = (getattr(msg, "additional_kwargs", {}) or {}).get("reasoning_content") or ""
            info = getattr(gen, "generation_info", None) or {}
            stat = {"step": len(self.step_stats) + 1, "llm_s": round(dur, 2),
                    "reasoning_chars": len(reasoning),
                    "eval_count": info.get("eval_count"),
                    "prompt_eval_count": info.get("prompt_eval_count"),
                    "content_chars": len(getattr(msg, "content", "") or "")}
            self.step_stats.append(stat)
            log.info("llm step %(step)d: %(llm_s).1fs, eval_count=%(eval_count)s, "
                     "reasoning_chars=%(reasoning_chars)d, content_chars=%(content_chars)d", stat)
            if reasoning:
                glimpse = first_sentence(reasoning, 140)
                self.reasoning.append(glimpse)
                if think_on and self.window is not None:
                    self.window.add(f"💭 {glimpse}")
                    self._push()
            self._touch()

        async def on_tool_start(self, serialized, input_str, *, inputs=None, **kw):
            name = (serialized or {}).get("name") or "(tool)"
            state[0] += 1
            state[1] = name
            if self.window is not None:
                self.window.add(f"🔧 {name} {arg_preview(inputs if inputs is not None else input_str)}".rstrip())
                self._push()
            self._touch()

        async def on_tool_end(self, output, **kw):
            if self.window is not None:
                self.window.mark_done()
                self._push()
            self._touch()

        async def on_tool_error(self, error, **kw):
            self._touch()

        def think_trail(self) -> str:
            """💭 trail for the separate trailing message when /think is on."""
            if not self.reasoning:
                return ""
            return "💭\n" + "\n".join(f"> {r}" for r in self.reasoning[-8:])

    return ToolTracker(), state


async def _idle_loop(progress, tracker) -> None:
    """Bubble-mode heartbeat: after IDLE_HEARTBEAT_S with no tool/LLM
    activity, edit the bubble to 'still thinking… 1m10s'. Replaces the
    5-min task_notifier heartbeat (which would be a second message)."""
    from workers.task_progress import IDLE_HEARTBEAT_S  # noqa: PLC0415
    last_hb = time.monotonic()
    try:
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            if now - tracker.last_activity >= IDLE_HEARTBEAT_S and now - last_hb >= IDLE_HEARTBEAT_S:
                tracker._push(idle=True)
                last_hb = now
    except asyncio.CancelledError:
        return


def _is_fresh(created_at: str | None) -> bool:
    """True when the row was enqueued within FRESH_TASK_S — the listener
    may still be writing its bubble handoff, so the worker waits for it."""
    from workers.task_progress import FRESH_TASK_S  # noqa: PLC0415
    try:
        ts = datetime.fromisoformat(created_at or "")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() < FRESH_TASK_S
    except ValueError:
        return False


def _note_history(progress, task_id: str, *, ok: bool, timed_out: bool, reply: str) -> None:
    """Close the loop in the chat history so the brain knows the queued
    task is DONE (pairs with the listener's "[queued task …]" turn)."""
    chat_id = getattr(progress, "chat_id", None)
    if not chat_id:
        return
    try:
        from core import telegram_chats as _tcs  # noqa: PLC0415
        if ok:
            body = f"[task {task_id} finished — result delivered: {(reply or '')[:300]}]"
        elif timed_out:
            body = f"[task {task_id} timed out — nothing delivered]"
        else:
            body = f"[task {task_id} failed — nothing delivered]"
        _tcs.write_turn(int(chat_id), "assistant", body)
    except Exception as exc:  # history is best-effort
        log.warning("history note for task %s failed: %s", task_id, exc)


async def _finish_bubble(progress, tracker, task_id: str, *, ok: bool, timed_out: bool,
                         reply: str, err: str, elapsed: float, think_on: bool) -> None:
    """Terminal edit of the live bubble. The deliverable replaces the
    bubble; the 💭 trail (when /think is on) goes out as a separate
    trailing message so the deliverable stays clean."""
    from workers import task_notifier  # noqa: PLC0415
    fe = task_notifier._fmt_elapsed
    _note_history(progress, task_id, ok=ok, timed_out=timed_out, reply=reply)
    if ok:
        await progress.finish(reply or "", elapsed_s=elapsed)
        if think_on:
            trail = tracker.think_trail() or task_notifier._work_trail(task_id)
            if trail:
                await progress.send(trail if trail.startswith("💭") else f"💭\n{trail}")
    elif timed_out:
        await progress.fail(f"⚠️ ran out of time on that one ({fe(elapsed)}). "
                            f"Say 'retry {task_id}' and I'll give it a longer leash.")
    else:
        log.warning("task %s failed after %.1fs: %s", task_id, elapsed, err)
        await progress.fail(f"❌ that one didn't make it — {task_notifier._humanize_error(err)} "
                            f"after {fe(elapsed)}. Say 'retry {task_id}' and I'll take another run at it.")


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
    # Live bubble handed off by the Telegram listener (None for CLI/API
    # enqueues → task_notifier sends a new message instead).
    from workers import task_progress, task_notifier  # noqa: PLC0415
    progress = await task_progress.TaskProgress.for_task(
        task_id, fresh=_is_fresh(row.get("created_at")))
    if progress is not None:
        try:
            from workers.conversation_handler import get_think_pref  # noqa: PLC0415
            think_on = get_think_pref(progress.chat_id)
        except Exception:
            think_on = False
    else:
        think_on = task_notifier._think_on()

    # TASK-route agents run with reasoning on: narration → reasoning_content,
    # content = deliverable. See nexus._make_llm.
    agent = await nexus.build_agent_async(model, reasoning=True)

    _publish({
        "ts": _now(), "event": "started", "task_id": task_id,
        "thread_id": thread_id, "route": route, "model": model,
        "input_preview": user_text[:200], "timeout_s": timeout_s,
    })
    event_bus.publish_remote(
        "task_started", task_id=task_id, route=route, model=model,
        input_preview=user_text[:200],
    )

    tracker, tool_state = _make_tool_tracker(progress, think_on=think_on, started=started)
    config = {"configurable": {"thread_id": thread_id}, "callbacks": [tracker]}
    # Phase 39 — the queue row stores the user's message VERBATIM (the
    # enqueue-time "[Current date and time: ...]" prefix is gone).
    # Wall-clock context is injected here, transiently, so the agent
    # still can't hallucinate "today" from training data.
    now = datetime.now().astimezone()
    # Folded into the human turn, NOT a second SystemMessage: strict chat
    # templates (Ornith-1.0) rejected a system message at index 1 with
    # "System message must be at the beginning" — every real task Jul–Sep
    # 2026 died on it before the model ran.
    dt_line = (
        f"[Current date and time: {now.isoformat(timespec='seconds')} "
        f"({now.strftime('%A')}). Use ONLY this for any time/date/day question.]"
    )
    # G1 — expand @file:/@diff/@git:/@url: refs into the agent input (routing
    # + logging stay on the original user_text above; unchanged when no refs).
    from core import context_refs  # noqa: PLC0415
    agent_text = f"{dt_line}\n\n{context_refs.expand_refs(user_text)}"
    lc_msgs = nexus.fast_mode_messages(agent_text, route=route)

    if progress is not None:
        progress.start()
        heartbeat_task = asyncio.create_task(_idle_loop(progress, tracker))
    else:
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
    if tracker.step_stats:
        log.info("task %s reasoning cost: %d llm steps, llm %.1fs total, %d reasoning chars",
                 task_id, len(tracker.step_stats),
                 sum(s["llm_s"] for s in tracker.step_stats),
                 sum(s["reasoning_chars"] for s in tracker.step_stats))

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
        last_step = ""
        if msgs:
            for m in reversed(msgs):
                if m.__class__.__name__ == "ToolMessage":
                    last_step = getattr(m, "name", "") or "(tool)"
                    break
        if progress is not None:
            await _finish_bubble(progress, tracker, task_id, ok=ok, timed_out=timed_out,
                                 reply=reply, err=err, elapsed=elapsed, think_on=think_on)
        elif ok:
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

    # Use asyncio's native signal handling — a plain signal.signal handler that
    # calls stop.set() does NOT reliably wake a loop blocked in epoll, so the
    # worker hung on every restart until systemd SIGKILL'd it at 90s. add_signal_
    # handler schedules the wake on the loop itself, so idle shutdown is instant.
    loop = asyncio.get_running_loop()

    def _sig():
        log.info("signal received — finishing current task before exit")
        stop.set()

    for _s in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(_s, _sig)
        except NotImplementedError:  # non-Unix fallback
            signal.signal(_s, lambda *_: stop.set())

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

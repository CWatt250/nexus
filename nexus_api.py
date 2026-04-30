#!/home/cwatt250/AI_Agent/venv/bin/python3
"""OpenAI-compatible API for Nexus.

Exposes POST /v1/chat/completions and GET /v1/models so Open WebUI (or any
OpenAI-format client) can talk to the LangGraph agent defined in nexus.py.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import git_sync  # noqa: E402
import reflection  # noqa: E402
import router  # noqa: E402
from memory import sessions  # noqa: E402
from nexus import (  # noqa: E402
    ThinkStripper,
    build_agent_async,
    extend_tools_with_mcp,
    fast_mode_messages,
    is_fast_route,
    load_system_prompt,
    set_system_prompt,
    strip_thinking,
)
from tools.sparky_state import instant_ack  # noqa: E402
from memory import metrics as agent_metrics  # noqa: E402
from memory import retros as agent_retros  # noqa: E402

MODEL_NAME = "nexus"
HOST = "0.0.0.0"
PORT = 11435

# System-prompt + MCP wiring stays sync — these don't need an event loop.
set_system_prompt(load_system_prompt())
_mcp_added = extend_tools_with_mcp()
if _mcp_added:
    print(f"[mcp] nexus-api loaded {_mcp_added} external tools", flush=True)
_system_prompt = load_system_prompt()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Warm-build the heavy async agent so the first request doesn't pay
    # the aiosqlite + LangGraph construction cost.
    await build_agent_async(router.model_for("heavy"))
    yield


app = FastAPI(title="nexus-api", version="0.5", lifespan=_lifespan)


def _last_user_text(msgs: list) -> str:
    for m in reversed(msgs):
        if m.role == "user" and m.content:
            return m.content
    return ""


async def _pick_agent(messages: list) -> tuple[object, str, str]:
    route, model = router.classify_and_model(_last_user_text(messages))
    agent = await build_agent_async(model)
    return agent, route, model


_reflection_threads: list[threading.Thread] = []


def _spawn_reflection(user: str, reply: str, messages, route: str, model: str) -> None:
    """Run reflect() in a background thread after an API response. Mirrors
    nexus.py so reflection happens whether the client talks to the CLI or the
    OpenAI-compatible API."""
    clean_reply = strip_thinking(reply or "")
    def _worker():
        try:
            reflection.reflect(user, clean_reply, messages=messages, route=route, model=model)
        except Exception:
            pass
        try:
            git_sync.auto_commit()
        except Exception:
            pass
    t = threading.Thread(target=_worker, name="api-reflect+commit", daemon=True)
    t.start()
    _reflection_threads.append(t)
    _reflection_threads[:] = [x for x in _reflection_threads if x.is_alive()]

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ChatMessage(BaseModel):
    role: str
    content: str | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    # Open WebUI extensions — any of these may carry the chat/session id:
    chat_id: str | None = None
    user: str | None = None
    metadata: dict | None = None


def _last_user_message(msgs: list[ChatMessage]) -> ChatMessage | None:
    for m in reversed(msgs):
        if m.role == "user" and m.content:
            return m
    return None


def _thread_id_for(req: ChatRequest) -> tuple[str, str]:
    """Pick a stable thread_id from the request. Returns (thread_id, source)."""
    if req.chat_id:
        return str(req.chat_id), "chat_id"
    meta = req.metadata or {}
    for key in ("chat_id", "session_id", "conversation_id", "thread_id"):
        v = meta.get(key)
        if v:
            return str(v), f"metadata.{key}"
    if req.user:
        return f"user:{req.user}", "user"
    # Fallback: hash first user message so same opening stays stable within a session.
    for m in req.messages:
        if m.role == "user" and m.content:
            h = hashlib.sha1(m.content.encode("utf-8", errors="replace")).hexdigest()[:16]
            return f"auto:{h}", "auto"
    return f"auto:{uuid.uuid4().hex[:16]}", "auto"


def _extract_reply(result: dict) -> str:
    for m in reversed(result.get("messages", [])):
        if m.__class__.__name__ == "AIMessage" and getattr(m, "content", None):
            return strip_thinking(m.content)
    msgs = result.get("messages", [])
    return strip_thinking(msgs[-1].content) if msgs else ""


def _completion_envelope(content: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_NAME,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _chunk_bytes(chunk_id: str, created: int, delta: dict, finish: str | None = None) -> bytes:
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": MODEL_NAME,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n".encode()


async def _stream_agent(
    agent, lc_msgs, config, user_msg: str, route: str, model: str, *, task_id: str
) -> AsyncIterator[bytes]:
    """Stream agent output token-by-token as OpenAI SSE chunks.

    Uses LangGraph's `stream_mode='messages'` which yields
    `(message_chunk, metadata)` tuples as the LLM emits tokens. We forward
    only AI content deltas; tool-call chunks without text are skipped so the
    client sees a clean stream even when the agent detours through a tool.
    """
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    yield _chunk_bytes(chunk_id, created, {"role": "assistant"})

    stripper = ThinkStripper()
    full_text_parts: list[str] = []
    final_messages = None
    started = time.monotonic()
    ok = True
    err_msg = ""
    agent_metrics._TASK_CTX.id = task_id

    try:
        async for event in agent.astream(
            {"messages": lc_msgs}, config=config, stream_mode="messages"
        ):
            # Event is typically (message_chunk, metadata).
            if isinstance(event, tuple) and event:
                msg = event[0]
            else:
                msg = event
            content = getattr(msg, "content", None)
            if not content:
                continue
            # content can be str or list-of-parts (Anthropic-style). Ollama
            # returns str, but normalize just in case.
            if isinstance(content, list):
                text = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            else:
                text = str(content)
            if not text:
                continue
            visible = stripper.feed(text)
            if visible:
                full_text_parts.append(visible)
                yield _chunk_bytes(chunk_id, created, {"content": visible})
        tail = stripper.flush()
        if tail:
            full_text_parts.append(tail)
            yield _chunk_bytes(chunk_id, created, {"content": tail})
    except Exception as exc:
        ok = False
        err_msg = f"{type(exc).__name__}: {exc}"
        yield _chunk_bytes(
            chunk_id, created, {"content": f"\n[stream error: {err_msg}]"}
        )

    yield _chunk_bytes(chunk_id, created, {}, finish="stop")
    yield b"data: [DONE]\n\n"

    try:
        snap = await agent.aget_state(config)
        final_messages = getattr(snap, "values", {}).get("messages") if snap else None
    except Exception:
        final_messages = None
    tool_calls = sum(
        1 for m in (final_messages or []) if m.__class__.__name__ == "ToolMessage"
    )
    agent_metrics.record_agent_turn(
        task_id=task_id,
        started_at=started,
        ended_at=time.monotonic(),
        route=route,
        model=model,
        user_text=user_msg,
        reply_text="".join(full_text_parts),
        tool_calls=tool_calls,
        success=ok,
        error=err_msg,
    )
    agent_retros.generate_retro_async(task_id)
    try:
        delattr(agent_metrics._TASK_CTX, "id")
    except AttributeError:
        pass
    _spawn_reflection(user_msg, "".join(full_text_parts), final_messages, route, model)


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_NAME,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "nexus",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    agent, route, model = await _pick_agent(req.messages)
    thread_id, _src = _thread_id_for(req)
    config = {"configurable": {"thread_id": thread_id}}

    # Checkpointer holds prior turns; we only pass the new user message.
    last = _last_user_message(req.messages)
    user_text = last.content if last and last.content else ""
    lc_msgs = fast_mode_messages(user_text, route=route) if user_text else []

    first_msg = last.content if last else None
    sessions.touch_session(thread_id, source="api", first_msg=first_msg)

    # Phase 13.8: pre-baked Sparky bubble within ~ms on heavy turns.
    instant_ack(user_text, route=route)

    task_id = uuid.uuid4().hex[:12]

    if req.stream:
        return StreamingResponse(
            _stream_agent(agent, lc_msgs, config, user_text, route, model, task_id=task_id),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    started = time.monotonic()
    ok = True
    err_msg = ""
    agent_metrics._TASK_CTX.id = task_id
    try:
        result = await agent.ainvoke({"messages": lc_msgs}, config=config)
    except Exception as exc:
        ok = False
        err_msg = f"{type(exc).__name__}: {exc}"
        result = {"messages": []}
    finally:
        try:
            delattr(agent_metrics._TASK_CTX, "id")
        except AttributeError:
            pass
    reply = _extract_reply(result) if ok else f"[error: {err_msg}]"
    msgs = result.get("messages", [])
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
        error=err_msg,
    )
    agent_retros.generate_retro_async(task_id)
    _spawn_reflection(user_text, reply, msgs, route, model)
    return _completion_envelope(reply)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "model": MODEL_NAME}


@app.get("/health")
async def health():
    """Health check endpoint for Telegram bot."""
    return {"status": "ok", "model": MODEL_NAME}


class SimpleChatRequest(BaseModel):
    """Simple chat request for Telegram integration."""
    message: str


@app.post("/chat")
async def simple_chat(req: SimpleChatRequest):
    """Simple chat endpoint for Telegram bot."""
    agent = await build_agent_async(router.model_for("heavy"))
    thread_id = "telegram-chat"
    config = {"configurable": {"thread_id": thread_id}}
    lc_msgs = [HumanMessage(content=req.message)]

    try:
        result = await agent.ainvoke({"messages": lc_msgs}, config=config)
        reply = _extract_reply(result)
        return {"response": reply}
    except Exception as e:
        return {"response": f"Error: {type(e).__name__}: {e}"}


@app.get("/tasks")
async def list_tasks():
    """List current tasks (for Telegram bot)."""
    try:
        from agents.orchestrator import get_orchestrator
        orch = get_orchestrator()
        status = orch.get_status()
        tasks = [t["description"] for t in status.get("queued_tasks", [])]
        return {"tasks": tasks}
    except Exception:
        return {"tasks": []}


class ScheduleRequest(BaseModel):
    """Phase 16.5 — POST /schedule body shape."""
    kind: str          # 'once' | 'cron' | 'interval'
    spec: str          # ISO datetime | cron expr | seconds
    input: str
    priority: int = 0


@app.post("/schedule")
async def schedule_create(req: ScheduleRequest):
    from core import scheduler
    try:
        sid = scheduler.add_schedule(req.kind, req.spec, req.input, priority=req.priority)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "schedule_id": sid}


@app.get("/schedules")
async def schedule_list():
    from core import scheduler
    return {"schedules": scheduler.list_schedules()}


@app.delete("/schedule/{schedule_id}")
async def schedule_delete(schedule_id: str):
    from core import scheduler
    return {"deleted": scheduler.delete_schedule(schedule_id)}


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    """Phase 17.1 — websocket bus subscription. Sends a small replay of
    recent events on connect, then streams new events as they arrive."""
    from core import event_bus
    await ws.accept()
    q = event_bus.subscribe()
    try:
        for item in event_bus.replay_recent(limit=100):
            await ws.send_json(item)
        while True:
            item = await q.get()
            try:
                await ws.send_json(item)
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        event_bus.unsubscribe(q)


class PublishRequest(BaseModel):
    """POST /events/publish — let out-of-process workers ship into the bus."""
    event: str
    fields: dict | None = None


@app.post("/events/publish")
async def events_publish(req: PublishRequest):
    from core import event_bus
    record = {"event": req.event, **(req.fields or {})}
    event_bus.publish(record)
    return {"ok": True}


@app.get("/metrics/intent_latency")
async def intent_latency(hours: int = 24):
    """Rolling latency aggregate by intent over the last `hours` (default 24).

    Reads `~/AI_Agent/memory/intent_latencies.jsonl` (one line per
    routed message, written by `workers.conversation_handler.route_message`)
    and returns count / mean / p50 / p95 / max per intent. Cheap —
    file is bounded and we stream-parse line by line.

    Shape:
        {
          "window_hours": 24,
          "total": 87,
          "by_intent": {
            "query_inline": {"n": 30, "mean": 1.42, "p50": 1.31, "p95": 2.01, "max": 3.0},
            "query_tool":   {"n": 12, "mean": 4.12, "p50": 3.85, "p95": 9.21, "max": 14.5},
            "task":         {"n": 5,  "mean": 4.5,  "p50": 4.5,  "p95": 5.0,  "max": 5.0},
            ...
          },
          "fast_format": {"clean_output": 8, "search_top_hit": 3, ...}
        }
    """
    from datetime import datetime, timedelta, timezone
    import json as _json
    from pathlib import Path

    log_path = Path.home() / "AI_Agent" / "memory" / "intent_latencies.jsonl"
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 24 * 30)))).timestamp()

    by_intent: dict[str, list[float]] = {}
    fast_format: dict[str, int] = {}
    total = 0

    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = _json.loads(raw)
                    except _json.JSONDecodeError:
                        continue
                    ts = entry.get("ts", "")
                    if not isinstance(ts, str):
                        continue
                    try:
                        t = datetime.fromisoformat(ts).timestamp()
                    except ValueError:
                        continue
                    if t < cutoff:
                        continue
                    intent = entry.get("intent") or "unknown"
                    elapsed = entry.get("elapsed_s")
                    if not isinstance(elapsed, (int, float)):
                        continue
                    by_intent.setdefault(intent, []).append(float(elapsed))
                    total += 1
                    ff = entry.get("fast_format")
                    if ff:
                        fast_format[ff] = fast_format.get(ff, 0) + 1
        except OSError:
            pass

    def _percentile(values: list[float], pct: float) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        idx = max(0, min(len(s) - 1, int(round(pct / 100 * (len(s) - 1)))))
        return round(s[idx], 3)

    summary = {}
    for intent, vals in by_intent.items():
        if not vals:
            continue
        summary[intent] = {
            "n": len(vals),
            "mean": round(sum(vals) / len(vals), 3),
            "p50": _percentile(vals, 50),
            "p95": _percentile(vals, 95),
            "max": round(max(vals), 3),
        }

    return {
        "window_hours": hours,
        "total": total,
        "by_intent": summary,
        "fast_format": fast_format,
    }


@app.get("/metrics/quick_chat_cleanliness")
async def quick_chat_cleanliness(hours: int = 24):
    """Rolling cleanliness rate for the qwen3:4b quick_chat path
    (Fix #4 v2). Reads `~/AI_Agent/memory/quick_chat_cleanliness.jsonl`.

    Shape:
        {
          "window_hours": 24,
          "total": 87,
          "clean": 84,
          "leaked": 3,
          "clean_rate": 0.966,
          "fallback_used": 3,
          "leak_breakdown": {"denial": 1, "thinking": 2}
        }

    Target: >= 0.95 clean_rate. Below that, it's time to revisit the
    model choice or the strict-prompt config.
    """
    from datetime import datetime, timedelta, timezone
    import json as _json
    from pathlib import Path

    log_path = Path.home() / "AI_Agent" / "memory" / "quick_chat_cleanliness.jsonl"
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 24 * 30)))).timestamp()

    total = 0
    clean = 0
    fallback = 0
    leak_kinds: dict[str, int] = {}

    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = _json.loads(raw)
                    except _json.JSONDecodeError:
                        continue
                    ts = entry.get("ts", "")
                    if not isinstance(ts, str):
                        continue
                    try:
                        t = datetime.fromisoformat(ts).timestamp()
                    except ValueError:
                        continue
                    if t < cutoff:
                        continue
                    total += 1
                    if entry.get("clean"):
                        clean += 1
                    if entry.get("fallback_used"):
                        fallback += 1
                    lk = entry.get("leak_kind")
                    if lk:
                        leak_kinds[lk] = leak_kinds.get(lk, 0) + 1
        except OSError:
            pass

    return {
        "window_hours": hours,
        "total": total,
        "clean": clean,
        "leaked": total - clean,
        "clean_rate": round(clean / total, 3) if total else 1.0,
        "fallback_used": fallback,
        "leak_breakdown": leak_kinds,
    }


@app.get("/api/dispatches")
async def api_dispatches(limit: int = 30):
    """Phase 22 dashboard endpoint: queue snapshot + recent results.

    Single round-trip the dashboard polls/refreshes from. Combines the
    live queue (running/queued/pending) with the most recent results
    so the UI can render without juggling multiple endpoints."""
    from core import cc_dispatch as _ccd
    from dataclasses import asdict
    snap = _ccd.queue_summary()
    recent = [asdict(r) for r in _ccd.list_results(limit=limit)]
    level, spend, budget = _ccd.budget_status()
    return {
        "queue": snap,
        "recent": recent,
        "budget": {"level": level, "spend": spend, "monthly_budget": budget},
    }


class DispatchAPIRequest(BaseModel):
    """POST /api/dispatch — dashboard-facing dispatch entry point."""
    prompt: str
    label: str | None = None
    time_budget_minutes: int = 120
    force: bool = False


@app.post("/api/dispatch")
async def api_dispatch(req: DispatchAPIRequest):
    """Dashboard-facing dispatch. Mirrors the LangGraph tool path so the
    UI uses the same risky-pattern + budget gates. Returns dispatch_id
    immediately."""
    from core import cc_dispatch as _ccd
    if not req.prompt or not req.prompt.strip():
        return {"ok": False, "error": "empty prompt"}
    minutes = max(5, min(int(req.time_budget_minutes or 120), 480))
    level, spend, budget = _ccd.budget_status()
    if level == "over" and not req.force:
        return {
            "ok": False,
            "error": "budget exhausted",
            "spend": spend, "budget": budget,
        }
    risky = _ccd.is_risky(req.prompt)
    label = req.label or req.prompt.strip().splitlines()[0][:60]
    meta = _ccd.DispatchMeta.new(
        label=label, time_budget_minutes=minutes, risky_match=risky,
    )
    _ccd.write_prompt(meta, req.prompt, pending=bool(risky))
    return {
        "ok": True,
        "dispatch_id": meta.dispatch_id,
        "label": meta.label,
        "pending_approval": bool(risky),
        "risky_match": risky,
        "time_budget_minutes": minutes,
    }


class DispatchActionRequest(BaseModel):
    """POST /api/dispatch/{action} — approve / cancel / retry / extend."""
    dispatch_id: str
    extend_minutes: int | None = None


@app.post("/api/dispatch/approve")
async def api_dispatch_approve(req: DispatchActionRequest):
    from core import cc_dispatch as _ccd
    p = _ccd.approve(req.dispatch_id)
    return {"ok": p is not None, "dispatch_id": req.dispatch_id}


@app.post("/api/dispatch/cancel")
async def api_dispatch_cancel(req: DispatchActionRequest):
    from core import cc_dispatch as _ccd
    p = _ccd.cancel(req.dispatch_id)
    return {"ok": p is not None, "dispatch_id": req.dispatch_id}


@app.get("/api/services")
async def api_services():
    """Phase 22.4 + dashboard 2.6 — health snapshot of nexus-* units.
    Reads `systemctl is-active` for each default service. No sudo needed."""
    import subprocess as _sp
    from tools.restart_services_tool import DEFAULT_SERVICES
    out = []
    for name in DEFAULT_SERVICES:
        try:
            proc = _sp.run(
                ["/bin/systemctl", "is-active", f"{name}.service"],
                capture_output=True, text=True, timeout=3,
            )
            state = proc.stdout.strip() or "unknown"
        except Exception:
            state = "unknown"
        out.append({"service": name, "state": state})
    return {"services": out}


class RestartAPIRequest(BaseModel):
    services: list[str] | None = None


@app.post("/api/restart")
async def api_restart(req: RestartAPIRequest):
    from tools.restart_services_tool import restart_services_sync
    return restart_services_sync(req.services)


@app.get("/api/dispatch/{dispatch_id}/log")
async def api_dispatch_log(dispatch_id: str, tail: int = 200):
    """Tail the cc_logs/<id>.log file for the dashboard log viewer."""
    from core import cc_dispatch as _ccd
    path = _ccd.LOGS / f"{dispatch_id}.log"
    if not path.exists():
        return {"ok": False, "log": ""}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"ok": False, "log": ""}
    lines = text.splitlines()
    return {"ok": True, "log": "\n".join(lines[-max(1, tail):])}


@app.get("/api/memory/retros")
async def api_memory_retros(limit: int = 30):
    """Recent retros from memory/retros/. Used by Memory tab."""
    retros_dir = ROOT / "memory" / "retros"
    if not retros_dir.exists():
        return {"retros": []}
    files = sorted(retros_dir.glob("retro_*.md"),
                    key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append({
            "id": f.stem, "mtime": f.stat().st_mtime,
            "preview": text[:400], "size": len(text),
        })
    return {"retros": out}


@app.get("/api/memory/retro/{retro_id}")
async def api_memory_retro(retro_id: str):
    retros_dir = ROOT / "memory" / "retros"
    path = retros_dir / f"{retro_id}.md"
    if not path.exists():
        return {"ok": False, "body": ""}
    try:
        return {"ok": True, "body": path.read_text(encoding="utf-8")}
    except OSError:
        return {"ok": False, "body": ""}


@app.get("/api/agents")
async def list_agents_alias():
    """Dashboard alias for /agents — keeps API surface uniform under /api/."""
    return await list_agents()


@app.get("/agents")
async def list_agents():
    """Get status of all running agents and their tasks."""
    try:
        from agents.orchestrator import get_orchestrator
        orch = get_orchestrator()
        return orch.get_status()
    except Exception as e:
        return {"error": str(e), "agents": {}}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()

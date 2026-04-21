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
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
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
    build_agent,
    load_system_prompt,
    set_system_prompt,
    strip_thinking,
)

MODEL_NAME = "nexus"
HOST = "0.0.0.0"
PORT = 11435

app = FastAPI(title="nexus-api", version="0.4")
# Lock in the system prompt before any agent is built, then warm-start heavy.
set_system_prompt(load_system_prompt())
build_agent(router.model_for("heavy"))
_system_prompt = load_system_prompt()


def _last_user_text(msgs: list) -> str:
    for m in reversed(msgs):
        if m.role == "user" and m.content:
            return m.content
    return ""


def _pick_agent(messages: list) -> tuple[object, str, str]:
    route, model = router.classify_and_model(_last_user_text(messages))
    return build_agent(model), route, model


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
    agent, lc_msgs, config, user_msg: str, route: str, model: str
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
        yield _chunk_bytes(
            chunk_id, created, {"content": f"\n[stream error: {type(exc).__name__}: {exc}]"}
        )

    yield _chunk_bytes(chunk_id, created, {}, finish="stop")
    yield b"data: [DONE]\n\n"

    try:
        snap = agent.get_state(config)
        final_messages = getattr(snap, "values", {}).get("messages") if snap else None
    except Exception:
        final_messages = None
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
    agent, route, model = _pick_agent(req.messages)
    thread_id, _src = _thread_id_for(req)
    config = {"configurable": {"thread_id": thread_id}}

    # Checkpointer holds prior turns; we only pass the new user message.
    last = _last_user_message(req.messages)
    lc_msgs = [HumanMessage(content=last.content)] if last and last.content else []
    user_text = last.content if last and last.content else ""

    first_msg = last.content if last else None
    sessions.touch_session(thread_id, source="api", first_msg=first_msg)

    if req.stream:
        return StreamingResponse(
            _stream_agent(agent, lc_msgs, config, user_text, route, model),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    result = await agent.ainvoke({"messages": lc_msgs}, config=config)
    reply = _extract_reply(result)
    _spawn_reflection(user_text, reply, result.get("messages"), route, model)
    return _completion_envelope(reply)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "model": MODEL_NAME}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()

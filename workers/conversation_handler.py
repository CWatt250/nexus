"""Conversation handler (Phase 15.4 + 15.5).

A small, fast surface that answers Telegram / API messages about *running*
tasks without ever pulling a heavy model into the request path.

Two layers:

  1. Pattern-based intent classifier (`classify_intent`) handles the four
     status/modify/cancel/new-task shapes deterministically — no LLM in
     the loop. These are the operations the spec requires under <5s.
  2. Free-form chat falls through to a tiny ReAct agent on qwen3:4b that
     can still call any HANDLER_TOOLS, but with a short timeout so it
     never blocks Telegram.

Long-running work is never executed here. `queue_new_task` enqueues to
the task_worker (Phase 15.3). The handler keeps its own LangGraph
checkpointer namespace (`thread_id="handler:..."`) so its conversation
state stays isolated from any task's state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

import ollama  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import nexus  # noqa: E402  — loads tools, prompt, etc.
from core import task_queue  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

log = logging.getLogger("nexus.conversation_handler")

HANDLER_MODEL = "qwen3:4b"

# qwen3.6 outperforms qwen3:4b on intent classification by ~18x latency
# (517ms vs 9190ms mean) and is more accurate (10/10 vs 9/10 on the test
# set). qwen3:4b's chain-of-thought blows through num_predict on every
# call even with think=False. qwen3.6 follows the bare-label instruction
# directly. Both models are pinned by prewarm, so picking the better one
# is free.
CLASSIFIER_MODEL = "qwen3.6"
QUICK_CHAT_MODEL = "qwen3.6"

INTENT_SYSTEM_PROMPT = """Classify the user's message into exactly one label:
CHAT, QUERY_INLINE, QUERY_TOOL, TASK, or STATUS.

CHAT         — greetings, small talk, no real question or task.
               Examples: "hi", "hey", "what's up", "thanks", "lol nice"

QUERY_INLINE — factual question answerable in 1-2 sentences from general
               knowledge OR from the injected datetime. NO tool call needed.
               Examples: "what's 7+8", "what does TCP stand for",
                         "what time is it", "what day is it",
                         "explain a B-tree in one sentence"

QUERY_TOOL   — quick factual question that needs ONE tool call to answer,
               then a short reply. The user wants the answer NOW, inline,
               not a long task. Wall-clock target: under ~8 seconds.
               Examples: "what's the weather in Pasco WA",
                         "what's my github auth status",
                         "search for the latest news on Apple Vision Pro",
                         "do I have any open issues on the cli repo",
                         "list my github repos",
                         "search my notes for 'BidWatt schema'"
               Pick QUERY_TOOL when ONE web search, ONE GitHub call, ONE
               memory lookup, etc. is enough. If the user is asking for a
               summary, plan, build, research sweep, or anything that
               clearly needs >1 step, pick TASK instead.

TASK         — multi-step or long-running work, or anything that needs the
               full agent loop with all 91 tools.
               Examples: "research the top 5 AI agent frameworks and write
                         me a summary", "fix the bug in eod_summary.py",
                         "build a Next.js scaffold with auth",
                         "summarize this repo", "deploy to vercel",
                         anything starting with "queue:" (forced override)

STATUS — asking about Nexus's INTERNAL task queue or a specific task ID.
         The word "status" alone does NOT make it STATUS — context matters.
         INCLUDES:
         - "queue status", "what's in the queue", "any tasks running"
         - "is task abc12345 done", "status of task XYZ", "show task <id>"
         - "what are you working on", "any tasks in flight"
         EXCLUDES (these are TASK, not STATUS — they need a tool to answer):
         - "github auth status"        → TASK (calls github_auth_status)
         - "supabase status"           → TASK (calls a Supabase health tool)
         - "weather status"            → TASK (calls a weather lookup)
         - "ollama status"             → TASK (calls a local-API check)
         - "system status" / "wifi status" / any "<tool or domain> status"
         The cue is: STATUS is about Nexus's queue. TASK is about anything
         else, even if the user happens to say "status".
         Examples: "queue status", "is task abc12345 done",
                   "any tasks running right now", "what are you working on"

If you cannot tell the category, choose CHAT.
But if the message contains a URL or asks Nexus to look at/fetch external
content, ALWAYS choose TASK.

Output the label only — one word, nothing else."""

_LABEL_RE = re.compile(r"\b(CHAT|QUERY_INLINE|QUERY_TOOL|QUERY|TASK|STATUS)\b")


class Intent(BaseModel):
    """Result of LLM intent classification.

    `QUERY` (without _INLINE / _TOOL suffix) is kept for backwards
    compatibility — older callers still emit the bare label. When the
    classifier returns it, route_message normalises to QUERY_INLINE.
    """
    kind: Literal["CHAT", "QUERY_INLINE", "QUERY_TOOL", "QUERY", "TASK", "STATUS"] = Field(
        description="CHAT|QUERY_INLINE|QUERY_TOOL|TASK|STATUS"
    )
    raw: str = Field(default="", description="Raw model output for debugging.")


QUICK_CHAT_SYSTEM_PROMPT_BASE = (
    "You are Nexus — a fast, warm, terse personal assistant for Colton on his "
    "WattBott workstation. Reply in 2-3 sentences, conversational tone, no "
    "preamble, no <think> tags, no meta-commentary about how you'll answer. "
    "If the user is small-talking, banter back lightly. If they ask a quick "
    "factual question, answer directly. If you're not sure whether they want "
    "you to take action, end with a short offer like 'want me to dig into "
    "that?' so they can opt in to a real task.\n\n"
    "CAPABILITY RULES (critical):\n"
    "- You DO have tools — browser_tool, web search, GitHub, file read/write, "
    "  terminal, RAG memory, computer use, and ~85 more. Never say 'I can't "
    "  browse the web' or 'I don't have access to GitHub' or 'I can't view "
    "  files'. Those are wrong.\n"
    "- If the user asks you to do something that requires real-world data or "
    "  tool calls (browse a URL, look up live data, fetch external info, view "
    "  files, query a database, run a command), do NOT deny capability and do "
    "  NOT pretend to do it. Reply EXACTLY: 'Let me dig into that properly — "
    "  one sec' (and the system will re-route to the full agent).\n"
    "- For 'what can you do' / 'what tools do you have' / 'do you have "
    "  access to X', answer concretely from what you know about Nexus's tool "
    "  surface (web/GitHub/files/code/memory/computer-use/audio/image/etc.) "
    "  rather than reciting AI-assistant boilerplate."
)


# Phrases that flag a capability denial we want to retract. If any of
# these appear in quick_chat output, route_message will discard the reply
# and re-issue the message as a TASK so the agent can actually use tools.
_DENIAL_PATTERNS = [
    r"\bi can(?:not|'?t)\b",
    r"\bi do(?:n'?t| not) have (?:access|the ability)\b",
    r"\bi'?m (?:not able|unable)\b",
    r"\bi (?:lack|don'?t have) tools?\b",
    r"\bi (?:cannot|can'?t) (?:browse|access|view|fetch|open|read)\b",
    r"\bas an? (?:ai|language model|assistant), i (?:can'?t|cannot|don'?t)\b",
]
_DENIAL_RE = re.compile("|".join(_DENIAL_PATTERNS), re.IGNORECASE)


def _looks_like_denial(text: str) -> bool:
    """True if the model-generated reply contains a capability denial that
    Nexus actually has tools for. Used by route_message to recover."""
    return bool(_DENIAL_RE.search(text or ""))


def _datetime_context() -> str:
    """Real wall-clock context block for prompt injection.

    Models have no clock and will hallucinate dates from training data
    (e.g. qwen3.6 has been observed making up 'May 24, 2024'). Always
    inject this before any chat/query/task path that might reference
    'now', 'today', 'this week'.
    """
    now = datetime.now().astimezone()
    return (
        f"Current date and time: {now.isoformat(timespec='seconds')}. "
        f"Current day of week: {now.strftime('%A')}. "
        "When asked about the current time, date, or day, use ONLY the "
        "datetime above. Never guess or use training data."
    )


def quick_chat(message: str) -> str:
    """Inline conversational reply on qwen3.6 for CHAT and QUERY intents.

    2-3 sentences, no tools, no agent loop, no checkpoint state. ~1-2s
    warm. Strips any leaked <think> blocks defensively. Real datetime is
    injected into the system prompt every call so the model can answer
    "what time is it" correctly instead of hallucinating from training data.
    """
    system_prompt = f"{QUICK_CHAT_SYSTEM_PROMPT_BASE}\n\n{_datetime_context()}"
    try:
        resp = ollama.Client(host=nexus.OLLAMA_URL).chat(
            model=QUICK_CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            options={"temperature": 0.5, "num_ctx": 4096, "num_predict": 250},
            keep_alive=-1,
            think=False,
        )
    except Exception as exc:
        return f"(quick_chat error: {type(exc).__name__}: {exc})"
    body = (resp.get("message", {}) or {}).get("content", "").strip()
    return nexus.strip_thinking(body) if hasattr(nexus, "strip_thinking") else body


LITE_AGENT_TIMEOUT_S = 15.0
LITE_AGENT_PICKER_BUDGET = 5.0
LITE_AGENT_TOOL_BUDGET = 6.0
LITE_AGENT_FORMATTER_BUDGET = 4.0
LITE_AGENT_MODEL = "qwen3.6"


_PICKER_SYSTEM = (
    "You are a tool router. Given a user question, pick the SINGLE best "
    "tool from the list below and the args to call it with. Return ONLY "
    "a JSON object of the form: "
    '{"tool": "<exact tool name>", "args": {"<arg>": "<value>", ...}}. '
    "No prose. No reasoning. No <think> tags.\n\n"
    'If no listed tool fits, return {"tool": "_none", "args": {}}.'
)


_FORMATTER_SYSTEM = (
    "Write a 2-3 sentence answer to the user's question using the tool "
    "result below. Plain prose. No preamble. No reasoning. No <think> tags. "
    "If the tool returned an error, say so plainly and suggest an alternative "
    "in 1 sentence. Never echo the raw tool output verbatim."
)


def _ollama_chat(messages: list[dict], *, timeout: float, num_predict: int = 250,
                 fmt: str | None = None) -> str:
    """One-shot Ollama call with explicit per-call timeout. Returns the
    text content (already think-stripped) or raises on timeout/error."""
    client = ollama.Client(host=nexus.OLLAMA_URL, timeout=timeout)
    kwargs: dict = {
        "model": LITE_AGENT_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": -1,
        "options": {"temperature": 0.1, "num_predict": num_predict, "num_ctx": 4096},
    }
    if fmt:
        kwargs["format"] = fmt
    resp = client.chat(**kwargs)
    body = (resp.get("message", {}) or {}).get("content", "") or ""
    if hasattr(nexus, "strip_thinking"):
        body = nexus.strip_thinking(body)
    return body.strip()


def _pick_tool(message: str) -> dict | None:
    """First leg of lite_agent: ask qwen3.6 to pick one tool + args.
    Returns {"tool": str, "args": dict} or None on parse failure."""
    from tools.lite_agent_tools import picker_prompt_block  # noqa: PLC0415
    user_prompt = (
        f"{picker_prompt_block()}\n\n"
        f"User question: {message}\n\n"
        'Respond with the JSON object only.'
    )
    try:
        body = _ollama_chat(
            [
                {"role": "system", "content": _PICKER_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            timeout=LITE_AGENT_PICKER_BUDGET,
            num_predict=200,
            fmt="json",
        )
    except Exception as exc:
        log.warning("lite_agent picker failed: %s", exc)
        return None
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        log.warning("lite_agent picker returned non-JSON: %r", body[:160])
        return None
    if not isinstance(obj, dict) or "tool" not in obj:
        return None
    return obj


def _format_answer(message: str, tool_name: str, tool_result: str) -> str:
    """Second leg: turn the tool result into a 2-3 sentence reply."""
    user_prompt = (
        f"User question: {message}\n\n"
        f"Tool used: {tool_name}\n"
        f"Tool result:\n{(tool_result or '')[:4000]}"
    )
    try:
        body = _ollama_chat(
            [
                {"role": "system", "content": _FORMATTER_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            timeout=LITE_AGENT_FORMATTER_BUDGET,
            num_predict=250,
        )
    except Exception as exc:
        log.warning("lite_agent formatter failed: %s", exc)
        # Last-ditch: trim raw result so the user gets *something*.
        return (tool_result or "").strip()[:600] or f"({tool_name} returned no output)"
    return body or f"({tool_name} returned no output)"


def lite_agent(message: str) -> dict:
    """Two-LLM-call + one-tool-call fast path.

    Picks a tool from `tools.lite_agent_tools.get_registry()`, invokes
    it, then asks qwen3.6 to format the result into 2-3 sentences.
    Hard wall at LITE_AGENT_TIMEOUT_S (15 s) — beyond that we return
    a sentinel and let `route_message` fall through to TASK enqueue.

    Returns:
        {"ok": True,  "reply": str, "tool": str}
        {"ok": False, "reason": str}      # caller should fall through to TASK
    """
    import time as _time  # noqa: PLC0415
    started = _time.monotonic()

    def _budget_left() -> float:
        return LITE_AGENT_TIMEOUT_S - (_time.monotonic() - started)

    # 1. Pick a tool.
    if _budget_left() < LITE_AGENT_PICKER_BUDGET:
        return {"ok": False, "reason": "timeout before picker"}
    pick = _pick_tool(message)
    if not pick:
        return {"ok": False, "reason": "picker returned no JSON"}
    tool_name = (pick.get("tool") or "").strip()
    args = pick.get("args") or {}
    if tool_name == "_none":
        return {"ok": False, "reason": "picker chose _none"}
    if not isinstance(args, dict):
        return {"ok": False, "reason": "picker args not a dict"}

    from tools.lite_agent_tools import get_registry  # noqa: PLC0415
    registry = get_registry()
    if tool_name not in registry:
        return {"ok": False, "reason": f"tool {tool_name!r} not in lite registry"}

    # 2. Invoke the tool with whatever budget remains.
    if _budget_left() < LITE_AGENT_FORMATTER_BUDGET + 0.5:
        return {"ok": False, "reason": "timeout before tool call"}
    tool_obj = registry[tool_name]["tool"]
    try:
        tool_result = tool_obj.invoke(args)
    except Exception as exc:
        tool_result = f"ERROR: {type(exc).__name__}: {exc}"
        log.warning("lite_agent tool %s raised: %s", tool_name, exc)

    # 3. Format. If we're past budget, skip formatting and return raw.
    if _budget_left() < 0.5:
        log.info("lite_agent skipping formatter — out of budget")
        return {
            "ok": True,
            "tool": tool_name,
            "reply": (str(tool_result) or "")[:600],
        }
    reply = _format_answer(message, tool_name, str(tool_result))
    return {"ok": True, "tool": tool_name, "reply": reply}


def classify_intent_llm(message: str) -> Intent:
    """LLM-based intent classifier on qwen3.6. ~500ms warm.

    Returns an `Intent` Pydantic object. Defaults to CHAT on parse failure
    so the user gets a conversational reply instead of an unwanted task.
    """
    msg = (message or "").strip()
    if not msg:
        return Intent(kind="CHAT", raw="")
    try:
        resp = ollama.Client(host=nexus.OLLAMA_URL).chat(
            model=CLASSIFIER_MODEL,
            messages=[
                {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": msg},
            ],
            options={"temperature": 0, "num_ctx": 2048, "num_predict": 50},
            keep_alive=-1,
            think=False,
        )
    except Exception as exc:
        log.warning("classify_intent_llm failed (%s); defaulting to CHAT", exc)
        return Intent(kind="CHAT", raw=f"error: {exc}")
    raw = (resp.get("message", {}) or {}).get("content", "").strip()
    matches = _LABEL_RE.findall(raw.upper())
    if not matches:
        log.info("classify_intent_llm: no label in %r; defaulting to CHAT", raw[:80])
        return Intent(kind="CHAT", raw=raw)
    return Intent(kind=matches[-1], raw=raw)  # type: ignore[arg-type]
HANDLER_PROMPT = (
    "You are Nexus's conversation handler. You only manage tasks — you do "
    "NOT run them. Use tools to inspect the task queue, pause, cancel, or "
    "modify in-flight tasks, and to queue new tasks for the worker. Reply "
    "in 1-3 short sentences. If asked anything you can't answer with these "
    "tools, queue_new_task and tell the user you've handed it off."
)


@tool
def get_task_status(task_id: str = "") -> str:
    """Return status of one task (when task_id is given) or a list of the
    most recent tasks (when omitted)."""
    if task_id:
        row = task_queue.get_task(task_id)
        if not row:
            return f"no task with id {task_id}"
        return json.dumps({
            "task_id": row["task_id"],
            "status": row["status"],
            "kind": row["kind"],
            "thread_id": row["thread_id"],
            "input_preview": (row["input"] or "")[:160],
            "output_preview": (row.get("output") or "")[:160],
            "error": row.get("error"),
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "modifications": row.get("modifications"),
        }, ensure_ascii=False)
    rows = task_queue.list_tasks(limit=10)
    if not rows:
        return "queue empty"
    out_lines = []
    for r in rows:
        out_lines.append(
            f"{r['task_id']}  status={r['status']}  "
            f"started={r['started_at'] or '-'}  "
            f"input={(r['input'] or '')[:60]!r}"
        )
    return "\n".join(out_lines)


@tool
def pause_task(task_id: str) -> str:
    """Pause a running task (worker checks status between turns)."""
    return f"paused" if task_queue.pause(task_id) else "not running — nothing to pause"


@tool
def cancel_task(task_id: str, note: str = "") -> str:
    """Cancel a pending/running/paused task. Worker stops before its next turn."""
    return "cancelled" if task_queue.cancel(task_id, note) else "already finished — nothing to cancel"


@tool
def modify_task(task_id: str, note: str) -> str:
    """Append a modification note to a task's history. The worker reads these
    between turns so the user can refine scope without re-queuing."""
    task_queue.append_modification(task_id, note)
    return "noted"


@tool
def queue_new_task(input_text: str, priority: int = 0) -> str:
    """Enqueue a new heavy task for the worker to pick up. Returns the task_id."""
    if not input_text.strip():
        return "refusing: empty input"
    tid = task_queue.enqueue(input_text, priority=int(priority))
    return f"queued task {tid}"


HANDLER_TOOLS = [get_task_status, pause_task, cancel_task, modify_task, queue_new_task]


def _build_handler_agent_sync():
    """Build a sync ReAct agent on qwen3:4b with HANDLER_TOOLS only.

    Uses the existing _CHECKPOINTER (sync) namespaced via thread_id so the
    handler's conversation state never collides with any task's state."""
    from langgraph.prebuilt import create_react_agent
    from langchain_ollama import ChatOllama
    llm = ChatOllama(model=HANDLER_MODEL, base_url=nexus.OLLAMA_URL, reasoning=False)
    return create_react_agent(llm, HANDLER_TOOLS, prompt=HANDLER_PROMPT, checkpointer=nexus._CHECKPOINTER)


_handler_agent = None


def get_agent():
    global _handler_agent
    if _handler_agent is None:
        _handler_agent = _build_handler_agent_sync()
    return _handler_agent


_HANDLER_SAVER = None


async def _get_handler_saver():
    """Build a dedicated AsyncSqliteSaver on its own aiosqlite connection
    so handler turns don't queue behind the task worker's heavy checkpoint
    writes. Both connections share the same WAL'd checkpoints.db file."""
    global _HANDLER_SAVER
    if _HANDLER_SAVER is None:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        conn = await aiosqlite.connect(str(nexus.CHECKPOINT_DB), check_same_thread=False)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.commit()
        saver = AsyncSqliteSaver(conn)
        try:
            await saver.setup()
        except Exception:
            pass
        _HANDLER_SAVER = saver
    return _HANDLER_SAVER


async def _build_handler_agent_async():
    from langgraph.prebuilt import create_react_agent
    from langchain_ollama import ChatOllama
    saver = await _get_handler_saver()
    llm = ChatOllama(model=HANDLER_MODEL, base_url=nexus.OLLAMA_URL, reasoning=False)
    return create_react_agent(llm, HANDLER_TOOLS, prompt=HANDLER_PROMPT, checkpointer=saver)


_handler_agent_async = None


async def get_agent_async():
    global _handler_agent_async
    if _handler_agent_async is None:
        _handler_agent_async = await _build_handler_agent_async()
    return _handler_agent_async


_TASK_ID_RE = re.compile(r"\b([0-9a-f]{8,16})\b", re.IGNORECASE)
_STATUS_VERB_RE = re.compile(
    r"\b(status|state|progress|how[\s_-]*is|what'?s|where['\s_-]*is|check|where stand)\b",
    re.IGNORECASE,
)

# Words that mean "you're asking about Nexus's task queue, not some
# other subject's status". If a STATUS-classified message contains none
# of these, it's almost certainly "<tool or domain> status" and should
# be re-routed to TASK so the agent calls the right tool.
_STATUS_QUEUE_TRIGGERS_RE = re.compile(
    r"\b("
    r"queue|task|tasks|jobs?|nexus(?:'s)?|"
    r"work(?:load)?|in[-\s]+flight|running|active|pending|recent|"
    r"done|complete|stuck|blocked|backlog"
    r")\b",
    re.IGNORECASE,
)


def _is_genuine_queue_status(msg: str) -> bool:
    """True if the message clearly references the Nexus task queue.
    False if it just happens to contain "status" applied to some other
    subject (e.g. "github auth status" → wants github_auth_status tool,
    not a queue lookup)."""
    if not msg:
        return False
    if _TASK_ID_RE.search(msg):  # explicit task_id always counts
        return True
    return bool(_STATUS_QUEUE_TRIGGERS_RE.search(msg))


# The TASK enqueue path prepends a "[Current date and time: ...]\n\n"
# block so the agent has wall-clock context. The STATUS list view shows
# the stored input — strip the prefix so the user sees their own
# message, not the injected context.
_DATETIME_PREFIX_RE = re.compile(
    r"^\s*\[Current date and time:.*?\]\s*\n+",
    re.DOTALL,
)


def _strip_datetime_prefix(text: str) -> str:
    """Remove the leading '[Current date and time: ...]\\n\\n' block from
    a stored task input. Returns the user's original message. Idempotent
    and a no-op for inputs without the prefix."""
    if not text:
        return text
    return _DATETIME_PREFIX_RE.sub("", text, count=1)
_CANCEL_VERB_RE = re.compile(r"\b(cancel|abort|kill|stop)\b", re.IGNORECASE)
_PAUSE_VERB_RE = re.compile(r"\b(pause|hold)\b", re.IGNORECASE)
_LIST_VERB_RE = re.compile(r"\b(list|show|recent|what.*tasks?|all\s+tasks?)\b", re.IGNORECASE)
_QUEUE_VERB_RE = re.compile(
    r"^(queue|enqueue|new\s+task|add\s+task|launch|run|please)\b[: ]?\s*(.*)",
    re.IGNORECASE,
)
_MODIFY_VERB_RE = re.compile(
    r"\b(modify|note|update|append|add\s+note|annotate)\b",
    re.IGNORECASE,
)


def classify_intent(message: str) -> dict:
    """Cheap pattern-based intent classifier. Returns one of:
      {kind: 'status', task_id}
      {kind: 'list'}
      {kind: 'cancel', task_id} | 'pause'
      {kind: 'modify', task_id, note}
      {kind: 'queue', input}
      {kind: 'chat'}
    """
    msg = (message or "").strip()
    if not msg:
        return {"kind": "chat"}
    tid_match = _TASK_ID_RE.search(msg)
    tid = tid_match.group(1) if tid_match else None
    if _CANCEL_VERB_RE.search(msg) and tid:
        return {"kind": "cancel", "task_id": tid}
    if _PAUSE_VERB_RE.search(msg) and tid:
        return {"kind": "pause", "task_id": tid}
    if _MODIFY_VERB_RE.search(msg) and tid:
        note = _TASK_ID_RE.sub("", msg)
        for re_ in (_MODIFY_VERB_RE, _STATUS_VERB_RE):
            note = re_.sub("", note, count=1)
        note = note.strip(" :,.;-")
        if note:
            return {"kind": "modify", "task_id": tid, "note": note}
    if tid and (_STATUS_VERB_RE.search(msg) or len(msg) < 40):
        return {"kind": "status", "task_id": tid}
    if _LIST_VERB_RE.search(msg) and not tid:
        return {"kind": "list"}
    qm = _QUEUE_VERB_RE.match(msg)
    if qm:
        body = qm.group(2).strip(" :")
        if body:
            return {"kind": "queue", "input": body}
    return {"kind": "chat"}


_STATUS_OUTPUT_PREVIEW = 800


def _format_status(row: dict | None, task_id: str) -> str:
    if not row:
        return f"no task with id {task_id}"
    bits = [f"task {row['task_id']} → {row['status']}", f"created {row['created_at']}"]
    user_input = _strip_datetime_prefix(row.get("input") or "")
    if user_input:
        preview = user_input.strip().splitlines()[0][:200]
        bits.append(f"input: {preview}")
    if row.get("started_at"):
        bits.append(f"started {row['started_at']}")
    if row.get("finished_at"):
        bits.append(f"finished {row['finished_at']}")
    output = row.get("output") or ""
    if output:
        # Stop mid-sentence cutoff. Show a clean preview + total length so
        # the user knows there's more, then say how to ask for it.
        if len(output) > _STATUS_OUTPUT_PREVIEW:
            preview = output[:_STATUS_OUTPUT_PREVIEW].rstrip()
            # Cut on a sentence/word boundary if one is nearby.
            for sep in ("\n", ". ", "; ", ", ", " "):
                cut = preview.rfind(sep)
                if cut >= _STATUS_OUTPUT_PREVIEW - 80:
                    preview = preview[:cut]
                    break
            bits.append(
                f"output preview ({_STATUS_OUTPUT_PREVIEW} of {len(output)} chars):\n{preview}\n"
                f"…full output: {len(output)} chars. "
                f"Send 'full output {row['task_id']}' to get the rest."
            )
        else:
            bits.append("output:\n" + output)
    if row.get("error"):
        bits.append(f"error: {row['error']}")
    return "\n".join(bits)


def _busy_summary() -> str:
    """One-line summary of any running task; empty string if idle."""
    rows = task_queue.list_tasks(limit=10)
    running = [r for r in rows if r["status"] == "running"]
    if not running:
        return ""
    r = running[0]
    return f"running: {r['task_id']} ({(r['input'] or '')[:60]})"


def fast_handle(message: str, *, allow_llm_chat: bool = True) -> str | None:
    """Synchronous, no-LLM fast path. Returns a reply for status/list/
    cancel/pause/modify/queue intents in microseconds. For chat with a
    running task, returns a busy-with-task template (still <5s).

    Returns None only when the caller has set `allow_llm_chat=True` AND
    the queue is idle — in which case the LLM fallback is allowed to
    answer free-form chat. With the default allow_llm_chat=True we keep
    that escape hatch; for Telegram-bot path the caller passes False so
    chat never blocks on a contended LLM."""
    intent = classify_intent(message)
    kind = intent["kind"]
    if kind == "list":
        rows = task_queue.list_tasks(limit=10)
        if not rows:
            return "Queue is empty."
        return "\n".join(
            f"- {r['task_id']} [{r['status']}] {(r['input'] or '')[:60]}" for r in rows
        )
    if kind == "status":
        return _format_status(task_queue.get_task(intent["task_id"]), intent["task_id"])
    if kind == "cancel":
        ok = task_queue.cancel(intent["task_id"], note="cancelled via handler")
        return "cancelled." if ok else "already finished — nothing to cancel."
    if kind == "pause":
        ok = task_queue.pause(intent["task_id"])
        return "paused." if ok else "not running — nothing to pause."
    if kind == "modify":
        task_queue.append_modification(intent["task_id"], intent["note"])
        return f"noted on task {intent['task_id']}."
    if kind == "queue":
        tid = task_queue.enqueue(intent["input"])
        return f"queued task {tid}."
    # kind == 'chat'
    busy = _busy_summary()
    if busy:
        return (
            f"I'm here. A task is in flight — {busy}. "
            "For free-form chat without contention, wait until it finishes "
            "or send: 'queue: <your task>' to enqueue."
        )
    return None if allow_llm_chat else (
        "I'm here. The queue is idle. Send 'queue: <your task>' to launch one, "
        "or '<task_id>' to check a specific task's status."
    )


_QUEUE_PREFIX_RE = re.compile(r"^\s*queue\s*[:>]\s*(.+)$", re.IGNORECASE | re.DOTALL)
_FULL_OUTPUT_RE = re.compile(
    r"^\s*(?:full|whole|complete|show)\s+(?:output|result)\s+([0-9a-f]{8,16})\s*$",
    re.IGNORECASE,
)


def _full_output_reply(task_id: str) -> str:
    row = task_queue.get_task(task_id)
    if not row:
        return f"no task with id {task_id}"
    output = row.get("output") or ""
    if not output:
        return f"task {task_id} has no output yet (status={row['status']})"
    if len(output) <= 3500:
        return f"task {task_id} full output ({len(output)} chars):\n\n{output}"
    return (
        f"task {task_id} full output is {len(output)} chars — too big for a single Telegram "
        f"message. First 3500 chars below; ask again for the next chunk if needed.\n\n"
        f"{output[:3500]}"
    )


def _route_status(message: str) -> str:
    """STATUS branch: if message contains a task_id, return that task's
    detail; otherwise list the recent queue. Strips the injected
    datetime-context prefix so the user sees their own input, not the
    "Current date and time: ..." preamble route_message attaches."""
    tid_match = _TASK_ID_RE.search(message)
    if tid_match:
        tid = tid_match.group(1)
        row = task_queue.get_task(tid)
        if row:
            return _format_status(row, tid)
    rows = task_queue.list_tasks(limit=10)
    if not rows:
        return "Queue is empty — no recent tasks."

    def _line(r: dict) -> str:
        clean_input = _strip_datetime_prefix(r["input"] or "").strip()
        clean_input = clean_input.splitlines()[0] if clean_input else ""
        return f"- {r['task_id']} [{r['status']}] {clean_input[:60]}"

    return "Recent tasks:\n" + "\n".join(_line(r) for r in rows)


def route_message(message: str) -> dict:
    """Top-level Telegram/API router (Phase-15 conversation UX rewrite).

    Returns {kind, reply, meta}:
      - kind: 'queue' | 'chat' | 'query' | 'task' | 'status' | 'empty'
      - reply: the text to send back to the user
      - meta: {'task_id', 'classifier_raw', ...} for logging

    Flow:
      1. Empty input -> nudge reply.
      2. 'queue: <text>' prefix -> enqueue immediately (power-user override).
      3. LLM intent classifier runs once.
      4. Route on intent:
         - CHAT / QUERY_INLINE -> qwen3.6 inline reply via quick_chat()
         - QUERY_TOOL          -> lite_agent (one tool, ~8s budget)
         - TASK                -> enqueue, reply "On it. task_id=xxx"
         - STATUS              -> task_id lookup or queue list
    """
    msg = (message or "").strip()
    if not msg:
        return {"kind": "empty", "reply": "(empty message)", "meta": {}}

    # Power-user override: "queue: <task>"
    qm = _QUEUE_PREFIX_RE.match(msg)
    if qm:
        body = qm.group(1).strip()
        if not body:
            return {"kind": "queue", "reply": "queue: needs a task body.", "meta": {}}
        tid = task_queue.enqueue(body)
        log.info("route: queue-prefix override -> task %s", tid)
        return {"kind": "queue", "reply": f"On it. task_id={tid}", "meta": {"task_id": tid}}

    # Deterministic: "full output <task_id>" — return the unchunked
    # output from the row, since STATUS replies preview at 800 chars.
    fm = _FULL_OUTPUT_RE.match(msg)
    if fm:
        tid = fm.group(1).lower()
        return {"kind": "status", "reply": _full_output_reply(tid), "meta": {"task_id": tid}}

    # LLM classifier
    intent = classify_intent_llm(msg)
    meta = {"classifier_raw": intent.raw}
    log.info("route: classified %r as %s", msg[:60], intent.kind)

    # Backwards-compat: legacy callers / older classifier outputs return
    # the bare "QUERY" label. Treat it as QUERY_INLINE (the old behavior).
    if intent.kind == "QUERY":
        intent = Intent(kind="QUERY_INLINE", raw=intent.raw + " [legacy:query]")

    # Override: classifier loves to keyword-match "status" without context.
    # If it picked STATUS but the message doesn't reference the queue or
    # a task_id, it's almost always something like "github auth status"
    # that needs a tool call — promote to QUERY_TOOL so the lite_agent
    # picks github_auth_status. (Promote to TASK is too heavy for that.)
    if intent.kind == "STATUS" and not _is_genuine_queue_status(msg):
        log.info("STATUS→QUERY_TOOL override: %r doesn't reference queue", msg[:80])
        meta["status_override"] = True
        intent = Intent(kind="QUERY_TOOL", raw=intent.raw + " [override:status->query_tool]")

    if intent.kind == "QUERY_TOOL":
        # Single-tool fast path. Falls through to TASK enqueue if it can't
        # pick a tool, blows its budget, or the tool errors hard.
        result = lite_agent(msg)
        if result.get("ok"):
            return {
                "kind": "query_tool",
                "reply": result["reply"],
                "meta": {**meta, "tool": result.get("tool", "")},
            }
        log.info("lite_agent fell through to TASK: %s", result.get("reason"))
        meta["lite_agent_fallthrough"] = result.get("reason")
        # fall through to TASK enqueue below
        intent = Intent(kind="TASK", raw=intent.raw + " [fallthrough:lite_agent]")

    if intent.kind == "TASK":
        # Prepend real datetime so the spawned agent doesn't hallucinate
        # "today" / "this week" from training data.
        enqueued_input = f"[{_datetime_context()}]\n\n{msg}"
        tid = task_queue.enqueue(enqueued_input)
        return {"kind": "task", "reply": f"On it. task_id={tid}", "meta": {**meta, "task_id": tid}}

    if intent.kind == "STATUS":
        return {"kind": "status", "reply": _route_status(msg), "meta": meta}

    # CHAT or QUERY: inline reply on qwen3.6
    reply = quick_chat(msg)

    # Capability self-check: qwen3.6 sometimes denies tool access despite
    # the capability rules in the system prompt. If the reply reads like a
    # denial, treat it as a misclassified TASK — re-route, enqueue, and
    # discard the bad text so the user gets a real answer instead.
    if _looks_like_denial(reply):
        log.warning(
            "quick_chat produced denial — recovering as TASK. "
            "denial_text=%r intent_was=%s msg=%r",
            reply[:160], intent.kind, msg[:120],
        )
        enqueued_input = f"[{_datetime_context()}]\n\n{msg}"
        tid = task_queue.enqueue(enqueued_input)
        return {
            "kind": "task",
            "reply": f"On it. task_id={tid}",
            "meta": {**meta, "task_id": tid, "recovered_from": "denial"},
        }

    return {"kind": intent.kind.lower(), "reply": reply, "meta": meta}


def handle_sync(message: str, *, thread_id: str = "handler:default") -> str:
    """Sync handler entrypoint. Tries the no-LLM fast path first; falls
    back to the qwen3:4b ReAct agent for free-form chat."""
    fast = fast_handle(message)
    if fast is not None:
        return fast
    agent = get_agent()
    config = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, config=config)
    msgs = result.get("messages", [])
    for m in reversed(msgs):
        if m.__class__.__name__ == "AIMessage" and getattr(m, "content", ""):
            return nexus.strip_thinking(m.content)
    return ""


async def handle_async(message: str, *, thread_id: str = "handler:default") -> str:
    """Async handler entrypoint. Same two-tier strategy as handle_sync."""
    fast = fast_handle(message)
    if fast is not None:
        return fast
    agent = await get_agent_async()
    config = {"configurable": {"thread_id": thread_id}}
    result = await agent.ainvoke({"messages": [HumanMessage(content=message)]}, config=config)
    msgs = result.get("messages", [])
    for m in reversed(msgs):
        if m.__class__.__name__ == "AIMessage" and getattr(m, "content", ""):
            return nexus.strip_thinking(m.content)
    return ""


def main() -> int:
    """CLI smoke entrypoint: read a single line from argv, print the handler reply."""
    if len(sys.argv) < 2:
        print("usage: conversation_handler.py <message>", file=sys.stderr)
        return 2
    msg = " ".join(sys.argv[1:])
    print(handle_sync(msg))
    return 0


if __name__ == "__main__":
    sys.exit(main())

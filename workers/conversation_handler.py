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

# Quick-chat is the QUERY_INLINE / CHAT path — short factual answers,
# no tools, no agent loop. qwen3:4b is ~5x faster wall-clock than
# qwen3.6 (warm: ~1.5s vs ~8s). It denies capabilities slightly more
# often, which we catch via _looks_like_denial and retry on the
# heavier model exactly once. Net win: most turns land in 1-2s, the
# rare denial pays the qwen3.6 round-trip but still produces a real
# answer.
QUICK_CHAT_MODEL = "qwen3:4b"
QUICK_CHAT_DENIAL_FALLBACK_MODEL = "qwen3.6"

INTENT_SYSTEM_PROMPT = """Classify the user's message into exactly one label:
CHAT, QUERY_INLINE, QUERY_TOOL, TASK, or STATUS.

CHAT         — greetings, small talk, no real question or task.
               Examples: "hi", "hey", "what's up", "thanks", "lol nice"

QUERY_INLINE — factual question answerable in 1-2 sentences from general
               knowledge, the injected datetime, or PERSONAL FACTS already
               injected in the system prompt (Mem0 / RAG memories about
               Colton — name, role, employer, preferences, projects, family,
               etc.). NO tool call needed.
               Examples: "what's 7+8", "what does TCP stand for",
                         "what time is it", "what day is it",
                         "explain a B-tree in one sentence",
                         "what's my name", "what's my favorite color",
                         "where do I work", "what's my role",
                         "who am I", "what am I working on this week",
                         "what did I tell you about my dog"
               Personal-fact recall is ALWAYS QUERY_INLINE, never TASK —
               the answer is either in the prompt context or it isn't,
               and a 30-min agent loop won't change that.

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
               full agent loop with all 91 tools. TASK only fires on
               IMPERATIVE statements with a SPECIFIC OBJECT — "Build X",
               "Fix Y", "Add Z to file W", "Refactor module M".
               Examples: "research the top 5 AI agent frameworks and write
                         me a summary", "fix the bug in eod_summary.py",
                         "build a Next.js scaffold with auth",
                         "summarize this repo", "deploy to vercel",
                         anything starting with "queue:" (forced override)
               COUNTER-EXAMPLES that look task-ish but are actually CHAT
               (no specific object, just hype/openness):
               - "are you ready to build something?"  → CHAT
               - "want to ship something cool?"        → CHAT
               - "feel like coding tonight?"           → CHAT
               - "should we do anything fun?"          → CHAT
               - "let's build something" (no object)   → CHAT
               These need a clarifying reply, not 50 tool calls. Wait for
               the user to name the actual thing they want built.

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


# ── SOUL.md — single source of truth for Nexus's identity, tone, length
# rules, slang glossary, uncertainty rules, and conventions. Loaded once
# at module import and cached. Restart any consumer of this module to
# pick up SOUL.md edits. Mirror of how `nexus.load_static_prefix` works
# for the heavy agent.
_SOUL_PATH = Path.home() / "AI_Agent" / "SOUL.md"
_SOUL_CACHE: str | None = None


def load_soul() -> str:
    """Read SOUL.md from disk, cache, and return. Best-effort — never
    raises. If SOUL.md is missing, returns empty string and quick_chat
    falls back to just the output/capability rules below (graceful but
    visible — the bot's personality vanishes)."""
    global _SOUL_CACHE
    try:
        _SOUL_CACHE = _SOUL_PATH.read_text(encoding="utf-8")
    except OSError:
        _SOUL_CACHE = ""
    return _SOUL_CACHE


# Eager load at import so every quick_chat / lite_agent call sees a hot
# cache. Recovery: a service restart re-imports the module → re-reads
# SOUL.md from disk.
load_soul()


# Output / capability rules specific to the qwen3:4b quick_chat path.
# Personality, tone, length, and slang were moved into SOUL.md; only
# the bits that wouldn't make sense in a global persona file stay here:
#   - format constraints required by JSON-mode + tight num_predict,
#   - the routing escape ("Let me dig into that properly") that's a
#     quick_chat-specific protocol with route_message,
#   - tool-surface awareness so the model doesn't deny capabilities.
_QUICK_CHAT_OUTPUT_RULES = (
    # Strict prefix — qwen3:4b ignores 'no preamble' phrasing but obeys
    # this when paired with format=json + low num_predict.
    "OUTPUT ONLY THE FINAL ANSWER. Do NOT explain your reasoning. "
    "Do NOT count sentences. Do NOT think out loud. Do NOT meta-comment. "
    "Respond as if the user can only see your final words. If you catch "
    "yourself starting to reason, STOP and just give the answer.\n\n"
    # Phase 30 — qwen3:4b kept emitting untagged CoT after the SOUL.md
    # tone fix (probably triggered by the new "Reply vocabulary" section
    # being verbose enough to invite analysis). Generic "don't reason"
    # rules slid off; the model honors a literal anti-pattern list.
    "FORBIDDEN OPENERS — do NOT start the reply with these or anything "
    "structurally like them:\n"
    "  - 'User says ...' / 'The user is asking ...' / 'User asked ...'\n"
    "  - 'Best reply: \"...\"' / 'Final reply: ...' / 'My reply: ...'\n"
    "  - 'First, gotta ...' / 'First, check ...' / 'First, match his energy'\n"
    "  - 'I must respond ...' / 'I should respond ...' / 'I need to ...'\n"
    "  - 'Key points: ...' / 'Possible replies: ...' / 'Following the rules ...'\n"
    "  - 'We are in a situation where ...' / 'As Nexus, I ...' / 'Classic Colton'\n"
    "  - 'Okay, let's break this down ...' / 'Let me think about this'\n"
    "Just emit the reply itself. No analysis sentence in front of it. No "
    "quoted 'Best reply:' framing. No 'Why?' explanation after. The first "
    "character of your response is the first character of the answer.\n\n"
    "CAPABILITY RULES (critical):\n"
    "- You DO have tools — browser_tool, web search, GitHub, file read/write, "
    "  terminal, RAG memory, computer use, and ~85 more. Never say 'I can't "
    "  browse the web' or 'I don't have access to GitHub' or 'I can't view "
    "  files'. Those are wrong.\n"
    "- If the user asks you to do something that requires real-world data or "
    "  tool calls (browse a URL, look up live data, fetch external info, view "
    "  files, query a database, run a command), do NOT deny capability and do "
    "  NOT pretend to do it. Reply EXACTLY: 'Let me dig into that properly — "
    "  one sec' (and the system will re-route to the full agent). This is "
    "  the ONE place where 'dig into that' is allowed — never elsewhere.\n"
    "- For 'what can you do' / 'what tools do you have' / 'do you have "
    "  access to X', answer concretely from what you know about Nexus's tool "
    "  surface (web/GitHub/files/code/memory/computer-use/audio/image/etc.) "
    "  rather than reciting AI-assistant boilerplate."
)


def get_quick_chat_system_prompt() -> str:
    """Compose the qwen3:4b quick_chat system prompt: full SOUL.md
    (identity + tone + length + slang + uncertainty + conventions)
    followed by quick_chat-specific output and capability rules."""
    soul = _SOUL_CACHE if _SOUL_CACHE is not None else load_soul()
    if soul:
        return f"{soul}\n\n---\n\n{_QUICK_CHAT_OUTPUT_RULES}"
    return _QUICK_CHAT_OUTPUT_RULES


# Backwards-compat: a few comments + tests reference the old name. Keep
# the symbol exported but pointed at the new composer so anything that
# imports it still works.
QUICK_CHAT_SYSTEM_PROMPT_BASE = get_quick_chat_system_prompt()


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


# Denial-counter persistence. Each entry: {"ts": iso, "msg": short,
# "model": str, "kind": "denial"|"thinking_leak"}.
_DENIAL_LOG = Path.home() / "AI_Agent" / "memory" / "quick_chat_denials.jsonl"
_DENIAL_24H_THRESHOLD = 5  # alert via Telegram when >= this many in 24h
_DENIAL_ALERT_COOLDOWN_S = 6 * 3600  # don't re-alert more than every 6h
_DENIAL_LAST_ALERT = Path.home() / "AI_Agent" / "memory" / "quick_chat_denial_last_alert"

# Cleanliness telemetry: every quick_chat call writes one line.
# {"ts": iso, "model": str, "elapsed_s": float, "clean": bool,
#  "fallback_used": bool, "leak_kind": "denial"|"thinking"|null}
_CLEANLINESS_LOG = Path.home() / "AI_Agent" / "memory" / "quick_chat_cleanliness.jsonl"


def _record_denial(message: str, model: str, *, kind: str = "denial") -> None:
    """Append one line to the denial log. Best-effort, never raises.

    `kind` distinguishes denial-style refusals ("I can't browse the web")
    from thinking-style leaks ("Okay, the user asked..."). Both push the
    same 24h alert threshold so a regression in either is visible."""
    try:
        _DENIAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "msg": (message or "")[:140],
            "model": model,
            "kind": kind,
        }
        with _DENIAL_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("denial log append failed: %s", exc)


def _record_cleanliness(model: str, elapsed_s: float, *, clean: bool,
                        fallback_used: bool, leak_kind: str | None = None) -> None:
    """Append one cleanliness observation. Read by
    /metrics/quick_chat_cleanliness for the dashboard widget.
    Best-effort — never raises."""
    try:
        _CLEANLINESS_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": model,
            "elapsed_s": round(elapsed_s, 3),
            "clean": clean,
            "fallback_used": fallback_used,
        }
        if leak_kind:
            entry["leak_kind"] = leak_kind
        with _CLEANLINESS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("cleanliness log append failed: %s", exc)


def _denials_in_last_24h() -> int:
    """Count entries in the last 24h. Cheap — file is bounded and we
    short-circuit at the threshold."""
    if not _DENIAL_LOG.exists():
        return 0
    cutoff = datetime.now().astimezone().timestamp() - 24 * 3600
    n = 0
    try:
        for raw in _DENIAL_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ts = entry.get("ts", "")
            if not isinstance(ts, str):
                continue
            try:
                t = datetime.fromisoformat(ts).timestamp()
            except ValueError:
                continue
            if t >= cutoff:
                n += 1
                if n >= _DENIAL_24H_THRESHOLD * 4:  # cap, don't scan whole file
                    return n
    except OSError:
        return 0
    return n


def _maybe_alert_telegram(count_24h: int) -> None:
    """Fire a Telegram alert if denials are spiking. Cooldown'd so we
    don't spam the user once a regression starts."""
    if count_24h < _DENIAL_24H_THRESHOLD:
        return
    try:
        if _DENIAL_LAST_ALERT.exists():
            last = float(_DENIAL_LAST_ALERT.read_text().strip() or "0")
        else:
            last = 0.0
        now = datetime.now().astimezone().timestamp()
        if now - last < _DENIAL_ALERT_COOLDOWN_S:
            return
        from tools.telegram_tool import telegram_notify  # noqa: PLC0415
        msg = (
            f"⚠️ quick_chat denial spike: {count_24h} in last 24h. "
            f"qwen3:4b is denying capabilities Nexus actually has. "
            f"Review ~/AI_Agent/memory/quick_chat_denials.jsonl. "
            f"Consider reverting QUICK_CHAT_MODEL to qwen3.6 if it persists."
        )
        try:
            telegram_notify.invoke({"message": msg})
        except Exception as exc:
            log.warning("denial telegram alert failed: %s", exc)
        _DENIAL_LAST_ALERT.write_text(str(now))
    except OSError as exc:
        log.warning("denial alert bookkeeping failed: %s", exc)


_QUICK_CHAT_JSON_HINT = (
    '\n\nReturn ONLY this JSON object: {"reply": "<your answer>"}. '
    "No prose outside the JSON. No reasoning inside the reply field. "
    "No \"Okay,\" / \"Let me\" / \"The user\" preambles. Just the answer."
)

# qwen3:4b investigation (see /tmp/qwen3_4b_outputs.log) found that:
#  - `think=False` does NOT actually disable chain-of-thought; it just
#    hides the opening <think> tag. The model still emits raw reasoning
#    prose followed (sometimes) by a closing </think> + the real answer.
#  - With num_predict=200, half the responses got cut off mid-reasoning
#    before reaching any answer at all.
#  - With strict-prefix system prompt + format=json + num_predict=120 +
#    temperature=0.3, every test case returned a clean direct answer
#    in 0.3-0.5 s. That's the sweet spot.
QUICK_CHAT_NUM_PREDICT = 120
QUICK_CHAT_TEMPERATURE = 0.3


def _ollama_quick_chat(model: str, message: str, system_prompt: str) -> str:
    """One-shot quick_chat call against any model. Forces JSON-mode
    output ({"reply": "..."}) and tight num_predict to suppress
    qwen3:4b's CoT. Falls back to plain-text mode if the JSON parse
    fails so any model that doesn't honour the format flag still gets
    a chance to answer.
    """
    try:
        resp = ollama.Client(host=nexus.OLLAMA_URL).chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt + _QUICK_CHAT_JSON_HINT},
                {"role": "user", "content": message},
            ],
            options={
                "temperature": QUICK_CHAT_TEMPERATURE,
                "num_ctx": 4096,
                "num_predict": QUICK_CHAT_NUM_PREDICT,
            },
            keep_alive=-1,
            think=False,
            format="json",
        )
        body = (resp.get("message", {}) or {}).get("content", "").strip()
        try:
            obj = json.loads(body)
            reply = (obj or {}).get("reply", "") if isinstance(obj, dict) else ""
            if reply:
                return _clean_quick_chat(reply)
        except json.JSONDecodeError:
            pass  # fall through to plain-text retry
    except Exception as exc:
        log.warning("quick_chat json mode failed: %s — retrying plain", exc)

    # Plain-text fallback if the JSON path returned nothing useful.
    # Same tight num_predict so a stuck model still can't ramble forever.
    resp = ollama.Client(host=nexus.OLLAMA_URL).chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ],
        options={
            "temperature": QUICK_CHAT_TEMPERATURE,
            "num_ctx": 4096,
            "num_predict": QUICK_CHAT_NUM_PREDICT * 2,  # bigger budget for plain mode
        },
        keep_alive=-1,
        think=False,
    )
    body = (resp.get("message", {}) or {}).get("content", "").strip()
    return _clean_quick_chat(body)


# qwen3:4b reliably leaks reasoning prose like:
#   "Okay, the user asked X. That's a simple Y question. Let me think...
#    7 plus 8 equals 15. I should reply directly with the answer."
# JSON mode contains the OUTPUT shape but the CoT still ends up inside
# the `reply` field. This regex matches sentence-by-sentence preambles
# we can safely drop. Anything matching means "this sentence is the
# model talking to itself, not to the user".
_QUICK_CHAT_PREAMBLE_RE = re.compile(
    r"^\s*("
    r"okay[,.]?\s+(?:the\s+user|let|so|i|this|that|since|first)\b.*?(?:\.|\?|!)\s*"
    r"|hmm[,.]?\s+.*?(?:\.|\?|!)\s*"
    r"|wait[,.]?\s+.*?(?:\.|\?|!)\s*"
    r"|alright[,.]?\s+.*?(?:\.|\?|!)\s*"
    r"|let'?s?\s+(?:see|think|check|verify|calculate|figure)\b.*?(?:\.|\?|!)\s*"
    r"|let\s+me\s+(?:see|think|check|verify|calculate|figure|recall).*?(?:\.|\?|!)\s*"
    r"|the\s+user\s+(?:is\s+)?(?:asking|asked|wants|needs).*?(?:\.|\?|!)\s*"
    r"|since\s+(?:this|the\s+user|i\b).*?(?:\.|\?|!)\s*"
    r"|first[,.]?\s+let\s+me.*?(?:\.|\?|!)\s*"
    r"|i\s+(?:should|need\s+to|will|can)\s+(?:reply|answer|respond|provide).*?(?:\.|\?|!)\s*"
    r"|that'?s?\s+(?:a\s+)?(?:simple|quick|easy|straightforward).*?(?:\.|\?|!)\s*"
    r"|so\s+(?:the|i|let)\b.*?(?:\.|\?|!)\s*"
    r"|we\s+are\s+given.*?(?:\.|\?|!)\s*"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def _strip_reasoning_preamble(text: str) -> str:
    """Drop reasoning-prose sentences that lead the response. Loops
    because qwen3:4b stacks 2-3 of them in a row before getting to the
    actual answer. Stops as soon as no preamble matches the head."""
    if not text:
        return text
    for _ in range(6):  # cap loop — never strip more than 6 sentences
        m = _QUICK_CHAT_PREAMBLE_RE.match(text)
        if not m:
            break
        text = text[m.end():]
        # qwen3:4b sometimes uses ellipses ("Let me think... 7+8 = 15"),
        # so the previous sentence's trailing dots can lead the next
        # iteration. Strip them so subsequent regex matches stay anchored.
        text = text.lstrip(" .,;:")
    return text.strip()


def _split_unbalanced_close_think(text: str) -> str:
    """qwen3:4b often emits its CoT prose then a bare `</think>` close
    tag (no opening), then the real answer. Strip everything before
    the first `</think>` if one is present.

    This is separate from `nexus.strip_thinking` (which expects matched
    open+close tags) because the open is always missing here."""
    if not text or "</think>" not in text:
        return text
    return text.split("</think>", 1)[1].strip()


# Sentinel substrings that indicate raw internal reasoning leaked into
# the user-facing reply. If ANY of these survive both `</think>` split
# and the preamble stripper, we flag the response as "leaked" so the
# caller can retry on qwen3.6.
_LEAK_SENTINELS = (
    "let me count",
    "let me recall",
    "let me check",
    "let me think",
    "let me craft",
    "let me pick",
    "double-checking",
    "double check",
    "make sure to",
    "make sure it's",
    "no meta-commentary",
    "no preamble",
    "no <think>",
    "no  tags",
    "okay, the user",
    "okay, user just",
    "okay, user asked",
    "we are in the middle of",
    "we are in 2026",
    "i need to respond",
    "i should reply",
    "i'll respond with",
    "*checks notes*",
    "*checks time*",
    "*checks rules*",
    "as nexus,",
    "the user is asking",
    "the user just said",
    "the user asks:",
    "maybe something like",
    "first, i'll",
    "as per the rules",
    "since they're",
    "since the user",
    "perfect. no",
    "got that?\n",
    "let me craft",
    # BUG 9 — observed in May 1 production:
    "wait, the instructions",
    "wait, the system",
    "wait, the rules",
    "wait, the prompt",
    "the instructions say",
    "the system prompt says",
    "according to my instructions",
    "based on the system prompt",
    "remember, the rules",
    "looking at the rules",
    "i'm supposed to",
    "i'm being told to",
    "the persona says",
    # Phase 30 — qwen3:4b started leaking new untagged-reasoning shapes
    # after the SOUL.md tone fix. These sentinels catch the openers we've
    # observed in production:
    "user says ",
    "user says \"",
    "user says '",
    "best reply:",
    "final reply:",
    "first, gotta",
    "first, check",
    "first, match",
    "first, i need",
    "first, i should",
    "i must respond",
    "i should respond",
    "key points from",
    "key points:",
    "possible replies",
    "possible replies:",
    "following the rules",
    "we are in a situation",
    "we are given the message",
    "the user wants",
    "the user is using",
    "classic colton",
    "match his energy",
    "match colton's energy",
    "as nexus, i must",
    "as nexus, i should",
    "as nexus,",
    "okay, let's break",
    "let's break this down",
)


# BUG 9 — final defense: any reply that exits route_message goes through
# this stripper. Catches `<think>...</think>` blocks AND open `<think>`
# tags with no closer (qwen3:4b sometimes truncates mid-thought). The
# regex from nexus.py is the canonical source — re-exported here so
# every reply path picks it up without needing to import nexus.
import re as _re  # noqa: E402
_THINK_BLOCK_RE = _re.compile(r"<think>.*?</think>", _re.DOTALL | _re.IGNORECASE)
_OPEN_THINK_RE = _re.compile(r"<think>.*\Z", _re.DOTALL | _re.IGNORECASE)


# BUG 4 — words that signal "the user wants a synthesized answer, not
# raw search snippets". When any of these appear in the user message,
# lite_agent must skip the fast-format shortcuts and force the LLM
# formatter step that actually condenses across all hits.
_SYNTHESIS_KEYWORDS = (
    "summarize", "summary", "summarise",
    "in your own words",
    "tldr", "tl;dr",
    "bullet", "bullets",
    "condense", "condensed",
    "key points", "main points", "main takeaway", "takeaways",
    "explain in", "in 3 ", "in three ", "in 5 ", "in five ",
    "rewrite", "paraphrase",
)


def _wants_synthesis(message: str) -> bool:
    """True if the user explicitly asked for a synthesized answer.
    Case-insensitive substring match against `_SYNTHESIS_KEYWORDS`."""
    if not message:
        return False
    low = message.lower()
    return any(kw in low for kw in _SYNTHESIS_KEYWORDS)


# Phase 30 — qwen3:4b often leaks a CoT chain that *ends* with the
# intended answer wrapped in quotes after a "Best reply:" / "Final
# reply:" / "My reply:" marker. When that pattern is present we
# extract just the quoted answer and skip the prefix scrubber, since
# everything before the marker is reasoning by definition.
_BEST_REPLY_EXTRACT_RE = _re.compile(
    r"(?:best|final|my)\s+reply\s*:?\s*[\"\u201c\u2018']([^\"\u201d\u2019'\n]{2,400})[\"\u201d\u2019']",
    _re.IGNORECASE,
)


def _extract_best_reply(text: str) -> str | None:
    """Return the quoted answer following a 'Best reply:' marker, or
    None when no such marker is present. Used as a short-circuit by
    `_strip_think_final` to recover the intended reply when the
    surrounding CoT is too dense for sentinel-based stripping."""
    if not text:
        return None
    low = text.lower()
    if "reply:" not in low and "reply :" not in low:
        return None
    m = _BEST_REPLY_EXTRACT_RE.search(text)
    if not m:
        return None
    answer = m.group(1).strip()
    # Sanity guard — refuse extracted answers that themselves look
    # like leaked reasoning so we don't replace one leak with another.
    if any(s in answer.lower() for s in _LEAK_SENTINELS):
        return None
    return answer


def _strip_think_final(text: str) -> str:
    """Final reply scrubber. Removes <think>...</think>, dangling
    <think>..., and a known set of leaked-reasoning prefix lines.
    Idempotent — safe to apply on already-clean text.

    Phase 30 short-circuit: if the input is dense CoT that contains
    `Best reply: '...'`, pluck the quoted answer and return only that."""
    if not text:
        return text
    extracted = _extract_best_reply(text)
    if extracted:
        return extracted
    out = _THINK_BLOCK_RE.sub("", text)
    out = _OPEN_THINK_RE.sub("", out)
    # Drop any leading line whose lowercased prefix matches a sentinel.
    lines = out.splitlines()
    while lines:
        head = lines[0].strip().lower()
        if not head:
            lines.pop(0)
            continue
        if any(head.startswith(s) for s in _LEAK_SENTINELS):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()


def looks_like_thinking_leak(text: str) -> bool:
    """True if the cleaned reply still contains internal-reasoning
    sentinels we *know* are CoT artifacts. Used as a fallback trigger
    after the cleaner has already done its best."""
    if not text:
        return False
    lower = text.lower()
    return any(s in lower for s in _LEAK_SENTINELS)


def _clean_quick_chat(text: str) -> str:
    """Strip <think> tags, the bare `</think>` separator qwen3:4b
    emits without an opener, common reasoning-prose preambles, and
    trailing meta-commentary. Belt-and-suspenders on top of the
    JSON-mode + tight num_predict constraint.

    Order matters:
      1. `</think>` split — if the model self-marked the answer boundary,
         everything before it is reasoning to drop.
      2. nexus.strip_thinking — drop matched <think>...</think> blocks.
      3. preamble stripper — sentence-level "Okay, the user..." drops.
    """
    if not text:
        return text
    text = _split_unbalanced_close_think(text)
    if hasattr(nexus, "strip_thinking"):
        text = nexus.strip_thinking(text)
    text = _strip_reasoning_preamble(text)
    return text.strip()


def quick_chat(message: str) -> str:
    """Inline conversational reply for CHAT and QUERY_INLINE intents.

    Two-tier model strategy (Fix #4 v2 — qwen3:4b CoT-leak repair):
      1. qwen3:4b primary in JSON-mode + tight num_predict + strict
         system prompt. ~0.4-1.5s warm, 5-15x faster than qwen3.6.
      2. If the reply trips `_looks_like_denial` (model refused) OR
         `looks_like_thinking_leak` (model leaked CoT prose despite
         the JSON+strict-prompt+post-processing pipeline), log it and
         retry ONCE on qwen3.6. Five total leak-or-denial events in
         24h fires a Telegram alert so we can revert if qwen3:4b
         starts regressing.

    Real datetime is injected into the system prompt every call so the
    model can answer "what time is it" correctly instead of
    hallucinating from training data.
    """
    import time as _time  # noqa: PLC0415

    # SOUL.md is the single source of truth — `get_quick_chat_system_prompt`
    # composes full SOUL + quick_chat-specific output / capability rules.
    # _datetime_context appends real wall-clock so "what time is it" works.
    system_prompt = f"{get_quick_chat_system_prompt()}\n\n{_datetime_context()}"
    t0 = _time.monotonic()
    try:
        primary = _ollama_quick_chat(QUICK_CHAT_MODEL, message, system_prompt)
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        _record_cleanliness(QUICK_CHAT_MODEL, elapsed,
                            clean=False, fallback_used=False, leak_kind="error")
        return f"(quick_chat error: {type(exc).__name__}: {exc})"

    leak_kind: str | None = None
    if _looks_like_denial(primary):
        leak_kind = "denial"
    elif looks_like_thinking_leak(primary):
        leak_kind = "thinking"

    if leak_kind is None:
        elapsed = _time.monotonic() - t0
        _record_cleanliness(QUICK_CHAT_MODEL, elapsed,
                            clean=True, fallback_used=False)
        return primary

    log.info("quick_chat %s leak (%s) — retrying on %s. preview=%r msg=%r",
             QUICK_CHAT_MODEL, leak_kind, QUICK_CHAT_DENIAL_FALLBACK_MODEL,
             primary[:120], message[:120])
    _record_denial(message, QUICK_CHAT_MODEL, kind=leak_kind)
    _maybe_alert_telegram(_denials_in_last_24h())

    try:
        fallback = _ollama_quick_chat(
            QUICK_CHAT_DENIAL_FALLBACK_MODEL, message, system_prompt,
        )
    except Exception as exc:
        elapsed = _time.monotonic() - t0
        log.warning("quick_chat fallback failed: %s — returning primary reply", exc)
        _record_cleanliness(QUICK_CHAT_MODEL, elapsed,
                            clean=False, fallback_used=True, leak_kind=leak_kind)
        return primary

    # If the fallback also denied or leaked, return whichever is shorter
    # (the primary is usually less verbose) so the user gets *something*.
    fallback_bad = _looks_like_denial(fallback) or looks_like_thinking_leak(fallback)
    elapsed = _time.monotonic() - t0
    final = primary if fallback_bad else fallback
    _record_cleanliness(
        QUICK_CHAT_DENIAL_FALLBACK_MODEL, elapsed,
        clean=not fallback_bad, fallback_used=True, leak_kind=leak_kind,
    )
    return final


LITE_AGENT_TIMEOUT_S = 15.0
# Bumped from 5s → 8s after smoke runs showed qwen3.6 picker timing out
# under contention (model serving was busy with the EOD summary job).
# 8s + 4s + small tool window keeps total under the 15s ceiling.
LITE_AGENT_PICKER_BUDGET = 8.0
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


_FORMATTER_OUTPUT_RULES = (
    "Write a 2-3 sentence answer to the user's question using the tool "
    "result below. Plain prose. No preamble. No reasoning. No <think> tags. "
    "If the tool returned an error, say so plainly and suggest an alternative "
    "in 1 sentence. Never echo the raw tool output verbatim."
)


def get_formatter_system_prompt() -> str:
    """Same SOUL-as-base pattern quick_chat uses. The lite_agent formatter
    writes user-facing replies after a tool call, so tone consistency
    with quick_chat / heavy agent matters. SOUL.md provides the persona,
    tone, length rules, and slang glossary; the rules below add the
    formatter-specific output constraints."""
    soul = _SOUL_CACHE if _SOUL_CACHE is not None else load_soul()
    if soul:
        return f"{soul}\n\n---\n\n{_FORMATTER_OUTPUT_RULES}"
    return _FORMATTER_OUTPUT_RULES


# Backwards-compat alias kept for any imports that still reach for the
# old name. Resolves to the composed prompt at module import.
_FORMATTER_SYSTEM = get_formatter_system_prompt()


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
        # 8192 num_ctx leaves headroom for SOUL.md (~2700 tokens) plus
        # tool result payloads (up to ~1500 tokens after truncation) plus
        # the formatter output budget. The previous 4096 limit fit the
        # old hardcoded constants but is too tight once SOUL is the base.
        "options": {"temperature": 0.1, "num_predict": num_predict, "num_ctx": 8192},
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


# --- Part B: skip the LLM formatter when the tool output is already clean.
# Saves a 3-4s qwen3.6 round-trip on the common case where the tool
# already produced something the user can read directly.

# Tool names whose output is structured the same way every time and can
# be parsed deterministically into a 1-line answer.
_FORMATTABLE_SEARCH_TOOLS = frozenset({
    "searxng_search", "searxng_search_news", "web_search",
    "brave_search", "brave_search_news",
})


def _searxng_top_hit(text: str) -> str | None:
    """If `text` is a SearXNG / web_search formatted result list, pull
    the top hit's title and snippet into a single sentence. Returns
    None for any other shape so the caller falls back to the LLM
    formatter (or the generic clean-output check below).

    Expected input shape (from tools/searxng_tool._format_results +
    optional `[search:<backend>]` header from search_router.web_search):

        [search:searxng]
        - [google] Some Title
          https://example.com/path
          A short snippet here.
        - [bing] ...
    """
    if not text:
        return None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    if lines[0].startswith("[search:"):
        lines = lines[1:]
    if not lines or not lines[0].lstrip().startswith("- "):
        return None
    title_line = lines[0].lstrip("- ").strip()
    if title_line.startswith("["):
        idx = title_line.find("] ")
        if idx > 0:
            title_line = title_line[idx + 2:].strip()
    snippet = ""
    # The bullet's third line under it is typically the snippet
    # (line 0=title, 1=url, 2=snippet).
    if len(lines) >= 3 and not lines[2].lstrip().startswith("- "):
        snippet = lines[2].strip()
    if not title_line:
        return None
    if not snippet:
        return title_line
    # Trim for chat — Telegram users want to skim, not read.
    if len(snippet) > 240:
        snippet = snippet[:240].rstrip() + "…"
    return f"{title_line}: {snippet}"


def _looks_like_clean_output(text: str) -> bool:
    """Generic heuristic: True when tool output already reads like a
    user-facing answer and doesn't need an LLM formatter pass.

    Rules (all must hold):
    - non-empty
    - <= 600 chars (longer outputs are usually result lists / dumps)
    - doesn't start with JSON or list markers
    - doesn't start with ERROR (let the formatter explain those)
    - has at least one space (single-token outputs are usually IDs/codes)

    Earlier version required a terminating punctuation char, but real
    tool outputs commonly end on UTC dates, repo names, etc. — that
    bounced github_auth_status into the formatter for no reason. Shape
    checks (length + JSON/ERROR exclusion + word count) are enough.
    """
    if not text:
        return False
    text = text.strip()
    n = len(text)
    if n == 0 or n > 600:
        return False
    if text[0] in "{[":
        return False
    head = text[:8].upper()
    if head.startswith("ERROR") or head.startswith("(NO ") or head.startswith("ADD "):
        return False
    # Reject single-token outputs (ids, codes, single numbers). Multi-word
    # output that fits the size budget is almost always readable.
    return " " in text


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

    # 3a. Cheap shortcuts that skip the LLM formatter (Fix #4 part B).
    # Most QUERY_TOOL turns hit one of these and save ~3-4s.
    result_str = str(tool_result) if tool_result is not None else ""

    # BUG 4 — synthesis bypass. When the user asked for a summary /
    # bullets / "in your own words", DO NOT take the search-top-hit or
    # clean-output shortcut — those paste the raw snippet. Force the
    # LLM formatter so the model condenses across all results in the
    # shape the user requested.
    if tool_name in _FORMATTABLE_SEARCH_TOOLS and _wants_synthesis(message):
        log.info("lite_agent: synthesis requested — skipping fast-format paths")
    else:
        if tool_name in _FORMATTABLE_SEARCH_TOOLS:
            sx = _searxng_top_hit(result_str)
            if sx:
                return {
                    "ok": True,
                    "tool": tool_name,
                    "reply": sx,
                    "fast_format": "search_top_hit",
                }

        if _looks_like_clean_output(result_str):
            return {
                "ok": True,
                "tool": tool_name,
                "reply": result_str.strip(),
                "fast_format": "clean_output",
            }

    # 3b. Out of budget? Return raw — at least the user gets *something*.
    if _budget_left() < 0.5:
        log.info("lite_agent skipping formatter — out of budget")
        return {
            "ok": True,
            "tool": tool_name,
            "reply": result_str[:600],
            "fast_format": "out_of_budget",
        }

    # 3c. Last resort: pay the LLM formatter to rewrite into prose.
    reply = _format_answer(message, tool_name, result_str)
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


# Patterns that should ALWAYS route to QUERY_TOOL regardless of what the
# LLM classifier says. Live-smoke turned up cases where qwen3.6 saw
# "what's my github auth status" and picked TASK — even though the
# prompt lists it as a QUERY_TOOL example. Belt-and-suspenders: an
# obvious-shape question for a single fast tool gets a deterministic
# fast path here.
_FAST_TOOL_HARD_OVERRIDE_RE = re.compile(
    r"\b("
    # GitHub auth / status / single-call lookups
    r"github\s+auth(?:entication)?\s+status"
    r"|gh\s+auth(?:entication)?\s+status"
    r"|github\s+status"
    r"|am\s+i\s+(?:logged|signed)\s+in\s+to\s+github"
    # "the weather (in <location>)"
    r"|(?:what'?s\s+|how'?s\s+)?the?\s*weather\b"
    r"|weather\s+(?:in|for|at)\s+\w"
    # "search the web for X" / "search for X" / "look up X" — single
    # web_search call satisfies these.
    r"|search\s+(?:the\s+web\s+)?for\b"
    r"|look\s+(?:something\s+)?up\b"
    r"|google\s+\w"
    # "search my notes for X" / "search my memory for X"
    r"|search\s+(?:my\s+)?(?:notes?|memory|rag)\s+for\b"
    # GitHub list-my-repos
    r"|list\s+my\s+(?:github\s+)?repos?"
    r"|what\s+repos?\s+do\s+i\s+have"
    r")",  # no trailing \b — some patterns end on \w mid-word (e.g. "weather in seattle")
    re.IGNORECASE,
)


def _looks_like_single_fast_tool(msg: str) -> bool:
    """Deterministic gate: True for obvious one-tool-call shapes the LLM
    classifier sometimes misses. Used to short-circuit straight to
    QUERY_TOOL without paying the classifier round-trip."""
    return bool(_FAST_TOOL_HARD_OVERRIDE_RE.search(msg or ""))


# --- Phase 23.1 scaffolding intent detection -----------------------------
# Trigger phrases for "create/scaffold/spin up a <type>" requests. The
# matched recipe slug + project name get baked into a structured TASK
# that the agent picks up and routes to scaffold_project.

_SCAFFOLD_TRIGGER_RE = re.compile(
    r"\b(?:scaffold|spin\s+up|create|build|start|set\s+up|generate)"
    r"\s+(?:a\s+|an\s+|me\s+a\s+|me\s+an\s+|a\s+new\s+)?"
    r"(?:new\s+)?(?:project\s+)?",
    re.IGNORECASE,
)

# Map from natural-language hints to recipe slugs. Order matters —
# more specific phrases first so "marketplace" wins over plain "next.js".
_RECIPE_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:marketplace|multi[-\s]?sided|stripe[-\s]?connect)\b", re.I), "nextjs-marketplace"),
    (re.compile(r"\b(?:saas|sass|subscription)\b", re.I), "nextjs-saas"),
    (re.compile(r"\b(?:dashboard|admin\s+panel|analytics\s+app)\b", re.I), "nextjs-dashboard"),
    (re.compile(r"\b(?:landing\s+page|landing|coming\s+soon|waitlist)\b", re.I), "nextjs-landing"),
    (re.compile(r"\b(?:fastapi|python\s+api|rest\s+api)\b", re.I), "python-fastapi"),
    (re.compile(r"\b(?:python\s+cli|click\s+cli|cli\s+tool)\b", re.I), "python-cli"),
    # Generic Next.js fallback last — only if a specific subtype didn't match.
    (re.compile(r"\b(?:next\.?js|nextjs|next\s+app)\b", re.I), "nextjs-landing"),
]

# Project-name extraction: "called X" / "named X" / "for X" / quoted X.
_NAME_PATTERNS = [
    re.compile(r"""(?:called|named)\s+["']?([a-z0-9][a-z0-9-]{1,40}[a-z0-9])["']?""", re.I),
    re.compile(r"""["']([a-z0-9][a-z0-9-]{1,40}[a-z0-9])["']""", re.I),
]


def _detect_scaffold_intent(msg: str) -> dict | None:
    """Return {recipe, name, missing} when the message looks like a
    scaffolding request. `missing` is a list of fields the user didn't
    specify — caller can ask a clarifying question or pick defaults.
    Returns None for non-scaffolding messages."""
    if not msg or not _SCAFFOLD_TRIGGER_RE.search(msg):
        return None
    recipe = None
    for pat, slug in _RECIPE_HINTS:
        if pat.search(msg):
            recipe = slug
            break
    if recipe is None:
        return None  # trigger present but no recipe hint — not actually a scaffold
    name = None
    for np in _NAME_PATTERNS:
        m = np.search(msg)
        if m:
            name = m.group(1).lower()
            break
    missing = [f for f, v in (("name", name),) if not v]
    return {"recipe": recipe, "name": name, "missing": missing}


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


# BUG 6 — entity-question patterns that MUST hit the wiki before any
# reply generation. "What is BidWatt?" was hallucinating an answer
# about real-time electricity pricing because the classifier picked
# CHAT. These patterns intercept first.
_ENTITY_QUESTION_RE = re.compile(
    r"^\s*(?:what(?:'?s| is| are)|who(?:'?s| is| are)|tell me about|"
    r"explain|describe|define|wikiq?(?::|\s))\s+(.{2,})$",
    re.IGNORECASE | re.DOTALL,
)


def _entity_lookup(message: str) -> dict | None:
    """If the message looks like 'what is X / who is X / tell me about X',
    query the wiki BEFORE any LLM call. Returns a route dict on hit, or
    a route dict with a clearly-marked uncertainty prefix on miss, or
    None when the pattern doesn't apply at all so the caller can keep
    routing normally."""
    m = _ENTITY_QUESTION_RE.match(message.strip())
    if not m:
        return None
    try:
        from tools import wiki_tool  # noqa: PLC0415
        hits = wiki_tool.wiki_query.invoke({"question": message, "k": 3})
    except Exception as exc:
        log.warning("wiki_query failed: %s", exc)
        return None
    if not hits or hits.startswith("(no wiki hits"):
        # No wiki entry — fall through to quick_chat but tag the result
        # so the model (and the user) knows the answer isn't grounded.
        return {
            "kind": "query_inline",
            "reply": _entity_no_wiki_reply(message),
            "meta": {"wiki_hit": False},
        }
    # Got a hit — synthesize a grounded reply via quick_chat using the
    # wiki content as system context. Keep it tight.
    grounded_prompt = (
        "Answer the user's question using ONLY the wiki excerpt below. "
        "Be concise (2-4 sentences). If the excerpt doesn't fully answer, "
        "say so explicitly. Do not invent details.\n\n"
        f"WIKI EXCERPT:\n{hits[:4000]}\n\n"
        f"USER QUESTION: {message}"
    )
    try:
        reply = quick_chat(grounded_prompt)
    except Exception as exc:
        log.warning("grounded reply failed: %s", exc)
        reply = hits[:1500] + "\n\n(raw wiki excerpt — synthesis failed)"
    return {
        "kind": "query_inline",
        "reply": reply,
        "meta": {"wiki_hit": True},
    }


def _entity_no_wiki_reply(message: str) -> str:
    """Compose a reply for an entity question with no wiki match. Calls
    quick_chat with a tight uncertainty-required system overlay so the
    model can't invent a confident answer."""
    overlay = (
        "(no wiki entry — answering from training data) "
        "The user is asking about an entity Nexus has no curated wiki "
        "page for. Reply briefly. If you genuinely don't know, say "
        "\"I don't know\" or \"I'm guessing here\"; never invent "
        "confident facts. Begin your reply with the literal prefix "
        "\"(no wiki entry — answering from training data) \"."
    )
    try:
        reply = quick_chat(f"{overlay}\n\nUser question: {message}")
    except Exception:
        reply = "(no wiki entry — answering from training data) I don't know."
    if not reply.startswith("(no wiki entry"):
        reply = f"(no wiki entry — answering from training data) {reply}"
    return reply


_QUEUE_PREFIX_RE = re.compile(r"^\s*queue\s*[:>]\s*(.+)$", re.IGNORECASE | re.DOTALL)
_DISPATCH_PREFIX_RE = re.compile(
    r"^\s*(force\s+)?dispatch\s*[:>]\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)

# ─── Phase 28 + 29 — slash-command lookup table + parser ─────────────────
# Slash commands are the manual override fast lane: they trump intent
# classification, build-intent regex, dispatch: prefix, and the entity-
# question wiki short-circuit. Defined in one dict so /help can list them
# and the parser can return a structured route decision in one shot.
#
# Phase 29 ladder (from cheapest → most expensive marginal cost):
#   /max   — Claude Code via Max plan (no env file, $0 marginal)
#   /local — qwen3-coder:30b via Ollama (offline, $0)
#   /quick — qwen3:4b chat (no thinking, $0)
#   /code  — DeepSeek V4-Flash (~$0.005, saves Max quota for small builds)
#   /pro   — DeepSeek V4-Pro (~$0.05)
#   /api   — Anthropic Sonnet 4.6 via API key (~$0.10–1.00 fallback)
#   /real  — DEPRECATED alias for /api kept so muscle memory still works
SLASH_COMMANDS: dict[str, dict] = {
    "/max":   {"tool": "claude_code", "tier": "max",
               "blurb": "Claude Sonnet 4.6 via Max plan ($0 marginal)"},
    "/code":  {"tool": "claude_code", "tier": "flash",
               "blurb": "DeepSeek V4-Flash build (~$0.005)"},
    "/pro":   {"tool": "claude_code", "tier": "pro",
               "blurb": "DeepSeek V4-Pro build (~$0.05)"},
    "/api":   {"tool": "claude_code", "tier": "api",
               "blurb": "Anthropic Sonnet 4.6 via API key (fallback, paid)"},
    # Phase 29 — /real keeps working but logs a deprecation warning.
    "/real":  {"tool": "claude_code", "tier": "api",
               "blurb": "[DEPRECATED] alias for /api",
               "deprecated_alias_for": "/api"},
    "/local": {"tool": "local_builder", "model": "qwen3-coder:30b",
               "blurb": "qwen3-coder:30b local build (free)"},
    "/quick": {"tool": "quick_chat", "model": "qwen3:4b",
               "blurb": "qwen3:4b quick answer (no thinking, no tools)"},
}


_DEPRECATION_LOG = Path.home() / "AI_Agent" / "cc_logs" / "_deprecation.log"


def _log_deprecation(line: str) -> None:
    """Append a one-liner to cc_logs/_deprecation.log. Best-effort —
    silent on any I/O failure."""
    try:
        _DEPRECATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().astimezone().isoformat(timespec="seconds")
        with _DEPRECATION_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{ts} {line}\n")
    except OSError:
        pass


def parse_slash_command(text: str) -> dict | None:
    """Return {command, prompt, **route_meta} when `text` starts with a
    Phase 28/29 slash command, else None. Whitespace-tolerant; the
    prompt is everything after the first space (empty string if none).

    Phase 29 — /real triggers a one-line deprecation log to
    cc_logs/_deprecation.log so the move to /api is auditable, while
    the alias still routes correctly so muscle memory keeps working."""
    if not text or not text.startswith("/"):
        return None
    parts = text.split(" ", 1)
    cmd = parts[0].lower()
    prompt = parts[1].strip() if len(parts) > 1 else ""
    if cmd not in SLASH_COMMANDS:
        return None
    spec = SLASH_COMMANDS[cmd]
    if spec.get("deprecated_alias_for"):
        target = spec["deprecated_alias_for"]
        _log_deprecation(f"[DEPRECATED] {cmd} is now {target} — please update muscle memory")
        log.warning("slash %s is deprecated — alias for %s", cmd, target)
    return {"command": cmd, "prompt": prompt, **spec}


# Phase 28 — smart-routing regexes for non-slash messages. SIMPLE wins
# when the user explicitly asks for a quick/simple build; otherwise the
# existing _BUILD_INTENT_RE captures the broader "build me X" shape and
# upgrades it to a cloud dispatch so the heavier multi-file builds get
# the smarter (cheap-cloud) model instead of qwen3.6 alone.
SIMPLE_BUILD_RE = re.compile(
    r"^\s*(make|create|write)\s+(a\s+)?(simple|quick|basic|tiny|small)\b",
    re.IGNORECASE,
)
COMPLEX_BUILD_RE = re.compile(
    r"\b(build|create|make|fix|debug|refactor)\b.*(app|component|page|api|database|auth)",
    re.IGNORECASE | re.DOTALL,
)


def _enqueue_tiered_dispatch(prompt: str, tier: str, *,
                             label: str | None = None) -> dict:
    """Drop a Phase 28 cloud-tier prompt into the cc_dispatcher inbox.
    Returns a route dict {kind, reply, meta} so callers can hand it
    straight back to the user.

    Prepends a "write to ~/AI_Agent/games/<slug>.html" hint so the
    dispatcher's after-run check can find and auto-attach the artifact
    to Telegram (fixes the Phase 27 auto-attach bug for slash builds)."""
    from core import cc_dispatch as _ccd  # noqa: PLC0415
    if not prompt.strip():
        return {"kind": "dispatch", "reply": f"slash {tier}: needs a prompt.", "meta": {}}
    slug_words = re.findall(r"[a-zA-Z0-9]+", prompt.lower())[:5]
    slug = "-".join(slug_words) or "build"
    target_path = f"~/AI_Agent/games/{slug}.html"
    augmented = (
        f"Write the complete, self-contained output to {target_path} "
        f"(create the directory if needed). It should be a single file "
        f"that runs by opening in a browser — no build step, no CDN.\n\n"
        f"Build request:\n{prompt}"
    )
    risky = _ccd.is_risky(prompt)
    # Phase 29 — budget tiers: short for cheap/free models, longer for
    # the smarter (and slower) max + api Sonnet runs.
    if tier in ("flash", "pro", "local"):
        budget = 10
    elif tier == "max":
        budget = 30
    else:  # api / fallback
        budget = 30
    meta = _ccd.DispatchMeta.new(
        label=(label or prompt.splitlines()[0])[:60],
        time_budget_minutes=budget,
        risky_match=risky,
        tier=tier,
    )
    _ccd.write_prompt(meta, augmented, pending=bool(risky))
    log.info("slash dispatch tier=%s id=%s risky=%s",
             tier, meta.dispatch_id, bool(risky))
    if risky:
        reply = (
            f"🚨 Risky prompt held (matched: {risky}). "
            f"Reply 'go {meta.dispatch_id}' to dispatch."
        )
    else:
        reply = (
            f"🚀 On it — routing to Claude Code (tier {tier}). "
            f"id={meta.dispatch_id}. I'll ping when it's done."
        )
    return {
        "kind": "dispatch",
        "reply": reply,
        "meta": {"dispatch_id": meta.dispatch_id, "tier": tier, "risky": bool(risky)},
    }


def _slash_local_build(prompt: str) -> dict:
    """Phase 28 /local — qwen3-coder:30b local build via local_builder."""
    if not prompt.strip():
        return {"kind": "build", "reply": "/local: needs a description.", "meta": {}}
    from tools import local_builder  # noqa: PLC0415
    description = prompt
    slug_words = re.findall(r"[a-zA-Z0-9]+", description.lower())[:5]
    slug = "-".join(slug_words) or "build"
    target_path = f"~/AI_Agent/games/{slug}.html"
    tech_m = _BUILD_TECH_RE.search(description)
    tech = tech_m.group(1).lower() if tech_m else "html"
    if tech == "md":
        tech = "markdown"
    if tech == "bash":
        tech = "shell"
    try:
        result = local_builder.build_thing_core(
            description, target_path, tech, model="qwen3-coder:30b",
        )
    except RuntimeError as exc:
        return {
            "kind": "build", "reply": f"⚠️ /local build failed: {exc}",
            "meta": {"target_path": target_path},
        }
    notes_line = "" if result.notes == "ok" else f"\n  ⚠ {result.notes}"
    return {
        "kind": "build",
        "reply": (
            f"🛠️ /local built {result.path}\n"
            f"  tech    : {result.tech_stack}\n"
            f"  size    : {result.bytes_written} bytes / {result.lines} lines\n"
            f"  wall    : {result.wall_seconds}s on {result.backend}{notes_line}"
        ),
        "meta": {
            "target_path": result.path, "tech_stack": result.tech_stack,
            "wall_seconds": result.wall_seconds, "notes": result.notes,
        },
    }


def _route_slash_command(parsed: dict) -> dict:
    """Dispatch a parsed slash command to the right backend. Synchronous
    for /quick + /local (they're either fast or the listener wraps them
    in a background task). /code, /pro, /real return immediately because
    the cc_dispatcher daemon picks up the inbox file out-of-band."""
    tool = parsed["tool"]
    prompt = parsed["prompt"]
    if tool == "claude_code":
        return _enqueue_tiered_dispatch(prompt, parsed["tier"])
    if tool == "local_builder":
        return _slash_local_build(prompt)
    if tool == "quick_chat":
        if not prompt.strip():
            return {"kind": "chat", "reply": "/quick: needs a question.", "meta": {}}
        try:
            reply = quick_chat(prompt)
        except Exception as exc:
            return {"kind": "chat", "reply": f"/quick error: {type(exc).__name__}: {exc}", "meta": {}}
        return {"kind": "chat", "reply": reply, "meta": {"slash": parsed["command"]}}
    return {"kind": "empty", "reply": f"unknown slash route: {tool}", "meta": {}}

# Phase 27 — local builder routing. "build me X / create X / make me X
# / code X" gets handled directly via tools/local_builder using qwen3.6
# instead of going through the heavy agent or Claude Code. The
# dispatch: prefix still wins (intercepts first), so the user can
# always force a Claude Code session for harder builds.
_BUILD_INTENT_RE = re.compile(
    r"^\s*(?:build\s+(?:me\s+)?|create\s+(?:me\s+)?|make\s+(?:me\s+)?|code\s+(?:me\s+)?)(.+)$",
    re.IGNORECASE | re.DOTALL,
)
# Optional "at <path>" suffix: pull the explicit target path. Match
# everything up to "at <path>" so the description doesn't include it.
_BUILD_AT_PATH_RE = re.compile(
    r"^(.+?)\s+at\s+(\S+)\s*$",
    re.IGNORECASE | re.DOTALL,
)
# Tech-stack hint pulled from the description ("in HTML", "as a
# python script", etc.). Default html for games / widgets.
_BUILD_TECH_RE = re.compile(
    r"\b(?:in|as|using)\s+(html|python|markdown|md|shell|bash)\b",
    re.IGNORECASE,
)
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


_INTENT_LATENCIES = Path.home() / "AI_Agent" / "memory" / "intent_latencies.jsonl"
_INTENT_LATENCIES_KEEP_HOURS = 30  # keep ~30h of history; we display 24h


def _record_intent_latency(intent: str, elapsed_s: float, *, fast_format: str | None = None,
                           tool: str | None = None) -> None:
    """Append one latency observation. Best-effort, never raises.

    Read by `nexus_api.GET /metrics/intent_latency` and the Performance
    tab on the dashboard for the rolling 24h view."""
    try:
        _INTENT_LATENCIES.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "intent": intent,
            "elapsed_s": round(elapsed_s, 3),
        }
        if fast_format:
            entry["fast_format"] = fast_format
        if tool:
            entry["tool"] = tool
        with _INTENT_LATENCIES.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("intent latency log append failed: %s", exc)


def _route_message_inner(message: str) -> dict:
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

    # Phase 28 — slash commands trump all other routing. Manual override
    # fast lane, no wiki/intent/dispatch-prefix surprises in the way.
    slash = parse_slash_command(msg)
    if slash:
        log.info("route: slash %s tool=%s tier=%s",
                 slash["command"], slash["tool"], slash.get("tier", ""))
        return _route_slash_command(slash)

    # BUG 6 — entity questions ("what is X", "tell me about X", "who is
    # X", "explain X") MUST hit the wiki before any other classification.
    # Returns None when the pattern doesn't match so the rest of routing
    # runs unchanged. Returns a grounded reply (or a clearly-tagged
    # "no wiki entry" reply) when the pattern fires.
    eq = _entity_lookup(msg)
    if eq is not None:
        log.info("route: entity-question hit wiki=%s", eq.get("meta", {}).get("wiki_hit"))
        return eq

    # Power-user override: "queue: <task>"
    qm = _QUEUE_PREFIX_RE.match(msg)
    if qm:
        body = qm.group(1).strip()
        if not body:
            return {"kind": "queue", "reply": "queue: needs a task body.", "meta": {}}
        tid = task_queue.enqueue(body)
        log.info("route: queue-prefix override -> task %s", tid)
        return {"kind": "queue", "reply": f"On it. task_id={tid}", "meta": {"task_id": tid}}

    # Phase 28 + 29 — tiered build routing. Order:
    #   1. SIMPLE_BUILD ("make a quick fizzbuzz", "write a tiny ...") →
    #      local qwen3-coder:30b. Fast, free, no API.
    #   2. BUILD_INTENT ("build me X", "create X", etc.) → tier=max
    #      (Claude Code via Max plan, $0 marginal). Phase 29 flipped
    #      this default from /code (DeepSeek Flash) → /max because
    #      Colton already pays for the Max subscription, so burning
    #      DeepSeek tokens for the default routing doesn't save money.
    #   dispatch:/queue: prefixes still win above this branch.
    bm = _BUILD_INTENT_RE.match(msg)
    if bm and not _DISPATCH_PREFIX_RE.match(msg):  # belt-and-suspenders
        if SIMPLE_BUILD_RE.search(msg):
            log.info("route: build-intent SIMPLE → /local")
            return _slash_local_build(bm.group(1).strip())
        # Phase 29 — default to Max plan, not paid DeepSeek.
        log.info("route: build-intent → claude_code tier=max")
        return _enqueue_tiered_dispatch(
            bm.group(1).strip(), "max", label=bm.group(1).strip()[:60],
        )

    # Phase 22 — "dispatch: <prompt>" / "force dispatch: <prompt>" hands
    # the prompt to the cc_dispatcher daemon (background Claude Code
    # session), NOT the regular Nexus task agent. Defense-in-depth: the
    # Telegram listener's `_handle_dispatch_command` should already
    # short-circuit this, but if a message arrives via /chat or any
    # other path we still route to the dispatcher here.
    dm = _DISPATCH_PREFIX_RE.match(msg)
    if dm:
        forced = bool(dm.group(1))
        body = (dm.group(2) or "").strip()
        if not body:
            return {"kind": "dispatch", "reply": "dispatch: needs a prompt.", "meta": {}}
        from core import cc_dispatch as _ccd
        level, spend, budget = _ccd.budget_status()
        if level == "over" and not forced:
            return {
                "kind": "dispatch",
                "reply": (
                    f"Blocked: monthly Claude Code budget exhausted "
                    f"(${spend:.2f}/${budget:.2f}). Reply with "
                    f"'force dispatch: ...' to override."
                ),
                "meta": {"budget_blocked": True},
            }
        risky = _ccd.is_risky(body)
        meta = _ccd.DispatchMeta.new(
            label=body.splitlines()[0][:60],
            time_budget_minutes=30,
            risky_match=risky,
        )
        _ccd.write_prompt(meta, body, pending=bool(risky))
        log.info("route: dispatch-prefix override -> %s (risky=%s)",
                 meta.dispatch_id, bool(risky))
        if risky:
            reply = (
                f"🚨 Risky prompt held (matched: {risky}). "
                f"Reply 'go {meta.dispatch_id}' to dispatch."
            )
        else:
            reply = (
                f"🚀 Dispatched. dispatch_id={meta.dispatch_id} "
                f"(budget {meta.time_budget_minutes}m). "
                f"I'll ping when it's done."
            )
        return {
            "kind": "dispatch", "reply": reply,
            "meta": {"dispatch_id": meta.dispatch_id, "risky": bool(risky)},
        }

    # Deterministic: "full output <task_id>" — return the unchunked
    # output from the row, since STATUS replies preview at 800 chars.
    fm = _FULL_OUTPUT_RE.match(msg)
    if fm:
        tid = fm.group(1).lower()
        return {"kind": "status", "reply": _full_output_reply(tid), "meta": {"task_id": tid}}

    # Phase 23.1 — scaffolding intent goes ahead of every other branch.
    # "Scaffold a Next.js marketplace called shoppable" → enqueue a
    # structured TASK so scaffold_project runs in the worker (with its
    # own heartbeat thread) rather than blocking the conversation
    # handler. If we can't extract a project name, ask for one.
    sc = _detect_scaffold_intent(msg)
    if sc is not None:
        if sc["missing"]:
            return {
                "kind": "scaffold",
                "reply": (
                    f"Scaffolding request detected (recipe: {sc['recipe']}). "
                    "What should I name the project? Reply with a slug "
                    "(lowercase letters, digits, hyphens), e.g. "
                    "'my-new-app'."
                ),
                "meta": {"scaffold_recipe": sc["recipe"], "needs_name": True},
            }
        agent_input = (
            f"[scaffold:{sc['recipe']}] "
            f"Use the scaffold_project tool with "
            f'name="{sc["name"]}", recipe="{sc["recipe"]}", '
            f"options={{}} — return only the tool's summary."
        )
        tid = task_queue.enqueue(agent_input)
        log.info("scaffold intent → task %s recipe=%s name=%s",
                 tid, sc["recipe"], sc["name"])
        return {
            "kind": "scaffold",
            "reply": (
                f"On it. Scaffolding `{sc['name']}` from recipe "
                f"`{sc['recipe']}`. Heartbeats will land in Telegram every "
                f"60s during long steps. task_id={tid}"
            ),
            "meta": {
                "scaffold_recipe": sc["recipe"],
                "scaffold_name": sc["name"],
                "task_id": tid,
            },
        }

    # Deterministic short-circuit BEFORE the LLM classifier — saves a
    # round-trip on the high-confidence single-tool shapes the classifier
    # sometimes mis-routes ("what's my github auth status" used to land
    # in TASK despite the prompt example).
    if _looks_like_single_fast_tool(msg):
        log.info("route: hard-override %r → QUERY_TOOL", msg[:80])
        intent_obj = type("Intent", (), {"kind": "QUERY_TOOL", "raw": "[hard-override]"})()
        meta_pre = {"classifier_raw": "hard-override", "fast_tool_override": True}
        result = lite_agent(msg)
        if result.get("ok"):
            return {
                "kind": "query_tool",
                "reply": result["reply"],
                "meta": {
                    **meta_pre,
                    "tool": result.get("tool", ""),
                    "fast_format": result.get("fast_format"),
                },
            }
        log.info("hard-override lite_agent miss; falling to classifier path: %s",
                 result.get("reason"))
        # Fall through to the classifier path on miss — gives the LLM
        # one more chance to pick TASK vs CHAT correctly.

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
                "meta": {
                    **meta,
                    "tool": result.get("tool", ""),
                    "fast_format": result.get("fast_format"),
                },
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
    #
    # BUG 10 — when the original classification was CHAT or QUERY_INLINE,
    # the user wasn't asking for a long task; they got a denial and we
    # recovered. Reply with the friendly capability-recovery line, NOT
    # "On it. task_id=…" (that's reserved for genuinely-routed TASK).
    # task_id still flows back in meta for telemetry.
    if _looks_like_denial(reply):
        log.warning(
            "quick_chat produced denial — recovering as TASK. "
            "denial_text=%r intent_was=%s msg=%r",
            reply[:160], intent.kind, msg[:120],
        )
        enqueued_input = f"[{_datetime_context()}]\n\n{msg}"
        tid = task_queue.enqueue(enqueued_input)
        original_was_task = intent.kind == "TASK"
        recovery_reply = (
            f"On it. task_id={tid}"
            if original_was_task
            else "Let me dig into that properly — one sec."
        )
        return {
            "kind": "task" if original_was_task else "chat",
            "reply": recovery_reply,
            "meta": {**meta, "task_id": tid, "recovered_from": "denial"},
        }

    return {"kind": intent.kind.lower(), "reply": reply, "meta": meta}


def route_message(message: str) -> dict:
    """Public router — wraps `_route_message_inner` with latency
    telemetry. Every call appends one line to
    `memory/intent_latencies.jsonl` so the dashboard's Performance tab
    can render the rolling-24h average per intent.

    BUG 9 — every reply gets passed through `_strip_think_final` here,
    not just inside quick_chat, so reply paths that bypass the
    quick_chat cleaner (status replies, dispatch confirmations, queue
    listings) can't accidentally leak <think> blocks if a downstream
    composer ever interpolates model output into them.
    """
    import time as _time
    t0 = _time.monotonic()
    result = _route_message_inner(message)
    elapsed = _time.monotonic() - t0
    if isinstance(result, dict) and isinstance(result.get("reply"), str):
        result["reply"] = _strip_think_final(result["reply"])
    meta = result.get("meta", {}) or {}
    _record_intent_latency(
        intent=result.get("kind", "unknown"),
        elapsed_s=elapsed,
        fast_format=meta.get("fast_format"),
        tool=meta.get("tool"),
    )
    return result


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

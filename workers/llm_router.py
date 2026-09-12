"""Phase 39 — LLM router with verbatim passthrough.

Replaces the regex intent ladder in conversation_handler with ONE
structured-output call to the brain model (core/brain.py). The router
returns a routing decision ONLY — it never rewrites, augments, or
truncates the user's message. The original message bytes are what flow
downstream, whatever the route.

Failure contract: any error (Ollama down, junk output, schema
mismatch) falls back to the safest route — quick_chat — and logs a
WARNING. The router never guesses a dispatch.
"""
from __future__ import annotations

import json
import logging
import re

from core import brain

log = logging.getLogger("nexus.llm_router")

ROUTES = ("quick_chat", "lite_agent", "task", "dispatch", "status", "wiki")
TIERS = ("quick", "local", "code", "pro", "real", "max")

# Structured-outputs JSON schema passed as the Ollama `format` param.
ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": list(ROUTES)},
        "tier": {"type": ["string", "null"], "enum": list(TIERS) + [None]},
        "recon_mode": {"type": "boolean"},
    },
    "required": ["route", "tier", "recon_mode"],
}

# Deterministic recon detection — ORed with the router's judgment so a
# prompt that says "do not modify" can never be talked into producing
# artifacts, even if the LLM misses it.
_RECON_RE = re.compile(
    r"do\s+not\s+edit|do\s+not\s+modify|do\s+not\s+push|"
    r"report\s+findings|investigate|audit|\brecon\b",
    re.IGNORECASE,
)


# Chatty guard — a short question/greeting with no "do work" verb should get an
# instant quick_chat reply, never be escalated to a heavy background task or
# cloud dispatch. The small router model over-escalates these (e.g. it filed
# "what model are you running?" as a task). Deterministic so it can't be fumbled.
_ACTION_RE = re.compile(
    r"\b(build|make|create|write|code|program|deploy|research|fix|debug|generate|"
    r"design|set\s?up|install|schedule|refactor|scaffold|implement|migrate|"
    r"scrape|compile|automate|draft|"
    # ops verbs — "Can you bash, Hermes gateway restart?" is WORK, not chat.
    # Missing these routed real requests to the no-tools chat path, which then
    # denied having shell access (telegram msg 188-191, 2026-07-04).
    r"run|rerun|execute|bash|restart|reboot|relaunch|start|stop|kill|launch|"
    r"check|verify|test|update|upgrade|pull|push|commit|revert|delete|remove|"
    r"clean\s?up|clear|tail|grep|scan|download|upload|send|ping|monitor|"
    r"investigate|audit|look\s+into|dig\s+into|show\s+me)\b",
    re.IGNORECASE,
)
# A short "restart/stop/start <some service>" is a single systemctl call —
# it belongs on the lite_agent fast path, never a Claude Code dispatch.
_SERVICE_OP_RE = re.compile(
    r"\b(restart|reboot|relaunch|bounce|start|stop)\b.{0,40}"
    r"\b(service|gateway|worker|daemon|poller|watcher|listener|dashboard|"
    r"api|bot|nexus-[\w-]+|hermes-[\w-]+)\b",
    re.IGNORECASE | re.DOTALL,
)

_CHATTY_START_RE = re.compile(
    r"^\s*(what|whats|what's|who|who's|whos|when|where|why|how|is|are|am|do|does|"
    r"did|can|could|should|would|will|which|whose|tell\s+me|explain|hi|hey|hello|"
    r"thanks|thank\s+you|good\s+morning|good\s+night|yo|sup|ok|okay)\b",
    re.IGNORECASE,
)


def is_chatty(message: str) -> bool:
    """True for a short question / greeting with no action verb — answer it
    instantly via quick_chat instead of spinning up a background task."""
    msg = (message or "").strip()
    if not msg or len(msg.split()) > 30:
        return False
    if _ACTION_RE.search(msg):
        return False  # it's a work request, keep whatever the router chose
    return bool(_CHATTY_START_RE.match(msg) or msg.rstrip().endswith("?"))


def is_recon(message: str) -> bool:
    """True when the prompt asks for read-only investigation. Disables
    visual_verify auto-fire and any HTML/screenshot generation in the
    dispatch path."""
    return bool(_RECON_RE.search(message or ""))


# "quick/simple/tiny/..." → the cheap local build tier. Deterministic so a
# small router model that fumbles the tier (e.g. echoes "quick", which is
# NOT a valid dispatch tier and would collapse a build into quick_chat)
# can't break dispatch. Mirrors the is_recon OR-guard philosophy.
_LOCAL_TIER_RE = re.compile(
    r"\b(quick|simple|tiny|small|basic|little|minimal)\b", re.IGNORECASE,
)
_VALID_DISPATCH_TIERS = ("local", "code", "pro", "real", "max")


def resolve_dispatch_tier(message: str, tier: str | None) -> str:
    """Resolve the final tier for a dispatch route. All-local
    (2026-07-12): every router-resolved dispatch runs on the local
    tier. Slash commands bypass the router, so anything reaching here
    was inferred — and inferred work must never spend cloud tokens.
    Cloud tiers (/max, /code, /pro, /api) remain available as explicit
    slash commands only."""
    return "local"


# Host/runtime-health questions always want the system_status tool, i.e.
# lite_agent. The small router conflates "running processes" with the task
# queue ("tasks running") → status. This deterministic guard forces
# lite_agent ONLY when the router landed on the wrong shelf (status /
# quick_chat); it never overrides task/dispatch/wiki (so "build a process
# monitor" stays dispatch).
# Requests for a play link / game URL — answered by the game_links tool
# in lite_agent, never by a no-tools chat model.
_GAME_LINK_RE = re.compile(
    r"(?:\b(?:link|url)\b.{0,40}\b(?:play|game)\b)"
    r"|(?:\b(?:play|game)s?\b.{0,40}\b(?:link|url)\b)"
    r"|\bpull up\b.{0,40}\bgame\b"
    r"|\bwhat games\b",
    re.IGNORECASE | re.DOTALL,
)

_SYSTEM_HEALTH_RE = re.compile(
    r"\b(?:"
    r"running processes|process list|top processes|ps aux|"
    r"system (?:status|health)|are you (?:healthy|ok|up|alive|running)|"
    r"(?:memory|ram|disk|cpu|vram|gpu) (?:usage|use|free|status|pressure)|"
    r"how(?:'?s| is| much)\s+(?:your\s+)?(?:memory|ram|disk|cpu|vram|gpu)|"
    r"what(?:'?s| is)\s+(?:loaded|resident|serving you)|"
    r"is ollama (?:up|running|healthy|alive)|"
    r"which model (?:is )?(?:loaded|running|serving)"
    r")\b",
    re.IGNORECASE,
)


def is_system_health(message: str) -> bool:
    """True for questions about Nexus's own host/runtime health."""
    return bool(_SYSTEM_HEALTH_RE.search(message or ""))


ROUTER_SYSTEM_PROMPT = """You are the message router for Nexus, Colton's personal agent.
Classify the user's message into a route. You only ROUTE — you never
answer, never rewrite the message, never add to it.

Routes:

quick_chat — greetings, small talk, thanks, opinions, quick factual
  questions answerable from general knowledge or known personal facts
  (Colton's name, role at Irex Argus, projects, preferences), date/time
  questions, vague hype with no concrete object ("wanna build something
  cool?", "let's ship something" with no named thing).
  ALSO quick_chat: opinions, statements, and suggestions — "we should
  use X", "I think Y is better", "you should try Z", "what do you think
  about W", "maybe we switch to Q". Those want a reply, not work. A
  task/dispatch requires an IMPERATIVE build/fix/create/research request
  with a specific object. Musing about a change is not asking for it.

lite_agent — quick factual question needing exactly ONE tool call NOW:
  weather lookups, one web search ("search for X", "look up X",
  "google X"), github auth status, list my repos, search my notes,
  and Nexus's OWN host/runtime health ("are you healthy", "what model
  is loaded", "show the process list", "how's memory/disk/GPU",
  "what's running", "is ollama up") — these hit the system_status tool.
  If it clearly needs more than one step, use task instead.

task — multi-step work Nexus runs itself with its full tool belt:
  research-and-summarize sweeps, fetching/reading external URLs,
  fixing or editing files in the Nexus workspace, deploys, anything
  needing several tool calls. Imperative with a SPECIFIC object.

dispatch — coding/build work for the coding dispatcher: build/
  create/fix/refactor an app, game, component, script, or repo.
  Always set tier to "local" — all dispatches run on the local model;
  cloud tiers exist only behind explicit slash commands, which never
  reach this router. Use "quick" never (that's what quick_chat is for).

status — questions about Nexus's OWN task QUEUE or a specific task id:
  "queue status", "any tasks running", "is task abc12345 done".
  This route is ONLY the task queue. "<some other domain> status"
  (github/supabase/weather/wifi) AND Nexus's host/system/runtime health
  ("system status", "are you healthy") are lite_agent, NOT status.

wiki — "what is X / who is X / tell me about X / explain X" where X is
  a project, person, or entity Nexus tracks (BidWatt, Sparky, NIMO,
  coding router, ...). General-knowledge definitions ("what is TCP")
  are quick_chat.

tier — only set for dispatch; null for every other route.

recon_mode — true when the message asks for read-only investigation,
  audit, recon, or report-only output, or says do-not-edit/modify/push.
  Otherwise false.

Examples:
"we should probably move BidWatt to Drizzle" → {"route":"quick_chat","tier":null,"recon_mode":false}
"I think the router is over-escalating lately" → {"route":"quick_chat","tier":null,"recon_mode":false}
"you should add caching to the wiki path" → {"route":"quick_chat","tier":null,"recon_mode":false}
"add caching to the wiki path" → {"route":"dispatch","tier":"local","recon_mode":false}
"build a flappy bird clone in one html file" → {"route":"dispatch","tier":"local","recon_mode":false}
"what's the weather in Pasco" → {"route":"lite_agent","tier":null,"recon_mode":false}

Respond with ONLY the JSON object. No prose."""


_FALLBACK = {"route": "quick_chat", "tier": None, "recon_mode": False}


def route_llm(message: str) -> dict:
    """Classify `message` → {route, tier, recon_mode}. Never raises.

    On any failure the decision falls back to quick_chat (the safest
    route — worst case the user gets a chat reply instead of an
    unwanted dispatch) and the failure is logged at WARNING with a
    `router_error` key in the returned dict for telemetry.
    """
    msg = (message or "").strip()
    if not msg:
        return dict(_FALLBACK)

    try:
        raw = brain.chat(
            [
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": msg[:4000]},
            ],
            # Route on the small resident model (models.json `router` →
            # qwen3:4b), not the 35B brain. Classification is a constrained
            # JSON task; this removes a full brain inference from the front
            # of every message. brain.chat still degrades to qwen3:4b on
            # failure, so worst case is identical to before.
            model=brain.get_router_model(),
            fmt=ROUTER_SCHEMA,
            options={"temperature": 0.0, "num_predict": 200},
            timeout=30.0,
        )
    except Exception as exc:
        log.warning("router LLM call failed (%s: %s) — falling back to "
                    "quick_chat for %r", type(exc).__name__, exc, msg[:80])
        return {**_FALLBACK, "router_error": f"{type(exc).__name__}: {exc}"}

    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("router returned non-JSON %r — falling back to "
                    "quick_chat for %r", (raw or "")[:120], msg[:80])
        return {**_FALLBACK, "router_error": f"non-json: {(raw or '')[:80]}"}

    if not isinstance(obj, dict) or obj.get("route") not in ROUTES:
        log.warning("router returned invalid decision %r — falling back to "
                    "quick_chat for %r", obj, msg[:80])
        return {**_FALLBACK, "router_error": f"invalid: {obj!r}"[:160]}

    tier = obj.get("tier")
    if tier is not None and tier not in TIERS:
        log.warning("router returned unknown tier %r — nulling it", tier)
        tier = None

    route = obj["route"]

    # Dispatch tier is resolved deterministically — the small router model
    # sometimes returns "quick" (invalid for dispatch) by echoing a keyword,
    # which would otherwise collapse a build into a chat reply downstream.
    if route == "dispatch":
        tier = resolve_dispatch_tier(msg, tier)

    # Host-health guard: force lite_agent when the small router misfiled a
    # system-health question as status/quick_chat. Never overrides an
    # action route (task/dispatch/wiki).
    if route in ("status", "quick_chat") and is_system_health(msg):
        route = "lite_agent"
        tier = None

    # Chatty guard: a short question/greeting with no action verb gets an instant
    # quick_chat reply — never a heavy task/dispatch. (Fixes "what model are you
    # running?" being escalated to a background task with a raw task_id.)
    if route in ("task", "dispatch") and is_chatty(msg) and not is_system_health(msg):
        route = "quick_chat"
        tier = None

    # Action guard (inverse of the chatty guard): when the small router files a
    # real "can you restart/run/check X?" request as chat, the no-tools path
    # then denies having shell access. A clear action verb deterministically
    # upgrades chat → lite_agent (has tools), mirroring the host-health guard.
    if route in ("quick_chat", "status") and _ACTION_RE.search(msg):
        route = "lite_agent"
        tier = None

    # Game-link guard: "send me the link to play it" is ONE game_links
    # tool call. Without this the router filed it as a task and a
    # no-tools qwen3:4b hallucinated a path for 4 minutes (2026-07-12).
    # Never overrides dispatch ("build a game" stays a build).
    if route in ("task", "quick_chat", "status") and _GAME_LINK_RE.search(msg):
        route = "lite_agent"
        tier = None

    # Service-op guard: "restart the hermes gateway" is ONE systemctl call —
    # lite_agent's restart_service tool does it in seconds. Without this the
    # router escalated it to a full Claude Code dispatch (tier=max).
    if (route in ("task", "dispatch") and len(msg.split()) <= 12
            and _SERVICE_OP_RE.search(msg)):
        route = "lite_agent"
        tier = None

    return {
        "route": route,
        "tier": tier,
        # OR with deterministic keyword detection — the LLM can widen
        # recon, never narrow it.
        "recon_mode": bool(obj.get("recon_mode")) or is_recon(msg),
    }

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
    """Resolve the final tier for a dispatch route. Honors an explicit
    valid tier from the model; otherwise (None / the invalid "quick" /
    junk) infers from keywords: quick/simple/tiny → local, else max."""
    if tier in _VALID_DISPATCH_TIERS:
        return tier
    return "local" if _LOCAL_TIER_RE.search(message or "") else "max"


ROUTER_SYSTEM_PROMPT = """You are the message router for Nexus, Colton's personal agent.
Classify the user's message into a route. You only ROUTE — you never
answer, never rewrite the message, never add to it.

Routes:

quick_chat — greetings, small talk, thanks, opinions, quick factual
  questions answerable from general knowledge or known personal facts
  (Colton's name, role at Irex Argus, projects, preferences), date/time
  questions, vague hype with no concrete object ("wanna build something
  cool?", "let's ship something" with no named thing).

lite_agent — quick factual question needing exactly ONE tool call NOW:
  weather lookups, one web search ("search for X", "look up X",
  "google X"), github auth status, list my repos, search my notes.
  If it clearly needs more than one step, use task instead.

task — multi-step work Nexus runs itself with its full tool belt:
  research-and-summarize sweeps, fetching/reading external URLs,
  fixing or editing files in the Nexus workspace, deploys, anything
  needing several tool calls. Imperative with a SPECIFIC object.

dispatch — coding/build work for the Claude Code dispatcher: build/
  create/fix/refactor an app, game, component, script, or repo.
  Pick tier: "local" when the user says quick/simple/tiny/small,
  "max" for everything else (default). Only use "code"/"pro"/"real"
  if the user explicitly names the tier. Use "quick" never (that's
  what quick_chat is for).

status — questions about Nexus's OWN task queue or a specific task id:
  "queue status", "any tasks running", "is task abc12345 done".
  "<some other domain> status" (github/supabase/weather/wifi status)
  is lite_agent, NOT status.

wiki — "what is X / who is X / tell me about X / explain X" where X is
  a project, person, or entity Nexus tracks (BidWatt, Sparky, NIMO,
  coding router, ...). General-knowledge definitions ("what is TCP")
  are quick_chat.

tier — only set for dispatch; null for every other route.

recon_mode — true when the message asks for read-only investigation,
  audit, recon, or report-only output, or says do-not-edit/modify/push.
  Otherwise false.

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
            options={"temperature": 0.0, "num_ctx": 8192, "num_predict": 200},
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

    # Dispatch tier is resolved deterministically — the small router model
    # sometimes returns "quick" (invalid for dispatch) by echoing a keyword,
    # which would otherwise collapse a build into a chat reply downstream.
    if obj["route"] == "dispatch":
        tier = resolve_dispatch_tier(msg, tier)

    return {
        "route": obj["route"],
        "tier": tier,
        # OR with deterministic keyword detection — the LLM can widen
        # recon, never narrow it.
        "recon_mode": bool(obj.get("recon_mode")) or is_recon(msg),
    }

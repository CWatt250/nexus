"""Nexus multi-model router.

classify(message) → one of {"fast","mid","heavy","code","design"} using a
small Ollama model (qwen3:4b by default). model_for(route) resolves the route
to an actual Ollama model id via models.json so the mapping can change
without touching code.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import ollama

ROOT = Path.home() / "AI_Agent"
MODELS_FILE = ROOT / "models.json"
RUN_LOG = ROOT / "projects" / "nexus-core" / "run-log.jsonl"
OLLAMA_URL = "http://localhost:11434"

ROUTES = ("fast", "mid", "heavy", "code", "design")
DEFAULT_ROUTE = "mid"

SYSTEM_PROMPT = """You classify a user message and return which model route should handle it.

Routes:
- fast: greetings, yes/no, simple questions, short lookups, basic math
- mid: general chat, summaries, explanations of concepts
- heavy: complex reasoning, multi-step plans, architecture / strategy decisions
- code: any request involving code, debugging, building software, technical implementation
- design: any request about UI, UX, visual design, layout, or look/feel

Rules:
- Code beats heavy if code is involved. Design beats heavy if the task is about visual design.
- If unsure between two non-specialist routes, prefer the higher-capability one.

Respond with ONLY a JSON object: {"route": "<one of: fast | mid | heavy | code | design>"}
Do not include any text before or after the JSON. No reasoning, no commentary."""


def load_models() -> dict:
    # Phase 39 — brain transplant: hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:Q4_K_M owns heavy/code/design
    # and the route classifier; qwen3:4b stays as the fast tier.
    if not MODELS_FILE.exists():
        return {
            "brain": "hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:Q4_K_M",
            "router": "hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:Q4_K_M",
            "fast": "qwen3:4b",
            "mid": "qwen3:8b",
            "heavy": "hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:Q4_K_M",
            "code": "hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:Q4_K_M",
            "design": "hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:Q4_K_M",
        }
    try:
        return json.loads(MODELS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def model_for(route: str) -> str:
    """Resolve a route name to an Ollama model id. Falls back to the brain."""
    models = load_models()
    return models.get(route) or models.get("heavy") or "hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:Q4_K_M"


def _router_model() -> str:
    return load_models().get("router", "hf.co/deepreinforce-ai/Ornith-1.0-35B-GGUF:Q4_K_M")


def _log_routing(message: str, route: str, model: str, extra: dict | None = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": "router",
        "route": route,
        "model": model,
        "preview": (message or "").strip()[:160],
    }
    if extra:
        entry.update(extra)
    try:
        RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
        with RUN_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _normalize(raw: str) -> str | None:
    """Pull a route token out of arbitrary model output.
    Prefers a JSON `{\"route\": \"<name>\"}`, then falls back to scanning the text."""
    if not raw:
        return None
    stripped = raw.strip()
    # Try JSON first.
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            cand = str(obj.get("route", "")).strip().lower()
            if cand in ROUTES:
                return cand
    except json.JSONDecodeError:
        pass
    # Fall back to scanning the text for a route word.
    t = stripped.lower()
    for r in ROUTES:
        if t == r or t.startswith(r):
            return r
    # Longest-match first so "design" wins over "mid" if both appear.
    ordered = ("design", "heavy", "code", "fast", "mid")
    for r in ordered:
        if r in t:
            return r
    return None


def classify(message: str, *, log: bool = True) -> str:
    """Return one of ROUTES. Synchronous call via the ollama python client."""
    if not message or not message.strip():
        route = DEFAULT_ROUTE
        if log:
            _log_routing(message, route, model_for(route), {"reason": "empty"})
        return route

    router_model = _router_model()
    try:
        from core import brain as _brain  # noqa: PLC0415
        think = _brain.think_param(router_model)
    except Exception:
        think = False
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=router_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message.strip()[:2000]},
            ],
            stream=False,
            think=think,
            format="json",
            options={"temperature": 0.0, "num_predict": 64, "num_ctx": 2048},
            keep_alive=-1,
        )
    except Exception as exc:
        route = DEFAULT_ROUTE
        if log:
            _log_routing(message, route, model_for(route), {"error": f"{type(exc).__name__}: {exc}"})
        return route

    raw = ""
    if isinstance(resp, dict):
        raw = resp.get("message", {}).get("content", "") or ""
    else:
        msg = getattr(resp, "message", None)
        raw = getattr(msg, "content", "") or ""

    route = _normalize(raw) or DEFAULT_ROUTE
    if log:
        _log_routing(message, route, model_for(route), {"raw": raw.strip()[:80]})
    return route


def classify_and_model(message: str, *, log: bool = True) -> tuple[str, str]:
    route = classify(message, log=log)
    return route, model_for(route)


if __name__ == "__main__":
    import sys
    msg = " ".join(sys.argv[1:]) or "hello"
    r, m = classify_and_model(msg)
    print(f"{r}\t{m}")

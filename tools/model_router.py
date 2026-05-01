# tools/model_router.py — Multi-model routing for Nexus
# Routes messages to appropriate model based on complexity.
# Replaces/enhances the inline routing in router.py.

from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from enum import StrEnum
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path.home() / "AI_Agent"
MODELS_FILE = ROOT / "models.json"
OLLAMA_URL = "http://localhost:11434"

# Routes: fast → trivial, mid → moderate, heavy → complex, code → coding, design → design
class Route(StrEnum):
    FAST = "fast"       # simple fact, greeting, math — qwen3:4b
    MID = "mid"         # medium complexity — qwen3:4b or qwen3:14b
    HEAVY = "heavy"     # deep reasoning, coding, multi-step — qwen3.6
    CODE = "code"       # code changes — qwen3.6
    DESIGN = "design"   # design tasks — qwen3.6


@dataclass
class RoutingDecision:
    route: str
    model: str
    confidence: float  # 0.0–1.0
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


# Load models from models.json: { "fast": "qwen3:4b", "heavy": "qwen3.6", ... }
_MODEL_MAP: dict[str, str] = {}

def _load_models() -> dict[str, str]:
    global _MODEL_MAP
    if _MODEL_MAP:
        return _MODEL_MAP
    if MODELS_FILE.exists():
        try:
            with open(MODELS_FILE) as f:
                raw = json.load(f)
            # models.json might be { "routes": { "fast": "qwen3:4b", ... }, "default": "qwen3.6" }
            routes = raw.get("routes", raw)
            _MODEL_MAP = {k: v for k, v in routes.items() if isinstance(v, str)}
        except Exception:
            _MODEL_MAP = {}
    # Defaults
    _MODEL_MAP.setdefault("fast", "qwen3:4b")
    _MODEL_MAP.setdefault("mid", "qwen3:4b")
    _MODEL_MAP.setdefault("heavy", "qwen3.6")
    _MODEL_MAP.setdefault("code", "qwen3.6")
    _MODEL_MAP.setdefault("design", "qwen3.6")
    return _MODEL_MAP


def _ollama_classify(message: str) -> Optional[dict]:
    """Use local Ollama to classify a message into a route."""
    prompt = (
        "Classify this message into exactly ONE route from: [fast, mid, heavy, code, design]. "
        "Return ONLY a JSON object with keys: route (one of the five), confidence (0.0-1.0), "
        "reason (one sentence why). No other text.\n\nMessage:\n---\n{msg}\n---"
    ).format(msg=message)
    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": "qwen3:4b", "prompt": prompt, "stream": False},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def classify(message: str) -> RoutingDecision:
    """Classify a message and pick the right model. Fast heuristic first, Ollama fallback."""
    _load_models()
    msg_len = len(message)
    has_tool_keywords = any(
        kw in message.lower()
        for kw in ["git", "code", "edit", "create", "build", "deploy", "test", "docker", "api", "database", "schema"]
    )
    has_complex_signals = any(
        kw in message.lower()
        for kw in ["plan", "design", "architect", "refactor", "migrate", "debug", "investigate", "why", "how does"]
    )
    is_code_related = any(
        kw in message.lower()
        for kw in ["write code", "fix", "implement", "add feature", "change file", "commit", "push"]
    )

    # Heuristic classification
    if msg_len < 20 and not has_tool_keywords and not has_complex_signals and not is_code_related:
        route, reason, conf = Route.FAST, "short and simple", 0.9
    elif is_code_related:
        route, reason, conf = Route.CODE, "code-related keywords detected", 0.85
    elif has_complex_signals or has_tool_keywords:
        route, reason, conf = Route.HEAVY, "complex signals present", 0.8
    elif msg_len < 80:
        route, reason, conf = Route.MID, "moderate length, no strong signals", 0.6
    else:
        route, reason, conf = Route.HEAVY, "long message, likely complex", 0.5

    # Ollama refinement (best-effort, don't block)
    ollama_result = _ollama_classify(message)
    if ollama_result and "response" in ollama_result:
        raw_resp = ollama_result["response"]
        # Try to parse route from ollama response
        try:
            start = raw_resp.index("{")
            end = raw_resp.rindex("}") + 1
            parsed = json.loads(raw_resp[start:end])
            if parsed.get("route") in [r.value for r in Route]:
                route = Route(parsed["route"])
                conf = float(parsed.get("confidence", conf))
                if parsed.get("reason"):
                    reason = parsed["reason"]
        except (json.JSONDecodeError, ValueError):
            pass  # Ignore parse errors, stick with heuristic

    model = _MODEL_MAP.get(route.value, "qwen3:6")
    return RoutingDecision(route=route, model=model, confidence=conf, reason=reason)


def route(message: str) -> str:
    """Quick route() returns a one-line summary for logging."""
    d = classify(message)
    return f"{d.route} → {d.model} (conf={d.confidence:.2f}) {d.reason}"


# Tool wrapper
def model_router_tool(message: str) -> str:
    """Route a message to the right model. Returns JSON decision."""
    return json.dumps(classify(message).to_dict(), indent=2)

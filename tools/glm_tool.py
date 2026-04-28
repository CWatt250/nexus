"""GLM-5.1 / GLM-4.6 escalation path (Phase 14.6).

When the local stack stalls — three failed retries on the same task — the
orchestrator can hand the prompt to Z.ai's coding-class model for a second
opinion. Every call is logged to `memory/external-calls.jsonl` with a USD
cost estimate; spend in the current calendar month is summed against a
soft budget cap and we refuse new calls once the cap is hit.

Configuration via `~/AI_Agent/.env`:
  Z_AI_API_KEY=<key>             # required
  GLM_MODEL=glm-4.6               # optional (default below)
  GLM_BUDGET_USD=50               # optional monthly cap

Pricing approximation (per 1M tokens, USD):
  glm-4.6:  in=0.60  out=2.20
  glm-4.5:  in=0.60  out=2.20
  fallback: in=1.00  out=3.00     (conservative)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from langchain_core.tools import tool

ROOT = Path.home() / "AI_Agent"
load_dotenv(ROOT / ".env")

EXTERNAL_LOG = ROOT / "memory" / "external-calls.jsonl"
ALERT_LOG = ROOT / "memory" / "external-budget-alerts.jsonl"
DEFAULT_MODEL = os.getenv("GLM_MODEL", "glm-4.6")
BUDGET_USD = float(os.getenv("GLM_BUDGET_USD", "50"))
API_BASE = os.getenv("GLM_API_BASE", "https://api.z.ai/api/paas/v4")

log = logging.getLogger("nexus.glm")

_PRICING = {
    "glm-4.6":     {"in": 0.60, "out": 2.20},
    "glm-4.5":     {"in": 0.60, "out": 2.20},
    "glm-4-plus":  {"in": 0.60, "out": 2.20},
    "glm-5.1":     {"in": 0.60, "out": 2.20},
    "_default":    {"in": 1.00, "out": 3.00},
}


def _api_key() -> str | None:
    key = os.getenv("Z_AI_API_KEY") or os.getenv("ZHIPU_API_KEY") or os.getenv("GLM_API_KEY")
    return key.strip() if key else None


def _pricing(model: str) -> dict:
    base = re.split(r"[/:@]", model)[-1].lower()
    return _PRICING.get(base) or _PRICING["_default"]


def _cost_estimate(model: str, tokens_in: int, tokens_out: int) -> float:
    p = _pricing(model)
    return tokens_in / 1_000_000 * p["in"] + tokens_out / 1_000_000 * p["out"]


def _month_key(when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    return when.strftime("%Y-%m")


def _spend_this_month() -> float:
    if not EXTERNAL_LOG.exists():
        return 0.0
    month = _month_key()
    total = 0.0
    try:
        for line in EXTERNAL_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = obj.get("ts", "")
            if isinstance(ts, str) and ts.startswith(month):
                total += float(obj.get("cost_usd") or 0.0)
    except OSError:
        return 0.0
    return total


def _log_external_call(record: dict) -> None:
    EXTERNAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with EXTERNAL_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("external-calls log write failed: %s", exc)


def _maybe_alert(prev_spend: float, new_spend: float, budget: float) -> str | None:
    """Emit a one-line alert when the spend crosses the 50/80/100% bands.
    Returns the alert message (or None) so the caller can also surface it."""
    if budget <= 0:
        return None
    bands = (0.5, 0.8, 1.0)
    crossed = next(
        (b for b in bands if prev_spend < b * budget <= new_spend),
        None,
    )
    if crossed is None:
        return None
    msg = (
        f"GLM monthly spend crossed {int(crossed*100)}% of ${budget:.0f} "
        f"(now ${new_spend:.2f})."
    )
    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ALERT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "band_pct": int(crossed * 100),
                "spend_usd": round(new_spend, 4),
                "budget_usd": budget,
                "message": msg,
            }) + "\n")
    except OSError:
        pass
    # Telegram alerts are gated until Phase 15 (the bot is offline). The
    # alert is still durable in the JSONL so it can be replayed.
    return msg


def _missing_key_message() -> str:
    return (
        "GLM escalation unavailable: set Z_AI_API_KEY in ~/AI_Agent/.env "
        "(or ZHIPU_API_KEY / GLM_API_KEY). Get a key at https://z.ai/."
    )


@tool
def glm_consult(prompt: str, reason: str = "", model: str | None = None) -> str:
    """Send `prompt` to GLM (Z.ai) for a second-opinion answer when local
    models have failed or hit a wall.

    Args:
        prompt: full text to send.
        reason: short note about why we're escalating (logged for audit).
        model: override; defaults to glm-4.6.

    Logs every call to ~/AI_Agent/memory/external-calls.jsonl with token
    usage and a USD cost estimate. Refuses new calls when the rolling
    monthly spend has reached the GLM_BUDGET_USD cap (default $50)."""
    key = _api_key()
    if not key:
        return _missing_key_message()
    if not prompt or not prompt.strip():
        return "GLM consult skipped: empty prompt."

    model = model or DEFAULT_MODEL
    prev_spend = _spend_this_month()
    if prev_spend >= BUDGET_USD:
        return (
            f"GLM consult refused: monthly cap ${BUDGET_USD:.0f} already hit "
            f"(spent ${prev_spend:.2f} so far this month)."
        )

    started = time.monotonic()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": "glm_consult",
        "model": model,
        "reason": (reason or "")[:200],
        "prompt_chars": len(prompt),
        "ok": False,
    }
    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(f"{API_BASE}/chat/completions", json=body, headers=headers)
        elapsed_ms = (time.monotonic() - started) * 1000
        if resp.status_code != 200:
            record.update({
                "elapsed_ms": round(elapsed_ms, 1),
                "status": resp.status_code,
                "error": resp.text[:500],
                "cost_usd": 0.0,
            })
            _log_external_call(record)
            return f"GLM error {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
    except Exception as exc:
        record.update({
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "error": f"{type(exc).__name__}: {exc}",
            "cost_usd": 0.0,
        })
        _log_external_call(record)
        return f"GLM call failed: {type(exc).__name__}: {exc}"

    choices = data.get("choices") or []
    content = ((choices[0] if choices else {}).get("message") or {}).get("content", "") or ""
    usage = data.get("usage") or {}
    tin = int(usage.get("prompt_tokens") or 0)
    tout = int(usage.get("completion_tokens") or 0)
    cost = _cost_estimate(model, tin, tout)
    new_spend = prev_spend + cost
    alert = _maybe_alert(prev_spend, new_spend, BUDGET_USD)

    record.update({
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        "tokens_in": tin,
        "tokens_out": tout,
        "cost_usd": round(cost, 6),
        "month_spend_usd_after": round(new_spend, 4),
        "ok": True,
    })
    _log_external_call(record)

    out = content.strip()
    if alert:
        out = f"{out}\n\n⚠️ {alert}"
    return out


GLM_TOOLS = [glm_consult]

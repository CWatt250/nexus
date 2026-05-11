"""Provider layer for quick_chat / classifier (Phase 32).

Adds a DeepSeek path in front of the existing Ollama qwen3:4b / qwen3.6
fallback, with three guardrails:

1. Per-call timeout + retry-as-fallback. DeepSeek failure (timeout, 5xx,
   network) raises `ProviderError` so the caller can fall through to the
   existing Ollama path without changing its denial-detection logic.

2. Circuit breaker — 3 consecutive failures opens the breaker for 5
   minutes; the chat path skips DeepSeek directly during that window.
   State persists at memory/quick_chat_circuit.json so a process restart
   doesn't reset a bad streak.

3. Daily cost ceiling — every successful DeepSeek call appends to
   memory/quick_chat_costs.jsonl. When today's total crosses the
   `daily_cost_max_usd` from config/cost_limits.yaml, the next call is
   refused and a one-shot Telegram warning fires.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

log = logging.getLogger("nexus.quick_chat_providers")

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"

# DeepSeek deepseek-chat published pricing (May 2026):
#   input  $0.27 / 1M tokens
#   output $1.10 / 1M tokens
# Updated when DeepSeek changes their list price.
DEEPSEEK_INPUT_USD_PER_TOKEN = 0.27 / 1_000_000
DEEPSEEK_OUTPUT_USD_PER_TOKEN = 1.10 / 1_000_000

CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN_S = 5 * 60

_MEMORY = ROOT / "memory"
_COST_LOG = _MEMORY / "quick_chat_costs.jsonl"
_CIRCUIT_LOG = _MEMORY / "quick_chat_circuit.jsonl"
_CIRCUIT_STATE = _MEMORY / "quick_chat_circuit.json"
_BUDGET_ALERT_FLAG = _MEMORY / "quick_chat_budget_alert"

_state_lock = threading.Lock()


class ProviderError(Exception):
    """Raised when the DeepSeek call fails for any reason — timeout,
    HTTP 4xx/5xx, network, malformed response. Caller should treat this
    as a signal to fall back to Ollama."""


class BudgetExceeded(ProviderError):
    """Raised when the daily DeepSeek spend ceiling is hit. Caller falls
    back to Ollama for the rest of the day."""


# ── Config (cost_limits.yaml) ────────────────────────────────────────

def _load_quick_chat_config() -> dict:
    """Read the `quick_chat:` block from config/cost_limits.yaml. Empty
    dict on any failure so callers always get a usable mapping."""
    try:
        import yaml  # noqa: PLC0415
        path = ROOT / "config" / "cost_limits.yaml"
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return (data.get("quick_chat") or {}) if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning("cost_limits.yaml load failed: %s", exc)
        return {}


def get_daily_cost_max_usd() -> float:
    cfg = _load_quick_chat_config()
    try:
        return float(cfg.get("daily_cost_max_usd", 5.0))
    except (TypeError, ValueError):
        return 5.0


def get_configured_provider() -> str:
    """Returns 'deepseek' or 'ollama'. The cost_limits.yaml override
    lets the user force-pin local without touching code."""
    cfg = _load_quick_chat_config()
    return str(cfg.get("provider", "deepseek")).lower()


def get_deepseek_model() -> str:
    cfg = _load_quick_chat_config()
    return str(cfg.get("deepseek_model", DEEPSEEK_DEFAULT_MODEL))


def get_ollama_fallback_model() -> str:
    cfg = _load_quick_chat_config()
    return str(cfg.get("ollama_fallback_model", "qwen3:4b"))


# ── Circuit breaker ──────────────────────────────────────────────────

def _read_circuit_state() -> dict:
    if not _CIRCUIT_STATE.exists():
        return {"consecutive_failures": 0, "open_until": 0.0}
    try:
        return json.loads(_CIRCUIT_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"consecutive_failures": 0, "open_until": 0.0}


def _write_circuit_state(state: dict) -> None:
    try:
        _MEMORY.mkdir(parents=True, exist_ok=True)
        _CIRCUIT_STATE.write_text(json.dumps(state), encoding="utf-8")
    except OSError as exc:
        log.warning("circuit state write failed: %s", exc)


def _log_circuit_event(event: str, **fields) -> None:
    try:
        _MEMORY.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "event": event,
            **fields,
        }
        with _CIRCUIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("circuit log append failed: %s", exc)


def is_circuit_open() -> bool:
    """True when the breaker is currently tripped — caller skips DeepSeek
    and goes straight to Ollama."""
    with _state_lock:
        state = _read_circuit_state()
        return time.time() < float(state.get("open_until", 0.0))


def _record_failure(reason: str) -> None:
    with _state_lock:
        state = _read_circuit_state()
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
        if state["consecutive_failures"] >= CIRCUIT_BREAKER_THRESHOLD:
            state["open_until"] = time.time() + CIRCUIT_BREAKER_COOLDOWN_S
            _log_circuit_event(
                "open",
                failures=state["consecutive_failures"],
                cooldown_s=CIRCUIT_BREAKER_COOLDOWN_S,
                reason=reason,
            )
        else:
            _log_circuit_event(
                "failure",
                failures=state["consecutive_failures"],
                reason=reason,
            )
        _write_circuit_state(state)


def _record_success() -> None:
    with _state_lock:
        state = _read_circuit_state()
        was_open = float(state.get("open_until", 0.0)) > 0 or int(state.get("consecutive_failures", 0)) > 0
        state["consecutive_failures"] = 0
        state["open_until"] = 0.0
        if was_open:
            _log_circuit_event("reset")
        _write_circuit_state(state)


# ── Cost telemetry ───────────────────────────────────────────────────

def _today_utc_date() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


def _record_cost(model: str, input_tokens: int, output_tokens: int,
                 cost_usd: float) -> None:
    try:
        _MEMORY.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "est_cost_usd": round(cost_usd, 6),
        }
        with _COST_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("cost log append failed: %s", exc)


def daily_cost_usd(date_utc: str | None = None) -> float:
    """Sum est_cost_usd across all entries for the given UTC date. Cheap
    — log is bounded by daily volume."""
    target = date_utc or _today_utc_date()
    if not _COST_LOG.exists():
        return 0.0
    total = 0.0
    try:
        for raw in _COST_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ts = entry.get("ts", "")
            try:
                dt = datetime.fromisoformat(ts).astimezone(timezone.utc)
            except (TypeError, ValueError):
                continue
            if dt.strftime("%Y-%m-%d") == target:
                try:
                    total += float(entry.get("est_cost_usd", 0.0))
                except (TypeError, ValueError):
                    continue
    except OSError:
        return 0.0
    return total


def _maybe_alert_budget(spent: float, ceiling: float) -> None:
    """One alert per UTC day. Marker file holds the date we last warned —
    if today's date differs, we send and rewrite."""
    today = _today_utc_date()
    try:
        if _BUDGET_ALERT_FLAG.exists():
            last = _BUDGET_ALERT_FLAG.read_text(encoding="utf-8").strip()
            if last == today:
                return
    except OSError:
        pass
    msg = (
        f"⚠️ quick_chat DeepSeek daily ceiling hit: ${spent:.4f} of "
        f"${ceiling:.2f}. Falling back to Ollama qwen3:4b for the rest "
        f"of {today}. Raise daily_cost_max_usd in config/cost_limits.yaml "
        f"or wait for UTC midnight."
    )
    try:
        from tools.telegram_tool import telegram_notify  # noqa: PLC0415
        telegram_notify.invoke({"message": msg})
    except Exception as exc:
        log.warning("budget telegram alert failed: %s", exc)
    try:
        _BUDGET_ALERT_FLAG.write_text(today, encoding="utf-8")
    except OSError as exc:
        log.warning("budget alert flag write failed: %s", exc)


# ── DeepSeek client ──────────────────────────────────────────────────

def _get_api_key() -> str:
    from core import secrets as _secrets  # noqa: PLC0415
    key = _secrets.get("DEEPSEEK_API_KEY") or ""
    if not key:
        raise ProviderError("DEEPSEEK_API_KEY missing from secrets.yaml")
    return key


def deepseek_chat(message: str, system_prompt: str, *,
                  model: str | None = None,
                  max_tokens: int = 512,
                  temperature: float = 0.7,
                  timeout: float = 15.0,
                  history: list[dict] | None = None) -> tuple[str, dict]:
    """Call DeepSeek's /v1/chat/completions and return (reply, usage).

    Raises ProviderError on any failure mode the caller should fall back
    on (HTTP error, timeout, network, malformed response, missing key,
    breaker open, daily budget exceeded). Caller catches this single
    exception type and routes to Ollama.

    `usage` is the parsed `usage` dict from DeepSeek's response, useful
    for cost telemetry. Cost logging happens here so every successful
    DeepSeek call is recorded — caller doesn't need to remember.

    `history` (Phase 38) is an optional list of prior {role, content}
    turns inserted between the system prompt and the current user
    message. When None or empty, behavior is identical to pre-Phase-38.
    """
    if is_circuit_open():
        raise ProviderError("circuit breaker open")

    ceiling = get_daily_cost_max_usd()
    spent = daily_cost_usd()
    if ceiling > 0 and spent >= ceiling:
        _maybe_alert_budget(spent, ceiling)
        raise BudgetExceeded(f"daily ceiling ${ceiling:.2f} reached (spent ${spent:.4f})")

    api_key = _get_api_key()
    chosen_model = model or get_deepseek_model()
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": message})
    payload = {
        "model": chosen_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                DEEPSEEK_BASE_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.TimeoutException as exc:
        _record_failure(f"timeout: {exc}")
        raise ProviderError(f"deepseek timeout: {exc}") from exc
    except httpx.HTTPError as exc:
        _record_failure(f"http error: {exc}")
        raise ProviderError(f"deepseek http error: {exc}") from exc

    if resp.status_code >= 500:
        _record_failure(f"5xx: {resp.status_code}")
        raise ProviderError(f"deepseek 5xx: {resp.status_code}")
    if resp.status_code >= 400:
        # 4xx is a client error, not a server flake — don't trip the
        # breaker (it'll just keep failing) but still surface as a
        # fall-back trigger so Ollama answers the user.
        body = resp.text[:200]
        log.warning("deepseek 4xx %s: %s", resp.status_code, body)
        raise ProviderError(f"deepseek 4xx: {resp.status_code}: {body}")

    try:
        data = resp.json()
        reply = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {}) or {}
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        _record_failure(f"parse error: {exc}")
        raise ProviderError(f"deepseek malformed response: {exc}") from exc

    input_tokens = int(usage.get("prompt_tokens", 0) or 0)
    output_tokens = int(usage.get("completion_tokens", 0) or 0)
    cost_usd = (input_tokens * DEEPSEEK_INPUT_USD_PER_TOKEN
                + output_tokens * DEEPSEEK_OUTPUT_USD_PER_TOKEN)
    _record_cost(chosen_model, input_tokens, output_tokens, cost_usd)
    _record_success()
    return reply.strip(), usage

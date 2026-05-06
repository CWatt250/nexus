"""Phase 32 verification — DeepSeek-as-primary smoke test.

Exercises quick_chat() and classify_intent_llm() at the function level
(no Telegram, no service restart needed). Run from the project venv:

    ~/AI_Agent/venv/bin/python ~/AI_Agent/scripts/verify_phase32.py

Outputs:
  - Reply text for tests (a) "hey what's up", (b) "what's 2+2",
    (d) "list all my running services" — verbatim.
  - Last 5 lines of memory/quick_chat_denials.jsonl + quick_chat_costs.jsonl.
  - Circuit-breaker trip + reset by injecting forced failures.
  - Provider/config reflection so a misconfigured run is obvious.

Test (c) "/code echo ..." is intentionally not exercised here — slash
dispatch goes through cc_dispatcher.py and is unrelated to the
QUICK_CHAT swap. Run it manually from Telegram per the phase plan.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _tail(path: Path, n: int = 5) -> list[str]:
    if not path.exists():
        return [f"(no file at {path})"]
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:] if lines else ["(empty file)"]


def _print_tail(label: str, path: Path, n: int = 5) -> None:
    print(f"\n--- {label} (last {n} lines of {path.name}) ---")
    for line in _tail(path, n):
        print(line)


def _show_config() -> None:
    from workers import conversation_handler as ch
    from workers import quick_chat_providers as qcp

    print("=== Phase 32 config snapshot ===")
    print(f"QUICK_CHAT_PROVIDER          = {ch.QUICK_CHAT_PROVIDER}")
    print(f"QUICK_CHAT_FALLBACK_PROVIDER = {ch.QUICK_CHAT_FALLBACK_PROVIDER}")
    print(f"QUICK_CHAT_OLLAMA_MODEL      = {ch.QUICK_CHAT_OLLAMA_MODEL}")
    print(f"CLASSIFIER_PROVIDER          = {ch.CLASSIFIER_PROVIDER}")
    print(f"CLASSIFIER_FALLBACK          = {ch.CLASSIFIER_FALLBACK}")
    print(f"yaml.provider                = {qcp.get_configured_provider()}")
    print(f"yaml.deepseek_model          = {qcp.get_deepseek_model()}")
    print(f"yaml.daily_cost_max_usd      = {qcp.get_daily_cost_max_usd()}")
    print(f"yaml.ollama_fallback_model   = {qcp.get_ollama_fallback_model()}")


def _run_chat_tests() -> None:
    from workers import conversation_handler as ch

    cases = [
        ("a", "hey what's up"),
        ("b", "what's 2+2"),
        ("d", "list all my running services"),
    ]
    print("\n=== quick_chat() replies (tests a, b, d) ===")
    for label, msg in cases:
        t0 = time.monotonic()
        try:
            reply = ch.quick_chat(msg)
        except Exception as exc:
            reply = f"(EXCEPTION: {type(exc).__name__}: {exc})"
        elapsed = time.monotonic() - t0
        print(f"\n[{label}] msg={msg!r}  elapsed={elapsed:.2f}s")
        print(f"     reply: {reply}")


def _run_classifier_smoke() -> None:
    from workers import conversation_handler as ch

    print("\n=== classify_intent_llm() smoke ===")
    samples = [
        ("hey what's up", "CHAT"),
        ("what's 2+2", "QUERY_INLINE"),
        ("build me a Next.js scaffold with auth", "TASK"),
    ]
    for msg, expected in samples:
        t0 = time.monotonic()
        try:
            intent = ch.classify_intent_llm(msg)
            kind = intent.kind
        except Exception as exc:
            kind = f"ERROR: {exc}"
        elapsed = time.monotonic() - t0
        ok = "✓" if kind == expected else "✗"
        print(f"  {ok} {msg!r:55} → {kind:14} (expected {expected}, {elapsed:.2f}s)")


def _exercise_circuit_breaker() -> None:
    """Force three failures by hitting deepseek_chat with a bogus key,
    then confirm the breaker opens and closes correctly. Restores state
    afterward so production traffic isn't affected.
    """
    from workers import quick_chat_providers as qcp

    print("\n=== Circuit breaker exercise ===")
    saved_state = qcp._read_circuit_state().copy()
    # Force-clear so the test starts from zero.
    qcp._write_circuit_state({"consecutive_failures": 0, "open_until": 0.0})

    print(f"  before: {qcp._read_circuit_state()}")
    for i in range(qcp.CIRCUIT_BREAKER_THRESHOLD):
        qcp._record_failure(f"synthetic test failure #{i+1}")
        print(f"  after fail {i+1}: {qcp._read_circuit_state()}")

    print(f"  is_circuit_open() = {qcp.is_circuit_open()}  (expect True)")
    qcp._record_success()
    print(f"  after success:    {qcp._read_circuit_state()}")
    print(f"  is_circuit_open() = {qcp.is_circuit_open()}  (expect False)")

    # Restore whatever state was there before so a real outage isn't
    # masked by a test reset.
    qcp._write_circuit_state(saved_state)
    print(f"  restored: {saved_state}")


def main() -> int:
    _show_config()
    _run_chat_tests()
    _run_classifier_smoke()
    _exercise_circuit_breaker()

    mem = ROOT / "memory"
    _print_tail("denials", mem / "quick_chat_denials.jsonl")
    _print_tail("costs", mem / "quick_chat_costs.jsonl")
    _print_tail("circuit events", mem / "quick_chat_circuit.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase 29 verification — /max tier, /api rename, tier-specific cost gates.

Run from the project venv:

    ~/AI_Agent/venv/bin/python ~/AI_Agent/scripts/verify_phase29.py

All assertions must pass. Non-zero exit → at least one failed.

Gates checked:
  A. Slash parser recognises /max /code /pro /api /real /local /quick
  B. /real maps to tier="api" with deprecation flag
  C. Max-tier env has no ANTHROPIC_API_KEY / _BASE_URL / _AUTH_TOKEN
  D. cost_limits.yaml loads with per-tier ceilings (Phase 29 schema)
  E. Complex build prompt (no slash) → tier="max" via intent regex
  F. Phase 32 sentinel — QUICK_CHAT_PROVIDER + CLASSIFIER_PROVIDER unchanged
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = "✓"
FAIL = "✗"
_failures: list[str] = []


def _assert(label: str, cond: bool, detail: str = "") -> None:
    mark = PASS if cond else FAIL
    suffix = f"  [{detail}]" if detail and not cond else ""
    print(f"  {mark} {label}{suffix}")
    if not cond:
        _failures.append(label)


# ─── Gate A — slash parser ─────────────────────────────────────────────────
def check_slash_parser() -> None:
    print("\n[A] Slash parser")
    from workers.conversation_handler import parse_slash_command, SLASH_COMMANDS

    expected_slashes = ["/max", "/code", "/pro", "/api", "/real", "/local", "/quick"]
    for slash in expected_slashes:
        in_dict = slash in SLASH_COMMANDS
        parsed = parse_slash_command(f"{slash} test prompt")
        parsed_ok = parsed is not None and parsed["command"] == slash
        _assert(f"{slash} in SLASH_COMMANDS", in_dict)
        _assert(f"parse_slash_command('{slash} test prompt') returns correct dict",
                parsed_ok, str(parsed))


# ─── Gate B — /real deprecation alias ─────────────────────────────────────
def check_real_alias() -> None:
    print("\n[B] /real → /api deprecation alias")
    from workers.conversation_handler import SLASH_COMMANDS, parse_slash_command

    spec = SLASH_COMMANDS.get("/real", {})
    _assert("/real tier == 'api'", spec.get("tier") == "api",
            f"got tier={spec.get('tier')!r}")
    _assert("/real has deprecated_alias_for == '/api'",
            spec.get("deprecated_alias_for") == "/api",
            str(spec))

    parsed = parse_slash_command("/real build a widget")
    _assert("parse_slash_command('/real ...') returns tier='api'",
            parsed is not None and parsed.get("tier") == "api",
            str(parsed))
    _assert("parse_slash_command('/real ...') carries deprecated_alias_for",
            parsed is not None and parsed.get("deprecated_alias_for") == "/api",
            str(parsed))


# ─── Gate C — max tier env isolation ──────────────────────────────────────
def check_max_env() -> None:
    print("\n[C] Max-tier env isolation (no API-key bleed)")
    import importlib
    import unittest.mock as mock

    # Inject sentinel values into the test environment so the "pop"
    # logic has something to remove. We mock os.environ rather than
    # mutating it so the test is side-effect-free.
    fake_env = {
        **os.environ,
        "ANTHROPIC_API_KEY":    "sentinel-key",
        "ANTHROPIC_AUTH_TOKEN": "sentinel-token",
        "ANTHROPIC_BASE_URL":   "sentinel-url",
        "ANTHROPIC_MODEL":      "sentinel-model",
    }

    with mock.patch.dict(os.environ, fake_env, clear=True):
        # Re-import so _build_dispatch_env picks up the patched env.
        if "workers.cc_dispatcher" in sys.modules:
            del sys.modules["workers.cc_dispatcher"]
        from workers import cc_dispatcher as ccd
        env = ccd._build_dispatch_env("max")

    banned = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
              "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
              "ANTHROPIC_DEFAULT_OPUS_MODEL",
              "ANTHROPIC_DEFAULT_SONNET_MODEL",
              "ANTHROPIC_DEFAULT_HAIKU_MODEL",
              "CLAUDE_CODE_SUBAGENT_MODEL")
    for key in banned:
        _assert(f"max env does not contain {key}", key not in env,
                f"found value={env.get(key)!r}")

    # PATH / HOME / USER must survive so claude can run at all.
    for key in ("PATH", "HOME"):
        _assert(f"max env retains {key}", key in env)


# ─── Gate D — cost_limits.yaml per-tier schema ────────────────────────────
def check_cost_limits() -> None:
    print("\n[D] cost_limits.yaml — Phase 29 per-tier schema")
    from core.cc_dispatch import get_cost_limits

    limits = get_cost_limits()
    _assert("get_cost_limits() returns dict with 'per_tier' key",
            isinstance(limits.get("per_tier"), dict), str(limits))
    _assert("get_cost_limits() returns dict with 'per_day_usd' key",
            "per_day_usd" in limits, str(limits))

    pt = limits["per_tier"]
    _assert("max ceiling == None",  pt.get("max")   is None, str(pt.get("max")))
    _assert("flash ceiling == 0.10", pt.get("flash") == 0.10, str(pt.get("flash")))
    _assert("pro ceiling == 0.50",   pt.get("pro")   == 0.50, str(pt.get("pro")))
    _assert("api ceiling == 2.00",   pt.get("api")   == 2.00, str(pt.get("api")))
    _assert("local ceiling == None", pt.get("local") is None, str(pt.get("local")))
    _assert("quick ceiling == None", pt.get("quick") is None, str(pt.get("quick")))

    # quick_chat section must survive untouched (Phase 32 owns it).
    import yaml
    raw = yaml.safe_load((ROOT / "config" / "cost_limits.yaml").read_text(encoding="utf-8"))
    qc = raw.get("quick_chat", {})
    _assert("quick_chat section present",       bool(qc), str(qc))
    _assert("quick_chat.provider == 'deepseek'",
            qc.get("provider") == "deepseek", str(qc.get("provider")))
    _assert("quick_chat.deepseek_model present",
            bool(qc.get("deepseek_model")))
    _assert("quick_chat.daily_cost_max_usd present",
            "daily_cost_max_usd" in qc)


# ─── Gate E — complex build → tier=max ────────────────────────────────────
def check_complex_build_default() -> None:
    print("\n[E] Complex build prompt (no slash) routes to tier='max'")
    from workers.conversation_handler import (
        _BUILD_INTENT_RE, SIMPLE_BUILD_RE,
    )

    complex_prompts = [
        "build me a dashboard app",
        "build me a real-time chat component",
        "create me a REST API with auth",
        "make me a landing page",
    ]
    simple_prompts = [
        "make a quick hello world",
        "create a simple fizzbuzz",
        "make a basic counter",
    ]

    for prompt in complex_prompts:
        bm = _BUILD_INTENT_RE.match(prompt)
        is_simple = bool(SIMPLE_BUILD_RE.search(prompt))
        routes_to_max = (bm is not None) and (not is_simple)
        _assert(f"'{prompt[:45]}' → max (regex)", routes_to_max,
                f"bm={bm is not None}, simple={is_simple}")

    for prompt in simple_prompts:
        bm = _BUILD_INTENT_RE.match(prompt)
        is_simple = bool(SIMPLE_BUILD_RE.search(prompt))
        routes_to_local = (bm is not None) and is_simple
        _assert(f"'{prompt[:45]}' → local (not max)", routes_to_local,
                f"bm={bm is not None}, simple={is_simple}")


# ─── Gate F — Phase 32 sentinel check ─────────────────────────────────────
def check_phase32_sentinels() -> None:
    print("\n[F] Phase 32 constants unchanged (chat path not touched)")
    from workers.conversation_handler import (
        QUICK_CHAT_PROVIDER,
        CLASSIFIER_PROVIDER,
        QUICK_CHAT_DENIAL_FALLBACK_MODEL,
        QUICK_CHAT_MODEL,
    )

    _assert("QUICK_CHAT_PROVIDER == 'deepseek'",
            QUICK_CHAT_PROVIDER == "deepseek",
            repr(QUICK_CHAT_PROVIDER))
    _assert("CLASSIFIER_PROVIDER == 'deepseek'",
            CLASSIFIER_PROVIDER == "deepseek",
            repr(CLASSIFIER_PROVIDER))
    _assert("QUICK_CHAT_DENIAL_FALLBACK_MODEL is set (non-empty)",
            bool(QUICK_CHAT_DENIAL_FALLBACK_MODEL),
            repr(QUICK_CHAT_DENIAL_FALLBACK_MODEL))
    _assert("QUICK_CHAT_MODEL is set (non-empty)",
            bool(QUICK_CHAT_MODEL),
            repr(QUICK_CHAT_MODEL))


# ─── normalize_tier + is_paid_tier sanity ─────────────────────────────────
def check_tier_helpers() -> None:
    print("\n[+] Tier helper sanity (normalize_tier + is_paid_tier)")
    from core.cc_dispatch import normalize_tier, is_paid_tier

    _assert("normalize_tier('real') == 'api'", normalize_tier("real") == "api")
    _assert("normalize_tier('api') == 'api'",  normalize_tier("api")  == "api")
    _assert("normalize_tier('max') == 'max'",  normalize_tier("max")  == "max")
    _assert("normalize_tier('flash') == 'flash'", normalize_tier("flash") == "flash")

    _assert("is_paid_tier('flash') == True",  is_paid_tier("flash") is True)
    _assert("is_paid_tier('pro') == True",    is_paid_tier("pro")   is True)
    _assert("is_paid_tier('api') == True",    is_paid_tier("api")   is True)
    _assert("is_paid_tier('real') == True",   is_paid_tier("real")  is True)  # alias
    _assert("is_paid_tier('max') == False",   is_paid_tier("max")   is False)
    _assert("is_paid_tier('local') == False", is_paid_tier("local") is False)
    _assert("is_paid_tier('quick') == False", is_paid_tier("quick") is False)


def main() -> int:
    print("=" * 60)
    print("verify_phase29.py — Phase 29 gate checks")
    print("=" * 60)

    check_slash_parser()
    check_real_alias()
    check_max_env()
    check_cost_limits()
    check_complex_build_default()
    check_phase32_sentinels()
    check_tier_helpers()

    print("\n" + "=" * 60)
    if _failures:
        print(f"RESULT: FAIL — {len(_failures)} assertion(s) failed:")
        for f in _failures:
            print(f"  {FAIL} {f}")
        return 1
    else:
        print(f"RESULT: PASS — all assertions passed")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

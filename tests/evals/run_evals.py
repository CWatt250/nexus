#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Phase 39 — eval harness. Makes "fixed" mean fixed.

Loads fixture cases from tests/evals/cases/*.yaml and runs them against
the REAL routing / dispatch / scrubber code (imported directly — no
Telegram required). Cases that need live Ollama declare it via their
kind and are SKIPPED gracefully when Ollama or the named model is down.

Exit code: 0 = every non-skipped case passed, nonzero otherwise.
Every future phase must pass this suite before its ship gate counts.

Run:
    ~/AI_Agent/tests/evals/run_evals.sh            # full suite
    ~/AI_Agent/tests/evals/run_evals.sh --offline  # skip live cases
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

CASES_DIR = Path(__file__).resolve().parent / "cases"
OLLAMA_URL = "http://localhost:11434"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


# ── environment probes ──────────────────────────────────────────────────

def _ollama_models() -> set[str] | None:
    """Set of locally available model names, or None if Ollama is down."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        return {m.get("name", "") for m in data.get("models", [])}
    except Exception:
        return None


def _resolve_model(alias: str) -> str:
    from core import brain
    if alias == "brain":
        return brain.get_brain_model()
    if alias == "degraded":
        return brain.DEGRADED_MODEL
    return alias


def _model_available(models: set[str] | None, name: str) -> bool:
    if models is None:
        return False
    return any(m == name or m.split(":")[0] == name for m in models) or name in models


# ── shared fixtures ─────────────────────────────────────────────────────

class TempDispatchDirs:
    """Redirect core.cc_dispatch paths into a temp dir for the duration
    of a case so evals never touch the real inbox."""

    def __init__(self):
        from core import cc_dispatch
        self.ccd = cc_dispatch
        self.saved = {}

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nexus-eval-")
        base = Path(self.tmp.name)
        mapping = {
            "INBOX": base / "inbox",
            "PENDING": base / "inbox" / ".pending",
            "ARCHIVE": base / "archive",
            "LOGS": base / "logs",
            "RESULTS": base / "results",
            "METRICS": base / "metrics",
            "METRICS_LOG": base / "metrics" / "dispatches.jsonl",
        }
        for attr, val in mapping.items():
            self.saved[attr] = getattr(self.ccd, attr)
            setattr(self.ccd, attr, val)
        self.ccd.ensure_dirs()
        return self

    def __exit__(self, *exc):
        for attr, val in self.saved.items():
            setattr(self.ccd, attr, val)
        self.tmp.cleanup()

    def read_dispatch(self, dispatch_id: str):
        path = self.ccd.INBOX / f"{dispatch_id}.md"
        if not path.exists():
            path = self.ccd.PENDING / f"{dispatch_id}.md"
        assert path.exists(), f"no inbox/pending file for {dispatch_id}"
        meta, body = self.ccd.read_prompt(path)
        assert meta is not None, "unparseable dispatch meta"
        return meta, body


class WarningCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _sentence_count(text: str) -> int:
    import re
    parts = [p for p in re.split(r"[.!?\n]+", text) if p.strip()]
    return len(parts)


# ── case handlers — each returns (status, detail) ───────────────────────

def run_router_live(case, env) -> tuple[str, str]:
    if env["offline"]:
        return SKIP, "offline mode"
    from core import brain
    if not _model_available(env["models"], brain.get_brain_model()):
        return SKIP, f"brain model {brain.get_brain_model()} unavailable"
    from workers import llm_router
    decision = llm_router.route_llm(case["input"])
    if decision.get("router_error"):
        return FAIL, f"router errored: {decision['router_error']}"
    for key, want in (case.get("expect") or {}).items():
        got = decision.get(key)
        if got != want:
            return FAIL, f"expected {key}={want!r}, got {got!r} (decision={decision})"
    return PASS, str({k: decision[k] for k in ("route", "tier", "recon_mode")})


def run_dispatch_passthrough(case, env) -> tuple[str, str]:
    from workers import conversation_handler as ch
    prompt = case["input"]
    with TempDispatchDirs() as dirs:
        route = ch._enqueue_tiered_dispatch(prompt, tier=case.get("tier", "max"))
        if route.get("kind") != "dispatch" or not route["meta"].get("dispatch_id"):
            return FAIL, f"unexpected route result: {route}"
        meta, body = dirs.read_dispatch(route["meta"]["dispatch_id"])
    expect = case.get("expect") or {}
    if expect.get("body_byte_identical") and body.rstrip("\n") != prompt:
        return FAIL, f"body mutated:\n want: {prompt!r}\n got:  {body!r}"
    if "recon_mode" in expect and bool(meta.recon_mode) != bool(expect["recon_mode"]):
        return FAIL, f"recon_mode={meta.recon_mode}, expected {expect['recon_mode']}"
    for frag in case.get("must_not_contain_in_body") or []:
        if frag in body:
            return FAIL, f"forbidden fragment in body: {frag!r}"
    for frag in case.get("must_contain_in_label") or []:
        if frag not in meta.label:
            return FAIL, f"label missing {frag!r}: {meta.label!r}"
    for frag in case.get("must_not_contain_in_label") or []:
        if frag in meta.label:
            return FAIL, f"label contains forbidden {frag!r}: {meta.label!r}"
    return PASS, f"label={meta.label!r} recon={meta.recon_mode}"


def run_slash_parse(case, env) -> tuple[str, str]:
    from workers import conversation_handler as ch
    parsed = ch.parse_slash_command(case["input"])
    if parsed is None:
        return FAIL, "parse_slash_command returned None"
    for key, want in (case.get("expect") or {}).items():
        got = parsed.get(key)
        if got != want:
            return FAIL, f"expected {key}={want!r}, got {got!r}"
    return PASS, f"{parsed['command']} → tier={parsed.get('tier')} prompt verbatim"


def run_scrubber(case, env) -> tuple[str, str]:
    from workers import conversation_handler as ch
    out = ch._strip_think_final(case["input"])
    for frag in case.get("must_contain") or []:
        if frag not in out:
            return FAIL, f"missing {frag!r} in scrubbed output {out!r}"
    for frag in case.get("must_not_contain") or []:
        if frag in out:
            return FAIL, f"forbidden {frag!r} survived: {out!r}"
    return PASS, repr(out[:60])


def run_router_junk(case, env) -> tuple[str, str]:
    from core import brain
    from workers import llm_router

    junk = case["junk"]

    def fake_chat(*a, **kw):
        if junk == "__raise__":
            raise RuntimeError("simulated ollama failure")
        return junk

    cap = WarningCapture()
    logging.getLogger("nexus.llm_router").addHandler(cap)
    saved = brain.chat
    brain.chat = fake_chat
    try:
        decision = llm_router.route_llm(case["input"])
    finally:
        brain.chat = saved
        logging.getLogger("nexus.llm_router").removeHandler(cap)

    expect = case.get("expect") or {}
    if decision.get("route") != expect.get("route", "quick_chat"):
        return FAIL, f"expected safe fallback, got {decision}"
    if "router_error" not in decision:
        return FAIL, f"fallback not flagged with router_error: {decision}"
    if expect.get("logged_warning") and not cap.records:
        return FAIL, "no WARNING logged on router failure"
    return PASS, f"fell back safely ({decision['router_error'][:50]})"


def run_recon_keywords(case, env) -> tuple[str, str]:
    from workers import llm_router
    for msg in case.get("positives") or []:
        if not llm_router.is_recon(msg):
            return FAIL, f"missed recon: {msg!r}"
    for msg in case.get("negatives") or []:
        if llm_router.is_recon(msg):
            return FAIL, f"false-positive recon: {msg!r}"
    return PASS, f"{len(case.get('positives', []))}+{len(case.get('negatives', []))} keyword checks"


def run_quick_chat_live(case, env) -> tuple[str, str]:
    if env["offline"]:
        return SKIP, "offline mode"
    model = _resolve_model(case.get("model", "brain"))
    if not _model_available(env["models"], model):
        return SKIP, f"model {model} unavailable"
    from workers import conversation_handler as ch
    system_prompt = f"{ch.get_quick_chat_system_prompt()}\n\n{ch._datetime_context()}"
    reply = ch._ollama_quick_chat(model, case["input"], system_prompt)
    reply = ch._strip_think_final(reply)
    if not reply.strip():
        return FAIL, "empty reply"
    low = reply.lower()
    for frag in case.get("must_not_contain") or []:
        if frag.lower() in low:
            return FAIL, f"monologue leaked ({frag!r}): {reply!r}"
    if ch.looks_like_thinking_leak(reply):
        return FAIL, f"sentinel leak detected: {reply!r}"
    max_s = case.get("max_sentences")
    if max_s and _sentence_count(reply) > max_s:
        return FAIL, f"too long ({_sentence_count(reply)} sentences): {reply!r}"
    return PASS, repr(reply[:80])


def run_multiturn_live(case, env) -> tuple[str, str]:
    if env["offline"]:
        return SKIP, "offline mode"
    from core import brain
    model = brain.get_brain_model()
    if not _model_available(env["models"], model):
        return SKIP, f"brain model {model} unavailable"
    from core import telegram_chats as tcs
    from workers import conversation_handler as ch

    with tempfile.TemporaryDirectory(prefix="nexus-eval-chat-") as tmp:
        db = Path(tmp) / "chats.db"
        for turn in case.get("setup_turns") or []:
            tcs.write_turn(424242, turn["role"], turn["content"], db_path=db)
        saved = ch._load_quick_chat_memory_config
        ch._load_quick_chat_memory_config = lambda: {
            **ch._MEMORY_DEFAULTS, "db_path": str(db),
        }
        try:
            reply = ch.quick_chat(case["input"], chat_id=424242)
        finally:
            ch._load_quick_chat_memory_config = saved
    low = (reply or "").lower()
    for frag in case.get("must_contain") or []:
        if frag.lower() not in low:
            return FAIL, f"second turn did not see first: {reply!r}"
    return PASS, repr(reply[:80])


HANDLERS = {
    "router_live": run_router_live,
    "dispatch_passthrough": run_dispatch_passthrough,
    "slash_parse": run_slash_parse,
    "scrubber": run_scrubber,
    "router_junk": run_router_junk,
    "recon_keywords": run_recon_keywords,
    "quick_chat_live": run_quick_chat_live,
    "multiturn_live": run_multiturn_live,
}


def main() -> int:
    offline = "--offline" in sys.argv
    models = _ollama_models()
    if models is None and not offline:
        print("NOTE: Ollama unreachable — live cases will be skipped.")
    env = {"offline": offline, "models": models}

    cases = []
    for f in sorted(CASES_DIR.glob("*.yaml")):
        loaded = yaml.safe_load(f.read_text(encoding="utf-8")) or []
        for c in loaded:
            c["_file"] = f.name
            cases.append(c)

    print(f"nexus evals — {len(cases)} cases from {CASES_DIR}")
    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    failures = []
    for case in cases:
        handler = HANDLERS.get(case.get("kind", ""))
        name = case.get("name", "(unnamed)")
        if handler is None:
            status, detail = FAIL, f"unknown kind {case.get('kind')!r}"
        else:
            try:
                status, detail = handler(case, env)
            except Exception as exc:
                status, detail = FAIL, f"handler crashed: {type(exc).__name__}: {exc}"
        counts[status] += 1
        mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
        print(f"  {mark} [{status}] {name}  — {detail[:140]}")
        if status == FAIL:
            failures.append(name)

    print(f"\n{counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIP]} skipped")
    if failures:
        print("FAILED:")
        for n in failures:
            print(f"  - {n}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

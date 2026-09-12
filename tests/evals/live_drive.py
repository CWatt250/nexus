#!/usr/bin/env python3
"""Live drive — exercises Nexus the way Colton does from Telegram, in-process.

Runs real messages through `conversation_handler.route_message` (the same
function the Telegram listener calls), plus the desktop / vision / voice
stacks, against the live Ollama. Prints a scorecard. Not part of the
nightly gate (it takes ~4 min and hits the web); run by hand:

    ./venv/bin/python3 tests/evals/live_drive.py [--only chat|lookup|desktop|voice|route]

Messages that would ENQUEUE work (task/dispatch) are route-checked only —
never executed — so nothing lands in the real Telegram chat.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

FORBIDDEN = re.compile(
    r"\b(?:certainly|of course|i'?d be happy|great question|let me know if|"
    r"anything else\?|how can i help|just checking in)\b", re.I)
THINK_LEAK = re.compile(
    r"<think>|\bokay, the user\b|\bthe user (?:wants|asked|is asking)\b|"
    r"\blet me think\b|\bfirst, i need\b|\bbest reply\b|\bself-correction\b", re.I)
FALSE_PROMISE = re.compile(
    r"queued as a (?:full )?task|ping you when|i'?ll (?:go )?(?:check|look|dig)|"
    r"let me (?:go )?(?:check|look|dig)|one sec\b", re.I)
DENIAL = re.compile(r"\bi can'?t\b|\bno mic\b|text-in, text-out|\bcannot\b", re.I)

SYNTH_CHAT = 424242 + int(time.time()) % 100000  # fresh per run, never Colton's chat id


def sentences(t: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", (t or "").strip()) if s])


class Case:
    def __init__(self, cat, name, msg=None, *, expect_kind=None, max_s=None,
                 must=(), must_not=(), max_sentences=None, route_only=False,
                 expect_route=None, expect_tier=None, fn=None, no_denial=False):
        self.cat, self.name, self.msg = cat, name, msg
        self.expect_kind, self.max_s = expect_kind, max_s
        self.must, self.must_not, self.max_sentences = must, must_not, max_sentences
        self.route_only, self.expect_route, self.expect_tier = route_only, expect_route, expect_tier
        self.fn, self.no_denial = fn, no_denial


def today_tokens():
    now = datetime.now()
    return [now.strftime("%B %-d"), now.strftime("%Y-%m-%d"), now.strftime("%A"),
            now.strftime("%b %-d"), now.strftime("%-m/%-d")]


CASES = [
    # ── chat / voice ──
    Case("chat", "greeting", "hey what's up", expect_kind="chat", max_s=4, max_sentences=3),
    Case("chat", "one-word", "yo", expect_kind="chat", max_s=4, max_sentences=2),
    Case("chat", "thanks", "thanks man", expect_kind="chat", max_s=4, max_sentences=2),
    Case("chat", "opinion-drizzle", "what do you think about switching BidWatt to Drizzle?",
         expect_kind="chat", max_s=10, max_sentences=7),
    Case("chat", "statement-not-build", "we should be using ornith 1.5", expect_kind="chat", max_s=8),
    Case("chat", "capabilities", "who are you and what can you actually do?", expect_kind="chat",
         max_s=10, must=[r"voice|desktop|screenshot|search|browser"], no_denial=True),
    Case("chat", "voice-capability", "do you have voice capability and can talk to me?",
         expect_kind="chat", max_s=8, must=[r"voice"], must_not=[r"no mic", r"text-in, text-out"]),
    Case("chat", "which-model", "what ai model are you running right now?", max_s=10,
         must=[r"1\.5"], must_not=[r"1\.0\b", r"gpt-oss", r"rocm"]),
    Case("chat", "date", "what's today's date?", max_s=8, must=[r"|".join(map(re.escape, today_tokens()))]),
    Case("chat", "math", "what's 17*23? just the number", max_s=6, must=[r"391"]),
    Case("chat", "explain-short", "explain what a REST API is in 2 sentences", expect_kind="chat",
         max_s=10, max_sentences=3),
    # ── lookups (one tool) ──
    Case("lookup", "price+link", "how much does a Nvidia DGX Spark cost? give me a link to buy it",
         expect_kind="query_tool", max_s=15, must=[r"\$\s?\d", r"https?://"]),
    Case("lookup", "weather", "what's the weather in Pasco right now?", expect_kind="query_tool",
         max_s=15, must=[r"°|\bF\b|degrees|\d{2}"]),
    Case("lookup", "latest-version", "what's the latest ollama version?", expect_kind="query_tool",
         max_s=15, must=[r"\d+\.\d+"]),
    Case("lookup", "search-explicit", "search for the best mechanical insulation estimating software",
         expect_kind="query_tool", max_s=15),
    Case("lookup", "mimic-guard", "how much does an RTX 5090 cost right now?", expect_kind="query_tool",
         max_s=15, must=[r"\$\s?\d"], must_not=[r"queued as", r"ping you"]),
    # ── wiki / status / health ──
    Case("wiki", "what-is-bidwatt", "what is BidWatt?", max_s=15, must=[r"bid|estimat|next\.?js|supabase"]),
    Case("status", "queue-status", "queue status", expect_kind="status", max_s=5),
    Case("status", "health", "are you healthy? how's memory and GPU?", max_s=15, must=[r"GB|%|VRAM|RAM"]),
    # ── routing-only (never executed) ──
    Case("route", "build→dispatch/local", "build a snake game in one html file", route_only=True,
         expect_route="dispatch", expect_tier="local"),
    Case("route", "research→task", "research the three best open-source vision models for GUI grounding and give me a table",
         route_only=True, expect_route="task"),
    Case("route", "fix→task|dispatch", "fix the typo in the README", route_only=True, expect_route=("dispatch", "task")),
    Case("route", "opinion→chat", "I think the router is over-escalating lately", route_only=True,
         expect_route="quick_chat"),
    Case("route", "price→lite", "what does a Mac Studio M4 Ultra cost?", route_only=True, expect_route="lite_agent"),
]


def _desktop_cases():
    def screenshot_and_describe():
        from tools import desktop, vision_tool
        t = time.time(); full, small = desktop.screenshot(); t_shot = time.time() - t
        from PIL import Image, ImageStat
        luma = ImageStat.Stat(Image.open(small).convert("L")).mean[0]
        t = time.time(); desc = vision_tool.describe_image.invoke({"path": small}) \
            if hasattr(vision_tool.describe_image, "invoke") else vision_tool.describe_image(small)
        t_desc = time.time() - t
        ok = bool(luma > 5 and t_shot < 1.0 and t_desc < 12 and re.search(r"chrome|browser|tab|web|page|site|news", str(desc), re.I))
        return ok, f"shot {t_shot:.2f}s luma {luma:.0f} · describe {t_desc:.1f}s: {str(desc)[:90]!r}"

    def open_url_title():
        from tools import desktop
        t = time.time(); desktop.open_url("https://news.ycombinator.com"); time.sleep(2.5)
        title = desktop.active_window_title(); dt = time.time() - t
        return ("hacker news" in title.lower()), f"{dt:.1f}s title={title!r}"

    def desktop_task():
        from tools import desktop_agent
        t = time.time()
        r = desktop_agent.run_desktop_task("open https://example.com and tell me the page heading", max_steps=4)
        dt = time.time() - t
        summ = str(r.get("summary") or r)
        return ("example domain" in summ.lower()) and dt < 90, f"{dt:.1f}s steps={r.get('steps')} · {summ[:100]!r}"

    return [Case("desktop", "screenshot+describe", fn=screenshot_and_describe),
            Case("desktop", "open-url-title", fn=open_url_title),
            Case("desktop", "agent-loop", fn=desktop_task)]


def _voice_cases():
    def roundtrip():
        from tools import tts_tool, whisper_tool
        out = ROOT / "output" / "live_drive_voice.ogg"; out.parent.mkdir(exist_ok=True)
        t = time.time(); tts_tool.tts_to_ogg("What's the weather in Pasco today?", str(out)); t_tts = time.time() - t
        t = time.time(); text = whisper_tool.transcribe_file(str(out), model_size="small.en"); t_stt = time.time() - t
        ok = "weather" in text.lower() and "pasco" in text.lower() and t_stt < 5
        return ok, f"tts {t_tts:.1f}s · stt {t_stt:.1f}s → {text!r}"
    return [Case("voice", "tts→whisper", fn=roundtrip)]


def run(case: Case) -> tuple[bool, str, float]:
    t0 = time.time()
    if case.fn:
        try:
            ok, note = case.fn()
        except Exception as exc:
            return False, f"EXC {type(exc).__name__}: {exc}", time.time() - t0
        return ok, note, time.time() - t0
    from workers import conversation_handler as h, llm_router as r
    if case.route_only:
        d = r.route_llm(case.msg); dt = time.time() - t0
        exp = case.expect_route if isinstance(case.expect_route, tuple) else (case.expect_route,)
        ok = d.get("route") in exp and (case.expect_tier is None or d.get("tier") == case.expect_tier)
        return ok, f"route={d.get('route')}/{d.get('tier')}", dt
    res = h.route_message(case.msg, SYNTH_CHAT)
    dt = time.time() - t0
    reply = res.get("reply", "") or ""
    kind = res.get("kind")
    probs = []
    if case.expect_kind and kind != case.expect_kind:
        probs.append(f"kind={kind}≠{case.expect_kind}")
    if case.max_s and dt > case.max_s:
        probs.append(f"slow {dt:.1f}s>{case.max_s}s")
    for pat in case.must:
        if not re.search(pat, reply, re.I):
            probs.append(f"missing /{pat[:30]}/")
    for pat in case.must_not:
        if re.search(pat, reply, re.I):
            probs.append(f"has /{pat}/")
    if case.max_sentences and sentences(reply) > case.max_sentences:
        probs.append(f"{sentences(reply)} sentences>{case.max_sentences}")
    if FORBIDDEN.search(reply):
        probs.append("forbidden phrase")
    if THINK_LEAK.search(reply):
        probs.append("THINK LEAK")
    if kind == "chat" and FALSE_PROMISE.search(reply) and not res.get("meta", {}).get("task_id"):
        probs.append("FALSE PROMISE")
    if case.no_denial and DENIAL.search(reply):
        probs.append("denial")
    note = ("; ".join(probs) + " · " if probs else "") + f"[{kind}] {reply[:110]!r}"
    return not probs, note, dt


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only", default=None); a = ap.parse_args()
    cases = CASES + _desktop_cases() + _voice_cases()
    if a.only:
        cases = [c for c in cases if c.cat == a.only]
    # NEVER enqueue real work from the harness (the live worker would run it
    # and message the owner). Record instead.
    from core import task_queue
    enqueued: list[str] = []
    task_queue.enqueue = lambda text, *a, **k: (enqueued.append(text), "livedrive0000")[1]
    # warm the prompt cache so the first timed case isn't a cold prefix
    try:
        from workers import conversation_handler as h
        h.quick_chat("hi", chat_id=None)
    except Exception:
        pass
    rows, passed = [], 0
    for c in cases:
        if c.name == "mimic-guard":  # seed the trap right before this case only
            try:
                from core import telegram_chats as tc
                tc.init(); tc.write_turn(SYNTH_CHAT, "user", "research the best GPUs")
                tc.write_turn(SYNTH_CHAT, "assistant", "On it — queued as a full task. I'll ping you when it lands.")
            except Exception as e:
                print("seed history failed:", e)
        ok, note, dt = run(c); passed += ok
        rows.append((c.cat, c.name, ok, dt, note))
        print(f"{'✓' if ok else '✗'} {c.cat:8} {c.name:22} {dt:5.1f}s  {note}", flush=True)
    print(f"\n{passed}/{len(cases)} passed · would-have-enqueued: {enqueued}")
    by = {}
    for cat, _, ok, dt, _ in rows:
        b = by.setdefault(cat, [0, 0, 0.0]); b[0] += ok; b[1] += 1; b[2] += dt
    for cat, (p, n, t) in by.items():
        print(f"  {cat:8} {p}/{n}  avg {t/n:.1f}s")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())

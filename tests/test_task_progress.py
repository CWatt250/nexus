"""Live task bubble: listener → worker handoff, rolling window, narration
strip, finish/fail over a mocked httpx. No network, no Telegram."""
from __future__ import annotations

import asyncio
import json

import pytest

from tools import telegram_progress as tp
from workers import task_notifier as tn
from workers import task_progress as tpr

SAMPLE = """\
The PPC results are noise (SearXNG matched "qwen" to Japanese legal code), but the signal I need is clear and consistent across multiple independent sources: UI-TARS-2, Qwen2.5-VL 72B, and Qwen3-VL dominate GUI grounding leaderboards, with RegionFocus confirming Qwen2.5-VL-72B at SOTA on ScreenSpot-Pro. I have enough to build the table.

Here's where things stand as of today (2026-09-12):

| Model | Type | Best For |
|---|---|---|
| **Qwen3-VL** | VLM | overall |
| **UI-TARS-2** | GUI VLM | grounding |

### Quick take
- **best pure GUI grounding** → **UI-TARS-2**.
- **one open model for everything** → **Qwen3-VL 8B**.

Note: these leaderboards move fast, so check llm-stats.com for live numbers before committing to one. Want me to pull the current leaderboard values or benchmark one of these against your own screenshots?
"""


# ── mocked Telegram HTTP API ─────────────────────────────────────────
class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p


class FakeClient:
    calls: list[tuple[str, dict]] = []
    fail_edit_with: str | None = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, data=None, files=None):
        method = url.rsplit("/", 1)[-1]
        payload = dict(json or data or {})
        if files:
            payload["files"] = {k: v[0] for k, v in files.items()}
        FakeClient.calls.append((method, payload))
        if method == "editMessageText" and FakeClient.fail_edit_with:
            return FakeResp({"ok": False, "description": FakeClient.fail_edit_with}, 400)
        return FakeResp({"ok": True, "result": {"message_id": 7}})


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setattr(tpr.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(tpr, "PROGRESS_DIR", tmp_path / "task_progress")
    monkeypatch.setattr(tp, "EDIT_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(tpr, "EDIT_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(tpr, "CHUNK_SEND_DELAY_S", 0.0)
    monkeypatch.setattr(tpr, "HANDOFF_WAIT_S", 0.0)
    FakeClient.calls = []
    FakeClient.fail_edit_with = None
    yield


def _edits():
    return [p["text"] for m, p in FakeClient.calls if m == "editMessageText"]


# ── handoff JSON ─────────────────────────────────────────────────────
def test_handoff_roundtrip():
    p = tpr.write_handoff("abc123", chat_id=42, message_id=1001, title="🧠 on it — researching…")
    data = json.loads(p.read_text())
    assert data["chat_id"] == 42 and data["message_id"] == 1001 and "created_at" in data
    prog = asyncio.run(tpr.TaskProgress.for_task("abc123"))
    assert prog is not None and prog.chat_id == 42 and prog.message_id == 1001
    assert prog.title == "🧠 on it — researching…"
    tpr.clear_handoff("abc123")
    assert asyncio.run(tpr.TaskProgress.for_task("abc123", fresh=False)) is None


def test_for_task_without_json_falls_back_to_none():
    assert asyncio.run(tpr.TaskProgress.for_task("nope")) is None


def test_derive_title_from_verb():
    assert tpr.derive_title("Research the three best vision models") == "🧠 on it — researching…"
    assert tpr.derive_title("build me a flappy bird clone") == "🧠 on it — building…"
    assert tpr.derive_title("hmm, thoughts?") == "🧠 on it — working on it…"


# ── listener-side bubble.handoff ─────────────────────────────────────
class _Bot:
    def __init__(self):
        self.edits = []

    async def edit_message_text(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))


class _Upd:
    class _Chat:
        id = 42

        async def send_action(self, a):
            pass

    class _Msg:
        chat = None

        async def reply_text(self, text):
            class M:
                message_id = 1001
            return M()

    def __init__(self, bot):
        self._bot = bot
        self.effective_chat = self._Chat()
        self.message = self._Msg()
        self.message.chat = self.effective_chat

    def get_bot(self):
        return self._bot


def test_bubble_handoff_edits_and_releases():
    async def run():
        bot = _Bot()
        b = await tp.ProgressBubble(_Upd(bot)).start()
        h = await b.handoff("🧠 on it — researching…")
        assert h == {"chat_id": 42, "message_id": 1001}
        assert bot.edits[-1][2] == "🧠 on it — researching…"
        await asyncio.sleep(0)
        assert b._done and b._typing.cancelled()   # typing loop released to the worker
    asyncio.run(run())


# ── rolling window ───────────────────────────────────────────────────
def test_window_rolls_last_six_with_header_and_checks():
    w = tpr.ProgressWindow("🧠 on it — researching…")
    for i in range(8):
        w.add(f"🔧 tool{i} q=x")
    w.mark_done()
    out = w.render(42)
    lines = out.splitlines()
    assert lines[0] == "🧠 on it — researching…" and lines[1] == "⏱ 42.0s"
    assert len(lines) == 8 and lines[2] == "🔧 tool2 q=x" and lines[-1] == "🔧 tool7 q=x ✓"
    assert w.render(70, idle=True).splitlines()[0] == "🧠 still thinking… 1m10s"


def test_helpers_truncate():
    assert len(tpr.arg_preview({"query": "x" * 100})) <= 60
    assert tpr.arg_preview({"a": 1, "b": "two"}) == "a=1, b=two"
    s = tpr.first_sentence("The user wants a table. I should search first.", 140)
    assert s == "The user wants a table."
    assert len(tpr.first_sentence("word " * 60)) <= 140


# ── narration strip ──────────────────────────────────────────────────
def test_strip_narration_on_live_sample():
    out = tn.strip_narration(SAMPLE)
    assert out.startswith("Here's where things stand as of today (2026-09-12):")
    assert "PPC results are noise" not in out and "I have enough" not in out
    assert "| Model |" in out and "### Quick take" in out
    assert "Want me to" not in out
    assert out.rstrip().endswith("before committing to one.")


def test_strip_narration_keeps_single_paragraph_and_plain_answers():
    single = "I need your Vercel token to deploy — paste it and I'll ship."
    assert tn.strip_narration(single) == single
    plain = "Pasco: 91°F, clear. Saturday 2026-09-12."
    assert tn.strip_narration(plain) == plain


def test_strip_narration_glued_to_table():
    glued = "The results are noisy but I have enough to build the table.\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert tn.strip_narration(glued).startswith("| a | b |")


def test_trailing_offer_only_paragraph_is_dropped():
    out = tn.strip_narration("Done — 3 files changed.\n\nWant me to open a PR?")
    assert out == "Done — 3 files changed."


# ── finish / fail over mocked httpx ──────────────────────────────────
def _progress():
    return tpr.TaskProgress("t1", chat_id=42, message_id=1001, title="🧠 on it — researching…")


def test_stage_edits_latest_and_swallows_not_modified():
    async def run():
        p = _progress()
        await p.stage("🔧 web_search q=1")
        FakeClient.fail_edit_with = "Bad Request: message is not modified"
        await p.stage("🔧 web_search q=1 ✓")
        assert p._last_text == "🔧 web_search q=1 ✓"
    asyncio.run(run())
    assert _edits() == ["🔧 web_search q=1", "🔧 web_search q=1 ✓"]


def test_finish_edits_bubble_strips_narration_and_sends_photo(tmp_path, monkeypatch):
    from tools import telegram_render as tr
    monkeypatch.setattr(tpr, "render_tables",
                        lambda text: tr.prepare_with_captions(text, out_dir=tmp_path))
    tpr.write_handoff("t1", chat_id=42, message_id=1001, title="x")
    asyncio.run(_progress().finish(SAMPLE, elapsed_s=82))
    edits = _edits()
    assert edits[0].startswith("✅ done in 1m22s\n\nHere's where things stand")
    assert "PPC results" not in edits[0] and "Want me to" not in edits[0]
    assert "📊 table below" in edits[0] and "| Model |" not in edits[0]
    photos = [p for m, p in FakeClient.calls if m == "sendPhoto"]
    assert len(photos) == 1 and photos[0]["chat_id"] == 42
    assert photos[0]["caption"] == "Model · Type · Best For"
    assert not tpr.handoff_path("t1").exists()


def test_finish_chunks_long_output():
    asyncio.run(_progress().finish("word " * 2500, elapsed_s=3))
    sends = [p for m, p in FakeClient.calls if m == "sendMessage"]
    assert len(_edits()) == 1 and len(sends) >= 2
    assert _edits()[0].startswith("✅ done in 3.0s")


def test_finish_falls_back_to_send_when_edit_fails():
    FakeClient.fail_edit_with = "Bad Request: message to edit not found"
    asyncio.run(_progress().finish("hi", elapsed_s=1))
    sends = [p for m, p in FakeClient.calls if m == "sendMessage"]
    assert sends and sends[0]["text"].startswith("✅ done in 1.0s")


def test_fail_is_one_line_no_exception_text():
    asyncio.run(_progress().fail("❌ that one didn't make it — it ran out of time after 5m00s."))
    assert _edits() == ["❌ that one didn't make it — it ran out of time after 5m00s."]


# ── tracker → window → bubble ────────────────────────────────────────
def test_tracker_pushes_tool_lines_and_reasoning():
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult
    from workers import task_worker as tw

    async def run():
        p = _progress()
        tracker, state = tw._make_tool_tracker(p, think_on=True, started=0.0)
        rid = "r1"
        await tracker.on_chat_model_start({}, [], run_id=rid)
        msg = AIMessage(content="", additional_kwargs={"reasoning_content": "The user wants weather. I'll call the tool."})
        gen = ChatGeneration(message=msg, generation_info={"eval_count": 55})
        await tracker.on_llm_end(LLMResult(generations=[[gen]]), run_id=rid)
        await tracker.on_tool_start({"name": "get_weather"}, "Pasco, WA", inputs={"city": "Pasco, WA"})
        await tracker.on_tool_end("91F")
        await asyncio.sleep(0.05)
        assert state == [1, "get_weather"]
        assert tracker.reasoning == ["The user wants weather."]
        assert tracker.step_stats[0]["eval_count"] == 55
        lines = tracker.window.render(1).splitlines()
        assert lines[2] == "💭 The user wants weather." and lines[3] == "🔧 get_weather city=Pasco, WA ✓"
        assert tracker.think_trail().startswith("💭\n> The user wants weather.")
    asyncio.run(run())
    assert _edits() and _edits()[-1].endswith("🔧 get_weather city=Pasco, WA ✓")


def test_tracker_hides_reasoning_when_think_off():
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult
    from workers import task_worker as tw

    async def run():
        tracker, _ = tw._make_tool_tracker(_progress(), think_on=False, started=0.0)
        msg = AIMessage(content="x", additional_kwargs={"reasoning_content": "Secret plan."})
        await tracker.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]), run_id="r")
        assert "💭" not in tracker.window.render(1) and tracker.reasoning == ["Secret plan."]
    asyncio.run(run())


def test_notify_done_fallback_strips_and_trails_separately(monkeypatch):
    sent: list[str] = []

    async def fake_send(text):
        sent.append(text)
    monkeypatch.setattr(tn, "_send", fake_send)
    monkeypatch.setattr(tn, "_think_on", lambda: True)
    monkeypatch.setattr(tn, "_work_trail", lambda tid: "> why: you asked")
    monkeypatch.setattr(tn, "_render_tables", lambda t: (t, []))
    asyncio.run(tn.notify_done("t9", SAMPLE, elapsed_s=82))
    assert sent[0].startswith("✅ done in 1m22s\n\nHere's where things stand")
    assert "💭" not in sent[0] and sent[-1] == "💭\n> why: you asked"

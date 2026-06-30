#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Telegram Listener — receives commands from Telegram and routes to Nexus API."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# Setup
load_dotenv(Path.home() / "AI_Agent" / ".env")
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
NEXUS_API_URL = "http://localhost:11435"

# Authorized chat IDs (only respond to these)
AUTHORIZED_CHATS = set()
if TELEGRAM_CHAT_ID:
    AUTHORIZED_CHATS.add(int(TELEGRAM_CHAT_ID))


def is_authorized(update: Update) -> bool:
    """Check if the message is from an authorized chat."""
    if not AUTHORIZED_CHATS:
        return True  # No restriction if not configured
    return update.effective_chat.id in AUTHORIZED_CHATS


# ── Phase 41 — chunked send + live-draft streaming ──────────────────────
import threading  # noqa: E402

CHUNK_SEND_DELAY_S = 0.4   # spacing between sequential message chunks
DRAFT_THROTTLE_S = 1.0     # min seconds between sendMessageDraft pushes
DRAFT_MAX_CHARS = 4000     # cap on draft preview text


async def _reply_chunked(update: Update, text: str) -> None:
    """Send `text` via update.message.reply_text, split into ≤4096-char
    messages (the Telegram hard limit) using the shared chunker. Long
    replies arrive complete across multiple messages instead of being
    truncated. A small delay between sends stays clear of rate limits."""
    from core.telegram_chunk import chunk_text  # noqa: PLC0415
    chunks = chunk_text(text) or ["(empty reply)"]
    for i, chunk in enumerate(chunks):
        await update.message.reply_text(chunk)
        if i < len(chunks) - 1:
            await asyncio.sleep(CHUNK_SEND_DELAY_S)


async def _stream_quick_chat_reply(update: Update, message: str,
                                   chat_id: int) -> str:
    """Live-draft a quick_chat reply (Bot API 9.5 sendMessageDraft).

    As Ollama tokens arrive, push the growing partial text to an ephemeral
    "drafting" bubble (throttled). On completion, finalize with a real,
    chunked sendMessage — the draft is NOT persisted and vanishes after
    ~30s, so finalisation is mandatory. Returns the finalized reply text.

    Raises on a generation error so the caller falls back to the blocking
    path (reply never dropped). sendMessageDraft itself is best-effort:
    any draft error (library/account API-version gap) degrades silently to
    typing-indicator + final message."""
    from workers import conversation_handler as _ch  # noqa: PLC0415
    bot = update.get_bot()
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def _producer() -> None:
        try:
            for ev in _ch.quick_chat_stream(message, chat_id):
                loop.call_soon_threadsafe(q.put_nowait, ev)
            loop.call_soon_threadsafe(q.put_nowait, {"_done": True})
        except Exception as exc:  # noqa: BLE001 — surfaced to caller below
            loop.call_soon_threadsafe(q.put_nowait, {"_error": exc})

    threading.Thread(target=_producer, daemon=True).start()

    draft_id = update.message.message_id  # stable id for this draft
    final_text: str | None = None
    err: Exception | None = None
    last_push = 0.0
    draft_ok = True

    while True:
        ev = await q.get()
        if "_error" in ev:
            err = ev["_error"]
            break
        if "_done" in ev:
            break
        if "final" in ev:
            final_text = ev["final"]
            continue
        partial = ev.get("partial", "")
        if draft_ok and partial.strip():
            now = loop.time()
            if now - last_push >= DRAFT_THROTTLE_S:
                last_push = now
                try:
                    await bot.send_message_draft(
                        chat_id=chat_id, draft_id=draft_id,
                        text=partial[:DRAFT_MAX_CHARS],
                    )
                except Exception as exc:  # noqa: BLE001
                    draft_ok = False  # stop drafting, keep accumulating
                    logger.info("sendMessageDraft unsupported/failed (%s) — "
                                "degrading to final message only", exc)

    if err is not None:
        raise err
    if not final_text:
        final_text = "(handler returned no text — try again)"
    # Draft is ephemeral — the real message is what persists.
    await _reply_chunked(update, final_text)
    return final_text


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "Hey Colton! Sparky here.\n\n"
        "Send me any message and I'll route it to Nexus.\n\n"
        "Commands:\n"
        "/status - Check Nexus status\n"
        "/tasks - List current tasks\n"
        "/stop - Stop current task\n"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    if not is_authorized(update):
        return
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{NEXUS_API_URL}/health", timeout=10)
            if response.status_code == 200:
                data = response.json()
                await update.message.reply_text(f"Nexus Status: {data.get('status', 'unknown')}")
            else:
                await update.message.reply_text(f"Nexus returned {response.status_code}")
    except Exception as e:
        await update.message.reply_text(f"Could not reach Nexus API: {e}")


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /tasks command — read queue directly (no API hop)."""
    if not is_authorized(update):
        return
    try:
        from core import task_queue
        rows = task_queue.list_tasks(limit=10)
        if not rows:
            await update.message.reply_text("Queue is empty.")
            return
        lines = []
        for r in rows:
            preview = (r.get("input") or "")[:60]
            lines.append(f"- {r['task_id']}  [{r['status']}]  {preview}")
        await update.message.reply_text("Recent tasks:\n" + "\n".join(lines))
    except Exception as e:
        logger.exception("tasks_command failed: %s", e)
        await update.message.reply_text(f"Error: {type(e).__name__}: {e}")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop command."""
    if not is_authorized(update):
        return
    await update.message.reply_text("Stop command received. (Not yet implemented)")


async def _content_create_in_background(update: Update, topic: str, duration: int) -> None:
    """Phase 21 — long-running content pipeline. Runs the orchestrator
    in a worker thread so the listener event loop stays responsive,
    then sends the final mp4 back to the same chat. Best-effort —
    exceptions surface as a Telegram error reply."""
    chat_id = update.effective_chat.id
    try:
        from tools import content_create as _cc  # noqa: PLC0415
        info = await asyncio.to_thread(
            _cc.content_create_core, topic, duration, "energetic",
            True,  # prefer_real_visuals
            True,  # add_music
            ("9x16", "1x1", "16x9"),
        )
        final_path = info["final_video_path"]
        bot = update.get_bot()
        variants = info.get("aspect_variants", {})
        variant_line = (
            "variants: " + ", ".join(sorted(variants.keys())) + "\n"
            if len(variants) > 1 else ""
        )
        music_line = (
            f"music: {Path(info['music_track']).stem}\n"
            if info.get("music_used") else "music: (none)\n"
        )
        try:
            with open(final_path, "rb") as fh:
                await bot.send_video(
                    chat_id=chat_id,
                    video=fh,
                    caption=(
                        f"🎬 {Path(final_path).name}\n"
                        f"scenes: {info['scene_clips_built']} | "
                        f"actual: {info['duration_actual_seconds']:.1f}s\n"
                        f"{music_line}"
                        f"{variant_line}"
                        f"backend: {info['script_backend']} | "
                        f"cost: ${info['cost_usd']:.4f}"
                    ),
                )
        except Exception as send_exc:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ Video built at {final_path} but send failed: "
                    f"{type(send_exc).__name__}: {send_exc}"
                ),
            )
    except Exception as exc:
        try:
            await update.get_bot().send_message(
                chat_id=chat_id,
                text=f"⚠️ create-video failed: {type(exc).__name__}: {exc}",
            )
        except Exception:
            logger.exception("background video send error")


async def _build_in_background(update: Update, description: str, target_path: str, tech: str,
                               *, model: str | None = None) -> None:
    """Phase 27 + 28 — long-running local build. Mirrors
    _content_create_in_background: runs in a worker thread so the
    listener event loop stays responsive, then sends the result back
    to the same chat AND auto-attaches the generated file (Phase 28
    fix for the missing-file bug). `model` overrides the default
    qwen3.6 → /local + SIMPLE_BUILD pass qwen3-coder:30b for code."""
    chat_id = update.effective_chat.id
    bot = update.get_bot()
    try:
        from tools import local_builder  # noqa: PLC0415
        result = await asyncio.to_thread(
            local_builder.build_thing_core, description, target_path, tech, model,
        )
    except Exception as exc:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ build failed: {type(exc).__name__}: {exc}",
            )
        except Exception:
            logger.exception("background build error-send failed")
        return

    notes_line = "" if result.notes == "ok" else f"\n  ⚠ {result.notes}"
    msg = (
        f"🛠️ built {result.path}\n"
        f"  tech    : {result.tech_stack}\n"
        f"  size    : {result.bytes_written} bytes / {result.lines} lines\n"
        f"  wall    : {result.wall_seconds}s on {result.backend}{notes_line}"
    )
    try:
        await bot.send_message(chat_id=chat_id, text=msg)
    except Exception:
        logger.exception("background build success-send failed")
    # Phase 28 — auto-attach the generated file so the user can play
    # with it without ssh-ing into the box. Best-effort: skipped on
    # files >10MB, errors swallowed.
    try:
        from pathlib import Path as _Path  # noqa: PLC0415
        artifact = _Path(result.path).expanduser()
        if artifact.exists() and artifact.is_file() and artifact.stat().st_size <= 10 * 1024 * 1024:
            with open(artifact, "rb") as fh:
                await bot.send_document(
                    chat_id=chat_id, document=fh,
                    caption=f"{artifact.name} — {tech} build via {result.backend}",
                )
    except Exception:
        logger.exception("background build attach failed")
    # Phase 28 — visual verify HTML builds and warn if the page looks
    # broken. Heavyweight (Playwright + qwen2.5vl), but only fires for
    # html outputs and never blocks the success message above.
    if (result.tech_stack or "").lower() in ("html", "htm"):
        try:
            from tools import visual_verify  # noqa: PLC0415
            verdict = await asyncio.to_thread(
                visual_verify.verify_html_artifact_safe, result.path,
            )
            if verdict.get("needs_review"):
                await bot.send_message(
                    chat_id=chat_id,
                    text=("⚠️ visual verify flagged this build for review — "
                          + verdict.get("notes", "")[:240]),
                )
            shot = verdict.get("screenshot_path") or ""
            if shot:
                from pathlib import Path as _Path  # noqa: PLC0415
                shot_p = _Path(shot)
                if shot_p.exists():
                    with open(shot_p, "rb") as fh:
                        await bot.send_photo(
                            chat_id=chat_id, photo=fh,
                            caption="visual verify screenshot",
                        )
        except Exception:
            logger.exception("background build visual verify failed")


# Phase 27 build-arg helpers — still used by the /local slash handler.
# (Phase 39 removed the listener's build-intent regex interception;
# no-slash "build me X" messages now flow to route_message → LLM router.)
import re as _re_p27  # noqa: E402  — local alias to avoid clashing with module-level re imports
_TG_BUILD_AT_PATH_RE = _re_p27.compile(
    r"^(.+?)\s+at\s+(\S+)\s*$", _re_p27.IGNORECASE | _re_p27.DOTALL,
)
_TG_BUILD_TECH_RE = _re_p27.compile(
    r"\b(?:in|as|using)\s+(html|python|markdown|md|shell|bash)\b",
    _re_p27.IGNORECASE,
)


def _extract_build_args(body: str) -> tuple[str, str, str]:
    """Pull (description, target_path, tech) out of a build-intent body.
    Same logic as conversation_handler._route_message_inner. Defaults:
    target_path = ~/AI_Agent/games/<slug>.html, tech = html."""
    at_m = _TG_BUILD_AT_PATH_RE.match(body)
    if at_m:
        description = at_m.group(1).strip()
        target_path = at_m.group(2).strip()
    else:
        description = body
        slug_words = _re_p27.findall(r"[a-zA-Z0-9]+", description.lower())[:5]
        slug = "-".join(slug_words) or "build"
        target_path = f"~/AI_Agent/games/{slug}.html"
    tech_m = _TG_BUILD_TECH_RE.search(description)
    tech = tech_m.group(1).lower() if tech_m else "html"
    if tech == "md":
        tech = "markdown"
    if tech == "bash":
        tech = "shell"
    return description, target_path, tech


# ─── Phase 28 — slash command handlers ────────────────────────────────
# /code, /pro, /real → enqueue tier-aware Claude Code dispatch.
# /local → background qwen3-coder:30b build with auto-attach.
# /quick → synchronous quick_chat.
# All five ack within 2 seconds; the cc_dispatcher daemon + reporter
# handle long-running cloud dispatches out-of-band.

_TIER_TO_USER_FACING_CMD = {
    "max": "max", "flash": "code", "pro": "pro",
    "api": "api", "real": "real",
}


async def _handle_slash_dispatch(update: Update, tier: str, prompt: str) -> None:
    """Shared body for /max /code /pro /api /real. Routes through the
    tier-aware cc_dispatcher inbox. Acks immediately; the reporter
    daemon posts the completion + auto-attaches artifacts."""
    if not is_authorized(update):
        return
    if not prompt.strip():
        cmd = _TIER_TO_USER_FACING_CMD.get(tier, tier)
        await update.message.reply_text(f"/{cmd}: needs a prompt.")
        return
    try:
        from workers import conversation_handler as ch  # noqa: PLC0415
        result = await asyncio.to_thread(ch._enqueue_tiered_dispatch, prompt, tier)
    except Exception as exc:
        await update.message.reply_text(f"⚠️ slash dispatch error: {type(exc).__name__}: {exc}")
        return
    await _reply_chunked(update, result.get("reply", "(no reply)"))


async def max_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/max <prompt> — Claude Sonnet via Max plan ($0 marginal). Phase 29 default."""
    prompt = " ".join(context.args).strip() if context.args else ""
    await _handle_slash_dispatch(update, "max", prompt)


async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/code <prompt> — DeepSeek V4-Flash cloud build (saves Max quota)."""
    prompt = " ".join(context.args).strip() if context.args else ""
    await _handle_slash_dispatch(update, "flash", prompt)


async def pro_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pro <prompt> — DeepSeek V4-Pro cloud build (smarter, ~$0.05)."""
    prompt = " ".join(context.args).strip() if context.args else ""
    await _handle_slash_dispatch(update, "pro", prompt)


async def api_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/api <prompt> — Anthropic Sonnet 4.6 via API key (paid fallback)."""
    prompt = " ".join(context.args).strip() if context.args else ""
    await _handle_slash_dispatch(update, "api", prompt)


async def real_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/real <prompt> — DEPRECATED alias for /api. Logs to cc_logs/_deprecation.log."""
    from workers.conversation_handler import _log_deprecation  # noqa: PLC0415
    _log_deprecation("[DEPRECATED] /real is now /api — please update muscle memory")
    prompt = " ".join(context.args).strip() if context.args else ""
    await _handle_slash_dispatch(update, "api", prompt)


async def local_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/local <prompt> — qwen3-coder:30b local build, no API cost."""
    if not is_authorized(update):
        return
    prompt = " ".join(context.args).strip() if context.args else ""
    if not prompt:
        await update.message.reply_text("/local: needs a description.")
        return
    description, target_path, tech = _extract_build_args(prompt)
    short_desc = description if len(description) < 80 else description[:77] + "…"
    await update.message.reply_text(
        f"🛠️ /local Building: {short_desc}\n"
        f"  tech: {tech} | target: {target_path}\n"
        f"  qwen3-coder:30b — typically 30-90s. I'll ping when done."
    )
    asyncio.create_task(
        _build_in_background(update, description, target_path, tech, model="qwen3-coder:30b")
    )


async def quick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/quick <prompt> — qwen3:4b one-shot quick chat (no thinking)."""
    if not is_authorized(update):
        return
    prompt = " ".join(context.args).strip() if context.args else ""
    if not prompt:
        await update.message.reply_text("/quick: needs a question.")
        return
    await update.message.chat.send_action("typing")
    try:
        from workers import conversation_handler as ch  # noqa: PLC0415
        reply = await asyncio.wait_for(
            asyncio.to_thread(ch.quick_chat, prompt), timeout=30,
        )
    except asyncio.TimeoutError:
        await update.message.reply_text("/quick took >30s — try /local or rephrase.")
        return
    except Exception as exc:
        await update.message.reply_text(f"/quick error: {type(exc).__name__}: {exc}")
        return
    if not reply:
        reply = "(no reply)"
    await _reply_chunked(update, reply)


async def computer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/computer <task> — Phase 36 Computer Use agent. Drives the browser
    on Xvfb :99 to do dashboard tasks (Supabase, Vercel, Stripe, ...).
    Hard caps: 30 min, $5. Telegram updates every 30s with screenshots."""
    if not is_authorized(update):
        return
    task = " ".join(context.args).strip() if context.args else ""
    if not task:
        await update.message.reply_text(
            "/computer: needs a task. e.g. /computer resume the bidwatt supabase project"
        )
        return
    unsafe = "--unsafe" in task
    if unsafe:
        task = task.replace("--unsafe", "").strip()
    short = task if len(task) < 80 else task[:77] + "…"
    await update.message.reply_text(
        f"🖥️ /computer running: {short}\n"
        f"  display: :99 | safety: {'OFF (unsafe)' if unsafe else 'on'}\n"
        f"  caps: 30min, $5. I'll ping every 30s with a screenshot."
    )
    asyncio.create_task(_run_computer_in_background(update, task, unsafe))


async def _run_computer_in_background(update: Update, task: str, unsafe: bool) -> None:
    import time as _t  # noqa: PLC0415
    from tools import computer_agent, cu_browser, cu_recorder  # noqa: PLC0415

    bot = update.get_bot()
    chat_id = update.effective_chat.id
    last_update_at = [_t.monotonic()]
    last_iter = [-1]

    def _on_iter(iter_num: int, screenshot_path, model_text: str) -> None:
        # Throttle to ~one update per 30s; always ship the latest screenshot.
        now = _t.monotonic()
        if now - last_update_at[0] < 30 and iter_num != 1:
            return
        last_update_at[0] = now
        last_iter[0] = iter_num
        caption = (model_text or "(working)")[:900]
        try:
            with open(screenshot_path, "rb") as fh:
                asyncio.run_coroutine_threadsafe(
                    bot.send_photo(chat_id=chat_id, photo=fh,
                                   caption=f"iter {iter_num}: {caption}"),
                    asyncio.get_event_loop(),
                )
        except Exception:
            logger.exception("/computer iter update failed")

    try:
        cu_browser.launch("about:blank")
    except Exception as exc:
        await update.message.reply_text(f"⚠️ /computer browser launch failed: {exc}")
        return

    task_id = f"cu_{int(_t.time())}"
    rec = cu_recorder.Recorder((Path.home() / "AI_Agent" / "cu_logs" / task_id / "session.mp4"))
    try:
        rec.start()
        result = await asyncio.to_thread(
            computer_agent.run_task, task,
            task_id=task_id, unsafe=unsafe, on_iteration=_on_iter,
        )
    except Exception as exc:
        logger.exception("/computer crashed")
        await update.message.reply_text(f"⚠️ /computer crashed: {type(exc).__name__}: {exc}")
        return
    finally:
        rec.stop()

    summary = (
        f"🖥️ /computer {result.status}\n"
        f"  iters: {result.iterations} | elapsed: {result.elapsed_seconds:.0f}s | "
        f"cost: ${result.cost_usd:.3f}\n"
        f"  reason: {(result.reason or '')[:300]}\n"
        f"  log: {result.log_dir}"
    )
    if result.halt_reason:
        summary += f"\n  HALT: {result.halt_reason[:200]}"
    await update.message.reply_text(summary)
    if result.final_screenshot and Path(result.final_screenshot).exists():
        try:
            with open(result.final_screenshot, "rb") as fh:
                await bot.send_photo(chat_id=chat_id, photo=fh, caption="final screen")
        except Exception:
            logger.exception("final screenshot send failed")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — list every command Sparky responds to."""
    if not is_authorized(update):
        return
    await update.message.reply_text(
        "Sparky commands:\n\n"
        "Coding router (Phase 29 ladder, cheapest first):\n"
        "  /max <prompt>   — Claude Sonnet 4.6 via Max plan ($0 marginal) ★ default\n"
        "  /code <prompt>  — DeepSeek V4-Flash (~$0.005, saves Max quota)\n"
        "  /pro <prompt>   — DeepSeek V4-Pro (~$0.05)\n"
        "  /api <prompt>   — Sonnet 4.6 via API key (~$0.10–1.00, fallback)\n"
        "  /real <prompt>  — DEPRECATED alias for /api\n"
        "  /local <prompt> — qwen3-coder:30b local (free, offline)\n"
        "  /quick <prompt> — qwen3:4b quick chat (free)\n\n"
        "Computer Use (Phase 36):\n"
        "  /computer <task> — drives the :99 browser to do dashboard tasks\n"
        "                     hard caps: 30min wall clock, $5 spend\n"
        "                     append --unsafe to skip destructive-action stops\n\n"
        "Wiki:\n"
        "  wiki <query>     — search the Knowledge Garden\n"
        "  ingest <text|url> — add to the Knowledge Garden\n\n"
        "Dispatch (legacy):\n"
        "  dispatch: <prompt>       — Anthropic Sonnet via cc_dispatcher\n"
        "  force dispatch: <prompt> — bypass monthly budget cap\n"
        "  go cc_xxx                — release a held risky prompt\n"
        "  cancel cc_xxx            — drop a queued prompt\n"
        "  queue                    — show current queue + budget\n"
        "  retry cc_xxx             — re-run an archived dispatch\n"
        "  extend cc_xxx <minutes>  — re-dispatch with bigger budget\n"
        "  restart cc_xxx | nexus-* — bounce services\n\n"
        "Other:\n"
        "  /status   — Nexus health\n"
        "  /tasks    — recent tasks\n"
        "  /stop     — stop current task\n"
        "  build me X / make X / create X / code X — auto-routes to /max\n"
        "  make a quick/simple X                   — auto-routes to /local\n"
        "  script <topic> | create video <topic>   — Phase 21 content stack"
    )


async def _handle_content_command(update: Update, text: str) -> bool:
    """Phase 21 + 27 — short-form content + local build commands.
    Returns True if consumed.

    Shapes:
        script <topic>           — generate script only (fast, ~10-30s)
        create video <topic>     — full pipeline, video sent when done

    Phase 39 — the Phase 27 build-intent regex interception is removed.
    No-slash "build me X / create X" messages flow to route_message,
    where the LLM router dispatches them with the prompt verbatim.
    """
    low = text.strip().lower()

    if low.startswith("script "):
        topic = text.split(None, 1)[1].strip() if " " in text else ""
        if not topic:
            await update.message.reply_text("script: needs a topic.")
            return True
        await update.message.chat.send_action("typing")
        try:
            from tools import script_writer  # noqa: PLC0415
            result = await asyncio.wait_for(
                asyncio.to_thread(script_writer.script_write_core, topic, 30, "energetic"),
                timeout=120,
            )
        except asyncio.TimeoutError:
            await update.message.reply_text("Script generation took >120s. Try again.")
            return True
        except Exception as exc:
            await update.message.reply_text(f"⚠️ script: {type(exc).__name__}: {exc}")
            return True
        body = result.raw_text
        if len(body) > 3500:
            body = body[:3500] + "\n... [truncated, full at " + result.path + "]"
        cost_str = f" | cost ${result.cost_usd:.4f}" if result.cost_usd else " | free (local)"
        await update.message.reply_text(
            f"📝 {result.scene_count} scenes | backend {result.backend}{cost_str}\n\n{body}"
        )
        return True

    if low.startswith("create video ") or low.startswith("video: "):
        if low.startswith("video: "):
            topic = text.split(":", 1)[1].strip()
        else:
            topic = text.split(None, 2)[2].strip() if len(text.split()) >= 3 else ""
        if not topic:
            await update.message.reply_text("create video: needs a topic.")
            return True
        await update.message.reply_text(
            "🎬 Generating script + voiceovers + visuals + final mp4. "
            "Will send the file here when done (~2-5 min)."
        )
        # Run in background so the listener stays responsive.
        asyncio.create_task(_content_create_in_background(update, topic, 30))
        return True

    return False


async def _handle_dispatch_command(update: Update, text: str) -> bool:
    """Phase 22 — handle dispatch-control prefixes BEFORE conversation
    routing. Returns True if the message was consumed.

    Supported shapes (case-insensitive on the leading verb):
        dispatch: <prompt>           — queue a new CC dispatch
        force dispatch: <prompt>     — bypass monthly budget cap
        go cc_xxx                    — release a pending-approval prompt
        cancel cc_xxx                — drop a pending-approval prompt
        queue status                 — current queue snapshot
        restart cc_xxx | nexus-*     — restart services after a dispatch
        retry cc_xxx                 — re-dispatch the original prompt
        extend cc_xxx <minutes>      — re-dispatch with bigger budget
    """
    from core import cc_dispatch as _ccd  # local import: keep listener fast
    low = text.strip().lower()

    if low.startswith("dispatch:") or low.startswith("force dispatch:"):
        forced = low.startswith("force dispatch:")
        prompt = text.split(":", 1)[1].strip()
        if not prompt:
            await update.message.reply_text("dispatch: needs a prompt.")
            return True
        level, spend, budget = _ccd.budget_status()
        if level == "over" and not forced:
            await update.message.reply_text(
                f"Blocked: monthly Claude Code budget exhausted "
                f"(${spend:.2f}/${budget:.2f}). "
                f"Reply with 'force dispatch: ...' to override."
            )
            return True
        risky = _ccd.is_risky(prompt)
        from workers import llm_router as _lr  # noqa: PLC0415
        meta = _ccd.DispatchMeta.new(
            # Phase 39 — token-safe label, no mid-token cuts.
            label=_ccd.safe_label(prompt),
            time_budget_minutes=120,
            risky_match=risky,
            recon_mode=_lr.is_recon(prompt),
        )
        _ccd.write_prompt(meta, prompt, pending=bool(risky))
        snap = _ccd.queue_summary()
        ahead = snap["queued_count"]
        eta = f" — {ahead} ahead" if ahead else ""
        if risky:
            await update.message.reply_text(
                f"🚨 Risky prompt held (matched: {risky}). "
                f"Reply `go {meta.dispatch_id}` to dispatch."
            )
        else:
            await update.message.reply_text(
                f"🚀 Dispatched. id `{meta.dispatch_id}`{eta} "
                f"(budget {meta.time_budget_minutes}m). "
                f"I'll ping when it's done.",
                parse_mode="Markdown",
            )
        return True

    if low.startswith("go cc_"):
        did = text.split(None, 1)[1].strip()
        if _ccd.approve(did):
            await update.message.reply_text(f"✅ Released `{did}` — dispatching now.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"No pending dispatch with id `{did}`.", parse_mode="Markdown")
        return True

    if low.startswith("cancel cc_"):
        did = text.split(None, 1)[1].strip()
        if _ccd.cancel(did):
            await update.message.reply_text(f"🛑 Cancelled `{did}`.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"No dispatch to cancel for `{did}`.", parse_mode="Markdown")
        return True

    if low in ("queue status", "queue", "queue?", "/queue"):
        snap = _ccd.queue_summary()
        lines = []
        if snap["running"]:
            r = snap["running"]
            mins = r["elapsed_seconds"] / 60
            lines.append(f"▶︎ Running: `{r['dispatch_id']}` ({mins:.1f}m elapsed)")
        else:
            lines.append("▶︎ Running: (none)")
        lines.append(f"⏳ Queued: {snap['queued_count']}")
        for q in snap["queued"][:5]:
            lines.append(f"  - `{q['dispatch_id']}`")
        if snap["pending_approval"]:
            lines.append(f"🚨 Pending approval: {len(snap['pending_approval'])}")
            for p in snap["pending_approval"][:5]:
                lines.append(f"  - `{p['dispatch_id']}` (reply `go {p['dispatch_id']}`)")
        level, spend, budget = _ccd.budget_status()
        lines.append(f"💰 Budget: ${spend:.2f}/${budget:.2f} ({level})")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return True

    if low.startswith("restart "):
        target = text.split(None, 1)[1].strip()
        from tools import restart_services_tool  # noqa: PLC0415
        # `restart cc_xxx` → restart the default service set after a dispatch.
        # `restart nexus-foo` (or comma list) → restart specific services.
        if target.startswith("cc_"):
            services = None
        else:
            services = [s for s in (x.strip() for x in target.split(",")) if s]
        out = restart_services_tool.restart_services_sync(services)
        body = "\n".join(f"{'✓' if r['ok'] else '✗'} {r['message']}" for r in out["results"])
        await update.message.reply_text(
            f"Restarted {out['ok']}/{out['total']}:\n{body}"
        )
        return True

    if low.startswith("wiki ") or low == "wiki":
        query = text[len("wiki"):].strip()
        if not query:
            await update.message.reply_text("usage: `wiki <question>`", parse_mode="Markdown")
            return True
        try:
            from tools import wiki_tool  # noqa: PLC0415
            hits = wiki_tool.wiki_query.invoke({"question": query, "k": 3})
        except Exception as e:
            await update.message.reply_text(f"wiki_query error: {type(e).__name__}: {e}")
            return True
        # Telegram caps at 4096 chars; trim long bodies.
        if len(hits) > 3500:
            hits = hits[:3500] + "\n\n…(truncated)"
        await update.message.reply_text(hits)
        return True

    if low.startswith("ingest ") or low == "ingest":
        payload = text[len("ingest"):].strip()
        if not payload:
            await update.message.reply_text(
                "usage: `ingest <url or note>`", parse_mode="Markdown"
            )
            return True
        try:
            from tools import wiki_tool  # noqa: PLC0415
            msg = wiki_tool.wiki_ingest.invoke({
                "source": payload,
                "source_type": "manual",
            })
        except Exception as e:
            await update.message.reply_text(f"wiki_ingest error: {type(e).__name__}: {e}")
            return True
        await update.message.reply_text(f"📥 {msg}")
        return True

    if low.startswith("retry cc_") or low.startswith("extend cc_"):
        is_extend = low.startswith("extend cc_")
        parts = text.split()
        did = parts[1] if len(parts) >= 2 else ""
        new_budget = 240
        if is_extend and len(parts) >= 3:
            try:
                new_budget = max(5, min(int(parts[2]), 480))
            except ValueError:
                pass
        archive_path = _ccd.ARCHIVE / f"{did}.md"
        if not archive_path.exists():
            await update.message.reply_text(f"No archived dispatch `{did}`.", parse_mode="Markdown")
            return True
        meta, body = _ccd.read_prompt(archive_path)
        if not meta or not body:
            await update.message.reply_text(f"Could not parse archived dispatch `{did}`.", parse_mode="Markdown")
            return True
        new_meta = _ccd.DispatchMeta.new(
            label=("re-run: " if not is_extend else f"extend({new_budget}m): ") + meta.label,
            time_budget_minutes=new_budget if is_extend else meta.time_budget_minutes,
        )
        _ccd.write_prompt(new_meta, body, pending=False)
        await update.message.reply_text(
            f"🔁 Re-dispatched as `{new_meta.dispatch_id}` "
            f"(budget {new_meta.time_budget_minutes}m).",
            parse_mode="Markdown",
        )
        return True

    return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route user message through the conversation handler (Phase 15.5).

    The handler runs on qwen3:4b only and decides — via its own tool calls
    — whether to answer from queue state, modify a running task, or
    enqueue a new heavy task for the task_worker. Heavy turns NEVER run
    in this request; the bot replies fast (<10s) and the worker streams
    progress to memory/active_tasks.jsonl independently."""
    if not is_authorized(update):
        return

    user_message = update.message.text
    logger.info("Received message: %s", user_message[:100])
    await update.message.chat.send_action("typing")

    # Phase 22 dispatch shortcuts run BEFORE the LLM router so they're
    # deterministic and never blocked on Ollama.
    if await _handle_content_command(update, user_message):
        return

    if await _handle_dispatch_command(update, user_message):
        return

    chat_id = update.effective_chat.id
    # Phase 38: log every inbound user turn before routing. write_turn
    # is best-effort and never raises — a DB hiccup can't block routing.
    try:
        from core import telegram_chats as _tcs
        _tcs.write_turn(chat_id, "user", user_message)
    except Exception as e:
        logger.warning("telegram_chats user-write failed: %s", e)

    from workers import conversation_handler

    # Phase 41: live-draft streaming for pure chat turns. classify_intent
    # is the cheap deterministic gate (queue:/status/cancel/list bypass
    # it); the LLM router then confirms the turn is quick_chat (not a
    # build/task) before we stream tokens to a drafting bubble. Any failure
    # falls through to the blocking router path so the reply is never
    # dropped or delayed.
    streamed_reply: str | None = None
    try:
        intent = conversation_handler.classify_intent(user_message)
        if intent.get("kind") == "chat":
            from workers import llm_router
            route = await asyncio.to_thread(llm_router.route_llm, user_message)
            if route.get("route") == "quick_chat":
                streamed_reply = await _stream_quick_chat_reply(
                    update, user_message, chat_id)
    except Exception as e:
        logger.warning("stream path failed (%s) — falling back to router", e)
        streamed_reply = None

    if streamed_reply is not None:
        logger.info("route kind=chat chat_id=%s (streamed)", chat_id)
        try:
            from core import telegram_chats as _tcs
            _tcs.write_turn(chat_id, "assistant", streamed_reply)
        except Exception as e:
            logger.warning("telegram_chats assistant-write failed: %s", e)
        return

    try:
        # New 4-way LLM router: CHAT/QUERY -> brain inline reply,
        # TASK -> enqueue, STATUS -> task lookup. "queue: <text>" remains
        # a power-user prefix that bypasses classification. Run the
        # blocking router off the event loop so the bot stays responsive.
        # Phase 38: pass chat_id so quick_chat can prepend rolling history.
        result = await asyncio.wait_for(
            asyncio.to_thread(conversation_handler.route_message,
                              user_message, chat_id),
            timeout=25,
        )
        reply = result.get("reply", "")
        logger.info("route kind=%s chat_id=%s", result.get("kind"), chat_id)
    except asyncio.TimeoutError:
        await update.message.reply_text(
            "Took >25s to route — Ollama may be busy. Try again, or send "
            "'queue: <task>' to bypass classification."
        )
        return
    except Exception as e:
        logger.exception("conversation handler error: %s", e)
        await update.message.reply_text(f"handler error: {type(e).__name__}: {e}")
        return

    if not reply:
        reply = "(handler returned no text — try again)"

    # Phase 38: log the assistant reply before sending. Phase 41: the full
    # reply is logged and sent — chunked across messages by _reply_chunked
    # instead of truncated at 4000 chars.
    try:
        from core import telegram_chats as _tcs
        _tcs.write_turn(chat_id, "assistant", reply)
    except Exception as e:
        logger.warning("telegram_chats assistant-write failed: %s", e)

    await _reply_chunked(update, reply)


def main() -> None:
    """Start the Telegram bot."""
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        print("Add TELEGRAM_BOT_TOKEN=your_token to ~/AI_Agent/.env")
        sys.exit(1)

    # Phase 38: eager-init the conversation buffer so memory/telegram_chats.db
    # exists immediately on startup (idempotent). Without this the file is
    # lazy-created on first message, which makes ops verification harder.
    try:
        from core import telegram_chats as _tcs
        _tcs.init()
    except Exception as e:
        logger.warning("telegram_chats init failed: %s", e)

    # Create the Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("tasks", tasks_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("help", help_command))
    # Phase 28 — slash commands for tier-aware Claude Code routing.
    # Phase 29 added /max (default) + /api (renamed from /real).
    application.add_handler(CommandHandler("max", max_command))
    application.add_handler(CommandHandler("code", code_command))
    application.add_handler(CommandHandler("pro", pro_command))
    application.add_handler(CommandHandler("api", api_command))
    application.add_handler(CommandHandler("real", real_command))  # deprecated alias
    application.add_handler(CommandHandler("local", local_command))
    application.add_handler(CommandHandler("quick", quick_command))
    # Phase 36 — Computer Use agent (Anthropic native, drives :99 browser).
    application.add_handler(CommandHandler("computer", computer_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the bot
    logger.info("Starting Telegram listener...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

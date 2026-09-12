#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Nexus — LangGraph agent over local Ollama with a full tool belt."""
from __future__ import annotations

import re
import signal
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.prebuilt import create_react_agent

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import git_sync  # noqa: E402
import reflection  # noqa: E402
import router  # noqa: E402
from memory import sessions  # noqa: E402
from safety import circuit_breaker, guardrails  # noqa: E402,F401
from safety import sandbox as safety_sandbox  # noqa: E402,F401
from tools import context_compressor  # noqa: E402
from tools.sparky_state import SparkyCallbackHandler, instant_ack, post_state  # noqa: E402
from tools.searxng_tool import SEARXNG_TOOLS  # noqa: E402
from tools.search_router import WEB_SEARCH_TOOLS  # noqa: E402
from tools.scaffold_tool import SCAFFOLD_TOOLS  # noqa: E402
from tools.capabilities_tool import CAPABILITIES_TOOLS  # noqa: E402
from tools.browser_tool import browser_tool  # noqa: E402
from tools.browser_render import browser_render  # noqa: E402
from tools.file_tool import file_edit_tool, file_read_tool, file_write_tool  # noqa: E402
from tools.github_tool import GITHUB_TOOLS  # noqa: E402
from tools.markitdown_tool import markitdown_tool  # noqa: E402
from tools.mem0_tool import mem0_add, mem0_search  # noqa: E402
from tools.rag_tool import memory_add, memory_search, memory_list, memory_delete, memory_stats  # noqa: E402
from tools.chroma_dedup import memory_dedup, memory_compact  # noqa: E402
from tools.router_telemetry import router_telemetry, router_stats  # noqa: E402
from tools.search_tool import glob_tool, grep_tool  # noqa: E402
from tools.terminal_tool import terminal  # noqa: E402
from tools.tts_tool import tts_save, tts_speak  # noqa: E402
from tools.whisper_tool import whisper_record, whisper_transcribe  # noqa: E402
from tools.youtube_tool import YOUTUBE_TOOLS  # noqa: E402
from tools.telegram_tool import TELEGRAM_TOOLS  # noqa: E402
from tools.computer_use_tool import COMPUTER_USE_TOOLS  # noqa: E402
from tools.image_gen_tool import IMAGE_GEN_TOOLS  # noqa: E402
from tools.vercel_tool import VERCEL_TOOLS  # noqa: E402
# Tier-3 audit declutter (2026-06): the "game studio" cluster (opengame,
# godot, audio_gen/AudioCraft, bark, game_pipeline) was never provisioned —
# no Godot binary, AudioCraft/Bark uninstalled, OpenGame not cloned. Removed
# from the tool belt so the model stops considering dead tools. Files remain
# on disk; re-add the imports to restore if game dev is ever set up.
from tools.codebase_tool import CODEBASE_TOOLS  # noqa: E402
from tools.test_runner_tool import TEST_RUNNER_TOOLS  # noqa: E402
from tools.diff_tool import DIFF_TOOLS  # noqa: E402
from tools.coding_agent import CODING_AGENT_TOOLS, solve_coding_task  # noqa: E402
from tools.parallel_tools import PARALLEL_TOOLS  # noqa: E402
from tools.truncate import wrap_tools  # noqa: E402
from tools.glm_tool import GLM_TOOLS  # noqa: E402
from tools.bidwatt_tool import BIDWATT_TOOLS  # noqa: E402
from tools.notion_sync import NOTION_TOOLS  # noqa: E402
from tools.obsidian_sync import OBSIDIAN_TOOLS  # noqa: E402
from tools.chat_history_import import HISTORY_TOOLS  # noqa: E402
from tools.model_watcher import MODEL_WATCHER_TOOLS  # noqa: E402
from tools.cc_dispatch_tool import CC_DISPATCH_TOOLS  # noqa: E402
from tools.restart_services_tool import RESTART_SERVICES_TOOLS  # noqa: E402
from tools.weather_tool import WEATHER_TOOLS  # noqa: E402
from tools.wiki_tool import WIKI_TOOLS  # noqa: E402
from tools.script_writer import SCRIPT_WRITER_TOOLS  # noqa: E402
from tools.content_create import CONTENT_CREATE_TOOLS  # noqa: E402
from tools.vision_tool import VISION_TOOLS  # noqa: E402
from tools.desktop import DESKTOP_TOOLS  # noqa: E402  — Phase 3 headless :99
from tools.file_write import FILE_WRITE_TOOLS  # noqa: E402
from tools.bash_local import BASH_LOCAL_TOOLS  # noqa: E402
from tools.git_local import GIT_LOCAL_TOOLS  # noqa: E402
from tools.local_builder import LOCAL_BUILDER_TOOLS  # noqa: E402
from tools.system_probe import system_status  # noqa: E402
from tools.html_mockup import HTML_MOCKUP_TOOLS  # noqa: E402
from tools.goals_tool import GOALS_TOOLS  # noqa: E402
from tools.sandbox_tool import SANDBOX_TOOLS  # noqa: E402
from tools.code_intel import CODE_INTEL_TOOLS  # noqa: E402
from memory import metrics as agent_metrics  # noqa: E402
from memory import retros as agent_retros  # noqa: E402

OLLAMA_URL = "http://localhost:11434"
PROJECTS_DIR = Path.home() / "AI_Agent" / "projects"
MEMORY_DIR = Path.home() / "AI_Agent" / "memory"
CHECKPOINT_DB = MEMORY_DIR / "checkpoints.db"
LESSONS_MAX_LINES = 10          # Phase 1 prompt diet (was 60)
PROJECT_ACTIVE_DAYS = 7         # a project counts as "active" if its tasks.md
                                # was touched this recently
PROJECT_CONTEXT_MAX_CHARS = 2000

MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _open_checkpoint_conn() -> sqlite3.Connection:
    """Open the sync checkpoint connection with WAL + low-friction defaults
    so it shares the file cleanly with the async saver from the API path."""
    conn = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.commit()
    return conn


_checkpoint_conn = _open_checkpoint_conn()
_CHECKPOINTER = SqliteSaver(_checkpoint_conn)

TOOLS = [
    *CAPABILITIES_TOOLS,
    terminal,
    file_read_tool,
    file_write_tool,
    file_edit_tool,
    glob_tool,
    grep_tool,
    browser_tool,
    browser_render,
    memory_search,
    memory_add,
    memory_list,
    memory_delete,
    memory_stats,
    memory_dedup,
    memory_compact,
    router_telemetry,
    router_stats,
    markitdown_tool,
    mem0_add,
    mem0_search,
    *GITHUB_TOOLS,
    # brave_search/_news removed (Tier-3 declutter): no API key + SearXNG
    # covers web/news search locally for free.
    *SEARXNG_TOOLS,
    *WEB_SEARCH_TOOLS,
    *SCAFFOLD_TOOLS,
    whisper_record,
    whisper_transcribe,
    tts_speak,
    tts_save,
    *YOUTUBE_TOOLS,
    *TELEGRAM_TOOLS,
    *COMPUTER_USE_TOOLS,
    *IMAGE_GEN_TOOLS,
    *VERCEL_TOOLS,
    *CODEBASE_TOOLS,
    *TEST_RUNNER_TOOLS,
    *DIFF_TOOLS,
    *CODING_AGENT_TOOLS,
    *PARALLEL_TOOLS,
    *GLM_TOOLS,
    *BIDWATT_TOOLS,
    *NOTION_TOOLS,
    *OBSIDIAN_TOOLS,
    *HISTORY_TOOLS,
    *MODEL_WATCHER_TOOLS,
    *CC_DISPATCH_TOOLS,
    *RESTART_SERVICES_TOOLS,
    *WEATHER_TOOLS,
    *WIKI_TOOLS,
    *SCRIPT_WRITER_TOOLS,
    *CONTENT_CREATE_TOOLS,
    *VISION_TOOLS,
    *FILE_WRITE_TOOLS,
    *BASH_LOCAL_TOOLS,
    *GIT_LOCAL_TOOLS,
    *LOCAL_BUILDER_TOOLS,
    system_status,
    *HTML_MOCKUP_TOOLS,
    *GOALS_TOOLS,
    *SANDBOX_TOOLS,
    *CODE_INTEL_TOOLS,
    *DESKTOP_TOOLS,
]

# Phase 13.7 — every tool's return value passes through `truncate_tool_result`,
# which summarises payloads larger than ~500 tokens via qwen3:4b. Already
# bounded outputs (memory_*, router_*, etc.) are skipped inside wrap_tools.
wrap_tools(TOOLS, max_tokens=500)
# Phase 14.2 — record per-tool latency / tokens / success after the
# truncation wrapper so the metric line reflects what the model actually saw.
agent_metrics.wrap_tools_with_metrics(TOOLS)


def extend_tools_with_mcp() -> int:
    """Spawn external MCP servers from ~/AI_Agent/mcp/servers.json and
    append their discovered tools to TOOLS. Must be called before any
    agent is built. Returns the number of tools added."""
    import importlib.util
    client_path = ROOT / "mcp" / "client.py"
    if not client_path.exists():
        return 0
    spec = importlib.util.spec_from_file_location("nexus_mcp_client", client_path)
    if spec is None or spec.loader is None:
        return 0
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        mcp_tools = mod.load_mcp_tools()
    except Exception as exc:
        print(f"[mcp] failed to load external tools: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0
    TOOLS.extend(mcp_tools)
    # Phase 1 — remember which names came from MCP so the heavy/full
    # toolsets keep binding them (core/toolsets.py selects by name).
    _MCP_TOOL_NAMES.update(getattr(t, "name", "") for t in mcp_tools)
    return len(mcp_tools)


_MCP_TOOL_NAMES: set[str] = set()


def load_project_context() -> str:
    """Return the most recently modified projects/*/wiki/tasks.md prepended
    with its project name — but only when that project is *active* (touched
    within PROJECT_ACTIVE_DAYS). Empty string otherwise. Body is capped so
    a sprawling tasks.md can't blow up the static prompt."""
    if not PROJECTS_DIR.exists():
        return ""
    candidates = sorted(
        PROJECTS_DIR.glob("*/wiki/tasks.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return ""
    latest = candidates[0]
    if time.time() - latest.stat().st_mtime > PROJECT_ACTIVE_DAYS * 86400:
        return ""
    project = latest.parents[1].name
    try:
        body = latest.read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(body) > PROJECT_CONTEXT_MAX_CHARS:
        body = body[:PROJECT_CONTEXT_MAX_CHARS] + "\n…(truncated — read the file for the rest)"
    return f"# CURRENT PROJECT: {project}\n(from {latest})\n\n{body}"


def load_lessons() -> str:
    path = MEMORY_DIR / "lessons.md"
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    bullets = [ln for ln in text.splitlines() if ln.lstrip().startswith("- ")]
    if not bullets:
        return ""
    bullets = bullets[-LESSONS_MAX_LINES:]
    return "# LESSONS LEARNED (from past sessions)\n" + "\n".join(bullets)


def load_memory_facts() -> str:
    """G2 — durable learned facts about Colton/projects/machine (MEMORY.md,
    written by reflection.py). Injected into chat + heavy prompts so the agent
    remembers across sessions. Capped to the most recent entries."""
    path = MEMORY_DIR / "MEMORY.md"
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    bullets = [ln for ln in text.splitlines() if ln.lstrip().startswith("- ")]
    if not bullets:
        return ""
    bullets = bullets[-15:]  # Phase 1 prompt diet (was 40)
    return "## Learned facts about Colton (durable memory):\n" + "\n".join(bullets)


# Phase 1 prompt diet: the bound tool schemas already describe every tool,
# so this carries only guidance the schemas can't (≤ 600 chars).
_TOOL_HINT = (
    "# TOOLS\n"
    "Use tools proactively — never ask permission for read-only work.\n"
    "- Read a file before editing it; prefer grep_tool/glob_tool over dumping files.\n"
    "- web_search for anything current; wiki_query before speculating about "
    "Colton's projects or Nexus internals.\n"
    "- browser_render (not browser_tool) for x.com/linkedin/instagram/tiktok "
    "and whenever browser_tool returns an empty body.\n"
    "- Independent tool calls go in the SAME turn so they run in parallel.\n"
)
assert len(_TOOL_HINT) <= 600, len(_TOOL_HINT)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def load_static_prefix() -> str:
    """Return the byte-stable identity prefix: SOUL + STYLE + tool hint.

    Phase 1 prompt diet — TOOLS.md, NEXUS.md, LESSONS.md and wiki/index.md
    are no longer injected (the bound tool schemas describe the tools; the
    rest is reachable via wiki_query / file_read_tool on demand). SOUL and
    STYLE are read fresh on every call so an edit lands on the next
    startup without a stale module cache. CLAUDE.md is intentionally
    excluded — it is the autonomous-build playbook for Claude Code, not
    Nexus's own instruction set.
    """
    soul = _read_text(ROOT / "SOUL.md")
    style = _read_text(ROOT / "STYLE.md")
    sections = [f"# SOUL\n{soul}", f"# STYLE\n{style}", _TOOL_HINT]
    return "\n\n".join(sections)


def load_dynamic_suffix() -> str:
    """Return the slow-changing tail: recent lessons + durable memory facts
    + current-project context (only while a project is active). Empty if
    none. Live self-facts and the wall clock are NOT here — they ride in
    the per-turn message list (`turn_context_message`) so this prefix stays
    byte-stable between turns."""
    parts: list[str] = []
    lessons = load_lessons()
    if lessons:
        parts.append(lessons)
    facts = load_memory_facts()
    if facts:
        parts.append(facts)
    ctx = load_project_context()
    if ctx:
        parts.append(ctx)
    return "\n\n".join(parts)


def load_system_prompt() -> str:
    """Compose [STATIC_PREFIX][DYNAMIC_SUFFIX] and log token counts once."""
    static_part = load_static_prefix()
    dynamic_part = load_dynamic_suffix()
    composed = static_part if not dynamic_part else f"{static_part}\n\n{dynamic_part}"
    static_tokens = max(1, len(static_part) // 4)
    dynamic_tokens = max(0, len(dynamic_part) // 4)
    print(
        f"[prompt] static={len(static_part)}c/~{static_tokens}t  "
        f"dynamic={len(dynamic_part)}c/~{dynamic_tokens}t  "
        f"total=~{static_tokens + dynamic_tokens}t",
        file=sys.stderr,
    )
    return composed


# Agent cache is keyed by (model, toolset, is_async) so sync and async
# variants coexist — they share the same checkpoints.db file but talk to it
# via different saver objects so each variant sees only the methods it can
# call safely. Phase 1: toolset ("lite" / "heavy" / "full", see
# core/toolsets.py) decides which subset of TOOLS the LLM is bound to.
_AGENT_CACHE: dict[tuple[str, str, bool, bool], object] = {}


def tools_for(toolset: str | None = None) -> list:
    """Resolve a toolset name to the concrete subset of TOOLS (MCP tools
    included for heavy/full). None → $NEXUS_TOOLSET or 'heavy'."""
    from core import toolsets  # noqa: PLC0415
    return toolsets.select(TOOLS, toolset, extra_names=_MCP_TOOL_NAMES)
_SYSTEM_PROMPT = ""
_ASYNC_CHECKPOINTER: AsyncSqliteSaver | None = None


def set_system_prompt(prompt: str) -> None:
    """Lock in the system prompt that new agents will bake into their graph.
    Call once at startup before any agent is built."""
    global _SYSTEM_PROMPT
    _SYSTEM_PROMPT = prompt or ""


def _make_llm(model: str, *, reasoning: bool = False) -> ChatOllama:
    # num_ctx MUST be pinned — unset means the GGUF max (262144), which
    # allocated a 36.9 GB KV cache on qwen3:4b and crashed the GPU nightly.
    # reasoning=True (TASK-route agents only): Ollama `think` on — the
    # narration lands in additional_kwargs["reasoning_content"], content
    # stays the deliverable.
    from core import brain as _b  # noqa: PLC0415
    return ChatOllama(model=model, base_url=OLLAMA_URL, reasoning=reasoning,
                      num_ctx=_b.num_ctx_for(model))


def build_agent(model: str | None = None, toolset: str | None = None):
    """Build (and cache) a SYNC LangGraph agent for the given Ollama model.
    Use this from CLI / voice / any code path that calls `.invoke` or
    `.get_state` directly. For FastAPI's async handlers, call
    `build_agent_async` instead so checkpoint reads/writes don't go
    through LangGraph's `asyncio.to_thread` fallback on every turn.

    `toolset` — "lite" / "heavy" (default, or $NEXUS_TOOLSET) / "full"."""
    from core import toolsets  # noqa: PLC0415
    model = model or router.model_for("heavy")
    toolset = toolsets.resolve_name(toolset)
    key = (model, toolset, False, False)
    if key not in _AGENT_CACHE:
        _AGENT_CACHE[key] = create_react_agent(
            _make_llm(model),
            tools_for(toolset),
            prompt=_agent_prompt,
            checkpointer=_CHECKPOINTER,
        )
    return _AGENT_CACHE[key]


async def _get_async_checkpointer() -> AsyncSqliteSaver:
    """Lazy singleton: one aiosqlite connection shared by every async
    agent. Must be awaited — aiosqlite's Connection ctor is itself a
    coroutine and the saver needs a live connection to run setup().

    Enforces WAL + busy_timeout so concurrent sync (CLI) and async (API)
    accesses don't trip over the writer lock — the Phase 15 task worker
    + conversation handler will both share this saver."""
    global _ASYNC_CHECKPOINTER
    if _ASYNC_CHECKPOINTER is None:
        conn = await aiosqlite.connect(str(CHECKPOINT_DB), check_same_thread=False)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        await conn.commit()
        saver = AsyncSqliteSaver(conn)
        # Ensure the schema exists — no-op if the sync saver already ran it.
        try:
            await saver.setup()
        except Exception:
            pass
        _ASYNC_CHECKPOINTER = saver
    return _ASYNC_CHECKPOINTER


async def build_agent_async(model: str | None = None, toolset: str | None = None,
                            *, reasoning: bool = False):
    """Build (and cache) an ASYNC LangGraph agent backed by AsyncSqliteSaver
    on the same checkpoints.db. Use this from async contexts so state
    reads/writes don't block the event loop (or bounce through the thread
    pool on every turn). `toolset` as in `build_agent` (default heavy).
    `reasoning=True` is the TASK-route variant: Ollama think on + the
    phone-delivery style folded into the per-turn context."""
    from core import toolsets  # noqa: PLC0415
    model = model or router.model_for("heavy")
    toolset = toolsets.resolve_name(toolset)
    key = (model, toolset, True, reasoning)
    if key not in _AGENT_CACHE:
        saver = await _get_async_checkpointer()
        _AGENT_CACHE[key] = create_react_agent(
            _make_llm(model, reasoning=reasoning),
            tools_for(toolset),
            prompt=_task_agent_prompt if reasoning else _agent_prompt,
            checkpointer=saver,
        )
    return _AGENT_CACHE[key]


def agent_for_message(message: str, toolset: str | None = None) -> tuple[object, str, str]:
    """Classify the message, pick the right model, return (agent, route, model)."""
    route, model = router.classify_and_model(message)
    return build_agent(model, toolset), route, model


FAST_MODE_INSTRUCTION = (
    "FAST MODE: respond immediately, no thinking, no preamble, "
    "no tool calls unless strictly necessary, max 2 sentences."
)


def is_fast_route(route: str) -> bool:
    """A route counts as fast if the router classified it as a quick lookup."""
    return route == "fast"


_DT_MARKER = "[Current date and time:"

# Per-turn delivery rule for TASK-route agents (Telegram is the only
# consumer of queued-task output). Rides in the turn context, not SOUL.md.
TELEGRAM_DELIVERY_STYLE = (
    "Delivery is a phone screen. Lead with the answer in ≤3 lines. "
    "Prefer a table (≤4 columns, cells ≤6 words) or bullets over paragraphs. "
    "No preamble about sources or process. No closing questions or offers."
)


def turn_context_message(user_text: str = "", extra: str = "") -> SystemMessage:
    """Phase 1 — the volatile per-turn context (live self-facts + wall
    clock) as ONE SystemMessage, placed right after the static prefix by
    `_agent_prompt` on every step. It used to live in the system prompt,
    which made the static prefix change between turns. The date line is
    skipped when the caller already folded one into `user_text`
    (workers/task_worker.py does)."""
    parts: list[str] = []
    try:
        from core import self_facts  # noqa: PLC0415
        parts.append(self_facts.self_facts_block())
    except Exception as exc:  # never let a probe break a turn
        print(f"[prompt] self_facts skipped: {exc}", file=sys.stderr)
    if _DT_MARKER not in user_text:
        now = datetime.now().astimezone()
        parts.append(
            f"{_DT_MARKER} {now.isoformat(timespec='seconds')} "
            f"({now.strftime('%A')}). Use ONLY this for any time/date/day question.]"
        )
    if extra:
        parts.append(extra)
    return SystemMessage(content="\n\n".join(parts))


def _make_agent_prompt(extra: str = ""):
    """Per-step prompt builder handed to create_react_agent.

    Emits [static system prompt][turn context][…thread messages]. The
    static prefix (`_SYSTEM_PROMPT`) is byte-stable; the volatile bits
    (self-facts + wall clock, plus `extra`) are a separate SystemMessage
    rebuilt on every step, so they never get baked into the prefix and —
    unlike appending them to the per-turn message list — never accumulate
    in the checkpoint history of long threads."""
    def _agent_prompt(state) -> list:
        history = list(state["messages"])
        last_human = next(
            (m.content for m in reversed(history) if isinstance(m, HumanMessage)), "")
        out: list = []
        if _SYSTEM_PROMPT:
            out.append(SystemMessage(content=_SYSTEM_PROMPT))
        out.append(turn_context_message(
            last_human if isinstance(last_human, str) else "", extra))
        return out + history
    return _agent_prompt


_agent_prompt = _make_agent_prompt()
_task_agent_prompt = _make_agent_prompt(TELEGRAM_DELIVERY_STYLE)


def fast_mode_messages(user_text: str, *, route: str | None = None, override: bool | None = None) -> list:
    """Return the message list to feed into the agent for one turn.

    Prepends a SystemMessage with the FAST_MODE_INSTRUCTION when fast mode is
    on. Caller decides whether fast mode applies (`override`) or lets the
    router decide via the route name. Returns plain LangChain messages so
    both sync and async agent paths can use it. (Self-facts + datetime are
    injected per step by `_agent_prompt`, not here.)
    """
    fast = override if override is not None else (route is not None and is_fast_route(route))
    msgs: list = []
    if fast:
        msgs.append(SystemMessage(content=FAST_MODE_INSTRUCTION))
    msgs.append(HumanMessage(content=user_text))
    return msgs


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_OPEN_THINK_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)
# Orphan closer: the model (e.g. Ornith) emits raw reasoning then a bare
# </think> with no opening tag — everything up to and including it is reasoning.
_ORPHAN_CLOSE_RE = re.compile(r"^.*?</think>", re.DOTALL | re.IGNORECASE)

# Untagged reasoning preambles qwen3.6 sometimes emits when `think=False`
# is set on the model but the agent path is long enough that the system
# instruction drift matters. Anchored to start so we only catch the lead.
_REASONING_PREAMBLE_RE = re.compile(
    r"^\s*(?:"
    r"okay,?\s+(?:let'?s|let\s+me|so)\b"
    r"|hmm[,.\s]"
    r"|wait[,.\s]+(?:the\s+tool|but|i\s+|let)"
    r"|let\s+me\s+(?:check|see|think|look|verify)"
    r"|so\s+the\s+(?:user|tool|task)"
    r"|alright,?\s+(?:let|so)"
    r"|first[,.\s]+let\s+me"
    r")",
    re.IGNORECASE,
)


def strip_thinking(text: str) -> str:
    """Remove <think>...</think> reasoning blocks from a model response.
    qwen3:4b (the fast/router model) sometimes leaks these even with
    reasoning=False; strip before the text hits the user or reflection."""
    if not text:
        return text
    cleaned = _THINK_RE.sub("", text)
    cleaned = _OPEN_THINK_RE.sub("", cleaned)
    # Drop an orphan `</think>` closer + everything before it (reasoning with no
    # opening tag). Only when a closer is actually present, so normal text is safe.
    if "</think>" in cleaned.lower():
        cleaned = _ORPHAN_CLOSE_RE.sub("", cleaned)
    return cleaned.strip()


def looks_like_raw_reasoning(text: str) -> bool:
    """True if `text` appears to start with un-tagged qwen3 reasoning prose
    ("Okay, let me check...", "Hmm,", "Wait, the tool is..."). Used after
    strip_thinking() to detect leaks the regex can't catch.
    """
    if not text or len(text) < 8:
        return False
    return bool(_REASONING_PREAMBLE_RE.match(text))


_EXTRACT_FINAL_PROMPT = (
    "The text below is a leaked stream of an AI's reasoning process. "
    "Extract ONLY the user-facing final answer. Output the answer in "
    "1-3 short sentences, plain prose. No preamble. No reasoning. "
    "No <think> tags. No 'Hmm', 'Wait', 'Okay let me'. "
    "If there is no real answer in the text, output exactly: "
    "(no clean answer extracted)"
)


def extract_clean_answer(text: str, *, model: str | None = None) -> str:
    """One-shot Ollama call that asks the model to pull the final answer
    out of a reasoning-leaked response. Used as a fallback when
    `looks_like_raw_reasoning` flags the original output as suspect.
    Returns the original text on any failure so we never make things worse.

    Phase 39 — qwen3.6 retired as resident; defaults to the brain model
    (core/brain.py) with per-family think suppression.
    """
    if not text:
        return text
    try:
        import ollama  # noqa: PLC0415
        from core import brain as _brain  # noqa: PLC0415
        model = model or _brain.get_brain_model()
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=model,
            messages=[
                {"role": "system", "content": _EXTRACT_FINAL_PROMPT},
                {"role": "user", "content": text[:6000]},
            ],
            stream=False, think=_brain.think_param(model), keep_alive=-1,
            options={"temperature": 0.1, "num_predict": 400, "num_ctx": _brain.num_ctx_for(model)},
        )
    except Exception:
        return text
    body = (resp.get("message", {}) or {}).get("content", "").strip()
    body = strip_thinking(body)
    if not body or "(no clean answer extracted)" in body.lower():
        return text
    return body


def clean_task_reply(text: str, *, allow_reextract: bool = True) -> str:
    """Final scrub before a TASK reply hits the user.

    1. Strip <think>...</think> blocks (closed and open-ended).
    2. If what remains still looks like raw reasoning prose, ask qwen3.6
       to extract the final answer with a strict system prompt.

    Defense-in-depth on top of `reasoning=False` on the LLM. Pure text in,
    pure text out — never raises.
    """
    cleaned = strip_thinking(text or "")
    if allow_reextract and looks_like_raw_reasoning(cleaned):
        cleaned = strip_thinking(extract_clean_answer(cleaned))
    return cleaned


class ThinkStripper:
    """Stateful stripper for streaming qwen3 output. Feeds chunk-at-a-time
    and buffers partial <think> / </think> tags across chunk boundaries."""
    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self.in_think = False
        self.buf = ""

    def feed(self, text: str) -> str:
        if text:
            self.buf += text
        out: list[str] = []
        while self.buf:
            if self.in_think:
                idx = self.buf.find(self.CLOSE)
                if idx == -1:
                    keep = min(len(self.CLOSE) - 1, len(self.buf))
                    self.buf = self.buf[-keep:] if keep else ""
                    break
                self.buf = self.buf[idx + len(self.CLOSE):]
                self.in_think = False
            else:
                idx = self.buf.find(self.OPEN)
                if idx == -1:
                    keep = min(len(self.OPEN) - 1, len(self.buf))
                    emit = self.buf[:-keep] if keep else self.buf
                    if emit:
                        out.append(emit)
                    self.buf = self.buf[-keep:] if keep else ""
                    break
                if idx > 0:
                    out.append(self.buf[:idx])
                self.buf = self.buf[idx + len(self.OPEN):]
                self.in_think = True
        return "".join(out)

    def flush(self) -> str:
        if self.in_think:
            self.buf = ""
            return ""
        out = self.buf
        self.buf = ""
        return out


def _extract_reply(state: dict) -> str:
    msgs = state.get("messages", [])
    for m in reversed(msgs):
        content = getattr(m, "content", None)
        if content and (m.__class__.__name__ == "AIMessage" or getattr(m, "type", "") == "ai"):
            return strip_thinking(content)
    return strip_thinking(msgs[-1].content) if msgs else ""


_reflection_threads: list[threading.Thread] = []


def _spawn_reflection(user: str, reply: str, messages, route: str, model: str) -> None:
    """Run reflection.reflect in a non-daemon thread so the process waits for
    pending reflections to finish before exiting. Chains auto_commit after."""
    clean_reply = strip_thinking(reply)
    def _worker():
        try:
            reflection.reflect(user, clean_reply, messages=messages, route=route, model=model)
        except Exception:
            pass
        try:
            git_sync.auto_commit()
        except Exception:
            pass
    t = threading.Thread(target=_worker, name="reflect+commit", daemon=False)
    t.start()
    _reflection_threads.append(t)
    _reflection_threads[:] = [x for x in _reflection_threads if x.is_alive()]


def resolve_thread_id() -> tuple[str, bool]:
    """Ask the user whether to resume the last session. Returns (thread_id, resumed)."""
    last = sessions.get_current_thread()
    if last:
        try:
            ans = input(f"Resume last session {last[:8]}…? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        if ans in ("", "y", "yes"):
            sessions.touch_session(last, source="nexus")
            return last, True
    tid = str(uuid.uuid4())
    sessions.set_current_thread(tid)
    sessions.touch_session(tid, source="nexus")
    return tid, False


def interactive_loop() -> None:
    thread_id, resumed = resolve_thread_id()
    tag = "resumed" if resumed else "new"
    print(f"nexus ready — session {thread_id[:8]} ({tag}). ctrl-d / ctrl-c to exit.")
    sparky_cb = SparkyCallbackHandler()
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            post_state("idle")
            return
        if not user:
            continue
        agent, route, model = agent_for_message(user)
        fast = is_fast_route(route)
        tag = " FAST" if fast else ""
        print(f"[router: {route} → {model}{tag}]")
        ack = instant_ack(user, route=route)
        if ack:
            print(f"[sparky: {ack}]")
        config = {
            "configurable": {"thread_id": thread_id},
            "callbacks": [sparky_cb],
        }
        task_id = uuid.uuid4().hex[:12]
        turn_started = time.monotonic()
        turn_ok = True
        turn_err = ""
        result = {"messages": []}
        parts: list[str] = []
        with agent_metrics.task_context(task_id):
            try:
                print("nexus> ", end="", flush=True)
                stripper = ThinkStripper()
                for event in agent.stream(
                    {"messages": fast_mode_messages(user, route=route)},
                    config=config,
                    stream_mode="messages",
                ):
                    msg = event[0] if isinstance(event, tuple) and event else event
                    content = getattr(msg, "content", None)
                    if not content:
                        continue
                    text = "".join(
                        p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                    ) if isinstance(content, list) else str(content)
                    if not text:
                        continue
                    visible = stripper.feed(text)
                    if visible:
                        parts.append(visible)
                        print(visible, end="", flush=True)
                tail = stripper.flush()
                if tail:
                    parts.append(tail)
                    print(tail, end="", flush=True)
                print()
                try:
                    snap = agent.get_state(config)
                    result = {"messages": getattr(snap, "values", {}).get("messages", [])}
                except Exception:
                    result = {"messages": []}
            except Exception as exc:
                turn_ok = False
                turn_err = f"{type(exc).__name__}: {exc}"
                post_state("error", message=turn_err)
                raise
            finally:
                tool_calls = sum(
                    1 for m in result.get("messages", [])
                    if m.__class__.__name__ == "ToolMessage"
                )
                agent_metrics.record_agent_turn(
                    task_id=task_id,
                    started_at=turn_started,
                    ended_at=time.monotonic(),
                    route=route,
                    model=model,
                    user_text=user,
                    reply_text="".join(parts),
                    tool_calls=tool_calls,
                    success=turn_ok,
                    error=turn_err,
                )
                agent_retros.generate_retro_async(task_id)
        reply = strip_thinking("".join(parts))
        sessions.touch_session(thread_id, source="nexus", first_msg=user if not resumed and result["messages"] else None)
        sessions.set_current_thread(thread_id)
        _spawn_reflection(user, reply, result.get("messages"), route, model)
        try:
            cstatus = context_compressor.maybe_compress(agent, thread_id)
            if cstatus.get("compressed"):
                print(f"[context: compressed turn {cstatus['turn']} — dropped {cstatus.get('dropped', 0)} messages]")
        except Exception as exc:
            print(f"[context: compressor error: {type(exc).__name__}: {exc}]")
        post_state("idle")


def daemon_loop() -> None:
    """Keep the process alive under systemd when stdin is not a TTY.
    Remote entrypoints (nexus_api, Open WebUI) provide the real interface."""
    print("nexus-agent running in daemon mode; waiting for signal.", flush=True)
    stop = {"flag": False}

    def handle(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    while not stop["flag"]:
        time.sleep(60)
    print("nexus-agent shutting down.", flush=True)


def _coding_cli_mode(argv: list[str]) -> int:
    """`python3 nexus.py --code "<task>" --repo <path>` — headless run of
    the autonomous coding loop. Writes a markdown report to
    ~/AI_Agent/memory/coding-sessions/ and prints a short summary to
    stdout. Returns 0 on success, 1 on error."""
    import argparse
    ap = argparse.ArgumentParser(prog="nexus --code", add_help=True)
    ap.add_argument("--code", required=True, help="the coding task to solve")
    ap.add_argument("--repo", required=True, help="path to the repo root")
    ap.add_argument("--max-iterations", type=int, default=10)
    ap.add_argument("--no-commit", action="store_true", help="skip the git commit step")
    args = ap.parse_args(argv)
    try:
        report = solve_coding_task(
            args.code, args.repo,
            max_iterations=args.max_iterations,
            do_commit=not args.no_commit,
        )
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(report)
    return 0


def main() -> None:
    # Coding-agent CLI mode — handled before building the LangGraph agent.
    if len(sys.argv) > 1 and "--code" in sys.argv:
        sys.exit(_coding_cli_mode(sys.argv[1:]))
    set_system_prompt(load_system_prompt())
    added = extend_tools_with_mcp()
    if added:
        print(f"[mcp] loaded {added} external tools", file=sys.stderr)
    if sys.stdin.isatty():
        interactive_loop()
    else:
        daemon_loop()


if __name__ == "__main__":
    main()

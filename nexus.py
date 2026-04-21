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
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import create_react_agent

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import git_sync  # noqa: E402
import reflection  # noqa: E402
import router  # noqa: E402
from memory import sessions  # noqa: E402
from tools.browser_tool import browser_tool  # noqa: E402
from tools.file_tool import file_edit_tool, file_read_tool, file_write_tool  # noqa: E402
from tools.markitdown_tool import markitdown_tool  # noqa: E402
from tools.mem0_tool import mem0_add, mem0_search  # noqa: E402
from tools.rag_tool import memory_add, memory_search  # noqa: E402
from tools.search_tool import glob_tool, grep_tool  # noqa: E402
from tools.terminal_tool import terminal  # noqa: E402

OLLAMA_URL = "http://localhost:11434"
PROJECTS_DIR = Path.home() / "AI_Agent" / "projects"
MEMORY_DIR = Path.home() / "AI_Agent" / "memory"
CHECKPOINT_DB = MEMORY_DIR / "checkpoints.db"
LESSONS_MAX_LINES = 60

MEMORY_DIR.mkdir(parents=True, exist_ok=True)
_checkpoint_conn = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
_CHECKPOINTER = SqliteSaver(_checkpoint_conn)

TOOLS = [
    terminal,
    file_read_tool,
    file_write_tool,
    file_edit_tool,
    glob_tool,
    grep_tool,
    browser_tool,
    memory_search,
    memory_add,
    markitdown_tool,
    mem0_add,
    mem0_search,
]


def load_project_context() -> str:
    """Return the most recently modified projects/*/wiki/tasks.md prepended
    with its project name, or an empty string if none exist."""
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
    project = latest.parents[1].name
    try:
        body = latest.read_text(encoding="utf-8")
    except OSError:
        return ""
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


def load_system_prompt() -> str:
    soul = (ROOT / "SOUL.md").read_text()
    style = (ROOT / "STYLE.md").read_text()
    tool_hint = (
        "# TOOLS\n"
        "You have a full tool belt. Use it proactively — don't ask permission "
        "for read-only work, just do it.\n"
        "- `terminal(command)`: run shell commands (30s timeout).\n"
        "- `file_read_tool(path)`: read any file on disk (~ expands).\n"
        "- `file_write_tool(path, content)`: create or overwrite a file.\n"
        "- `file_edit_tool(path, old_string, new_string)`: find/replace in a file.\n"
        "- `glob_tool(pattern, root='.')`: list files matching a glob (supports **).\n"
        "- `grep_tool(pattern, root='.', glob='**/*')`: regex search file contents.\n"
        "- `browser_tool(url)`: fetch a URL with headless Chromium and return text.\n"
        "- `memory_search(query_text, k=4)`: query long-term memory (Chroma RAG).\n"
        "- `memory_add(text)`: save a snippet to long-term memory (Chroma RAG).\n"
        "- `markitdown_tool(source)`: convert a PDF/Word/Excel/PPT/URL to markdown and stash in RAG.\n"
        "- `mem0_add(text)`: extract durable facts from text into Mem0 (LLM-refined).\n"
        "- `mem0_search(query, k=5)`: semantic search of Mem0 memories.\n\n"
        "Guidelines:\n"
        "- Read files before editing them.\n"
        "- Prefer `grep_tool`/`glob_tool` for codebase exploration over dumping whole files.\n"
        "- Use `browser_tool` when the user cites a URL or you need current info the model doesn't have.\n"
        "- After completing a task, consider `memory_add` to record anything useful for future sessions.\n"
    )
    ctx = load_project_context()
    lessons = load_lessons()
    sections = [f"# SOUL\n{soul}", f"# STYLE\n{style}", tool_hint]
    if lessons:
        sections.append(lessons)
    if ctx:
        sections.append(ctx)
    return "\n\n".join(sections)


_AGENT_CACHE: dict[str, object] = {}
_SYSTEM_PROMPT = ""


def set_system_prompt(prompt: str) -> None:
    """Lock in the system prompt that new agents will bake into their graph.
    Call once at startup before any agent is built."""
    global _SYSTEM_PROMPT
    _SYSTEM_PROMPT = prompt or ""


def build_agent(model: str | None = None):
    """Build (and cache) a LangGraph agent for the given Ollama model.
    If `model` is None, uses the router's `heavy` default.
    """
    model = model or router.model_for("heavy")
    if model not in _AGENT_CACHE:
        llm = ChatOllama(model=model, base_url=OLLAMA_URL, reasoning=False)
        _AGENT_CACHE[model] = create_react_agent(
            llm,
            TOOLS,
            prompt=_SYSTEM_PROMPT or None,
            checkpointer=_CHECKPOINTER,
        )
    return _AGENT_CACHE[model]


def agent_for_message(message: str) -> tuple[object, str, str]:
    """Classify the message, pick the right model, return (agent, route, model)."""
    route, model = router.classify_and_model(message)
    return build_agent(model), route, model


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_OPEN_THINK_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """Remove <think>...</think> reasoning blocks from a model response.
    qwen3:4b (the fast/router model) sometimes leaks these even with
    reasoning=False; strip before the text hits the user or reflection."""
    if not text:
        return text
    cleaned = _THINK_RE.sub("", text)
    cleaned = _OPEN_THINK_RE.sub("", cleaned)
    return cleaned.strip()


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
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not user:
            continue
        agent, route, model = agent_for_message(user)
        print(f"[router: {route} → {model}]")
        config = {"configurable": {"thread_id": thread_id}}
        result = agent.invoke({"messages": [HumanMessage(content=user)]}, config=config)
        reply = _extract_reply(result)
        print(f"nexus> {reply}")
        sessions.touch_session(thread_id, source="nexus", first_msg=user if not resumed and result["messages"] else None)
        sessions.set_current_thread(thread_id)
        _spawn_reflection(user, reply, result.get("messages"), route, model)


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


def main() -> None:
    set_system_prompt(load_system_prompt())
    if sys.stdin.isatty():
        interactive_loop()
    else:
        daemon_loop()


if __name__ == "__main__":
    main()

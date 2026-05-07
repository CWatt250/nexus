"""Parallel composite tools for Phase 13.5.

LangGraph's ToolNode already runs multiple `tool_calls` from a single
AIMessage concurrently, but only if the model issues them as a batch. The
composites here force parallelism even when the model picks just one tool
call. Each composite runs two related lookups in a ThreadPoolExecutor and
returns both results in one labeled string so the agent doesn't pay two
sequential round-trips.

Pairs (per spec):
  - web_search + rag_search        -> quick_lookup
  - get_file_context + git log     -> repo_inspect
  - clipboard_read + screenshot    -> screen_clip
"""
from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_core.tools import tool

from tools import rag_tool
from tools.brave_search_tool import brave_search
from tools.codebase_tool import get_file_context

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="parallel-tool")


def _label(name: str, body: str) -> str:
    return f"## {name}\n{body.strip() if isinstance(body, str) else body}"


def _safe(fn, *args, **kwargs) -> str:
    try:
        return str(fn(*args, **kwargs))
    except Exception as exc:
        return f"[{type(exc).__name__}: {exc}]"


def _run_two(a, b) -> tuple[str, str]:
    fa = _POOL.submit(a)
    fb = _POOL.submit(b)
    return fa.result(), fb.result()


@tool
def quick_lookup(query: str, count: int = 5) -> str:
    """Run web search and RAG memory search in parallel for one query.

    Use when you need both fresh web hits and prior-session context for
    the same topic — saves a sequential round-trip vs calling
    brave_search and memory_search separately."""
    web, mem = _run_two(
        lambda: _safe(brave_search.invoke, {"query": query, "count": count}),
        lambda: _safe(rag_tool.memory_search.invoke, {"query_text": query, "k": count}),
    )
    return "\n\n".join([_label("WEB (brave)", web), _label("MEMORY (chroma)", mem)])


def _git_log(repo_path: str, n: int = 10) -> str:
    repo = Path(repo_path).expanduser().resolve()
    if not (repo / ".git").exists():
        return f"[not a git repo: {repo}]"
    try:
        out = subprocess.run(
            ["git", "log", f"-n{n}", "--oneline", "--decorate", "--no-color"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return "[git log: timeout]"
    return out.stdout.strip() or out.stderr.strip() or "[git log: empty]"


@tool
def repo_inspect(file_path: str, repo_path: str = "", git_n: int = 10) -> str:
    """Read a file's symbol/import context AND the repo's recent git log in parallel.

    repo_path defaults to the file's enclosing repo root if omitted."""
    fp = Path(file_path).expanduser().resolve()
    if not repo_path:
        cur = fp if fp.is_dir() else fp.parent
        for _ in range(8):
            if (cur / ".git").exists():
                repo_path = str(cur)
                break
            if cur.parent == cur:
                break
            cur = cur.parent
        if not repo_path:
            repo_path = str(fp.parent)
    ctx, glog = _run_two(
        lambda: _safe(get_file_context.invoke, {"file_path": file_path}),
        lambda: _safe(_git_log, repo_path, git_n),
    )
    return "\n\n".join([_label(f"FILE CONTEXT ({file_path})", ctx),
                        _label(f"GIT LOG ({repo_path})", glog)])


def _read_clipboard() -> str:
    try:
        out = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except FileNotFoundError:
        return "[xclip not installed]"
    except subprocess.TimeoutExpired:
        return "[clipboard: timeout]"
    text = out.stdout
    return text[:4000] if text else "[clipboard: empty]"


def _resolve_display_for_scrot() -> str | None:
    """Return DISPLAY to use for scrot — real env first, :99 headless fallback."""
    current = os.environ.get("DISPLAY")
    if current:
        return current
    try:
        if subprocess.run(
            ["xdpyinfo", "-display", ":99"],
            capture_output=True, timeout=2,
        ).returncode == 0:
            return ":99"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _take_screenshot() -> str:
    out_dir = Path.home() / "AI_Agent" / "output" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"screen-{stamp}.png"
    display = _resolve_display_for_scrot()
    if not display:
        return "[scrot: no display available (real or :99)]"
    env = {**os.environ, "DISPLAY": display}
    try:
        subprocess.run(
            ["scrot", str(target)],
            check=True,
            capture_output=True,
            timeout=5,
            env=env,
        )
    except FileNotFoundError:
        return "[scrot not installed]"
    except subprocess.CalledProcessError as exc:
        return f"[scrot failed: {exc.stderr.decode(errors='replace').strip()}]"
    except subprocess.TimeoutExpired:
        return "[scrot: timeout]"
    return f"saved {target}"


@tool
def screen_clip() -> str:
    """Capture clipboard contents AND a fresh screenshot in parallel.

    Returns clipboard text (truncated to 4kB) plus the screenshot save path."""
    clip, shot = _run_two(_read_clipboard, _take_screenshot)
    return "\n\n".join([_label("CLIPBOARD", clip), _label("SCREENSHOT", shot)])


PARALLEL_TOOLS = [quick_lookup, repo_inspect, screen_clip]

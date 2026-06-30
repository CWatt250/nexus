"""Phase 27.2 — local_builder.

Generates code from a natural-language description using local
qwen3.6 (free), then writes it via the scope-guarded write_file tool.
Single-file targets only in this part (HTML games, Python scripts,
markdown docs). Multi-file projects are out of scope — for those use
the existing scaffold_project tool or dispatch to claude code.

The whole point: when the user says "build me snake", Nexus produces
the file locally at $0 cost without spawning a Claude Code session.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from tools.file_write import _resolve_in_scope

log = logging.getLogger("nexus.local_builder")

OLLAMA_HOST = "http://localhost:11434"


def _live_model(key: str = "code", default: str = "qwen3-coder:30b") -> str:
    """Resolve from models.json (was hardcoded qwen3.6, which pinned 23GB
    resident via keep_alive=-1). models.json `code` is the resident brain → 0 extra VRAM."""
    try:
        import json
        return json.loads((Path.home() / "AI_Agent" / "models.json").read_text()).get(key) or default
    except Exception:
        return default


OLLAMA_MODEL = _live_model("code")

# Tech-stack-keyed system prompts. Each one frames the model for the
# format it's about to emit so the output isn't wrapped in markdown
# fences or chatty preamble.
TECH_PROMPTS: dict[str, str] = {
    "html": (
        "You are a senior front-end developer. Output ONE complete, "
        "self-contained HTML file with inline CSS and inline JavaScript. "
        "It must run by opening the file in a browser — no build step, "
        "no external dependencies, no CDN scripts.\n\n"
        "Constraints:\n"
        "- Output ONLY the HTML. No markdown fences. No prose around it.\n"
        "- Start with `<!doctype html>`. End with `</html>`.\n"
        "- All styles inline in <style> tags, all JS inline in <script> tags.\n"
        "- Make it actually work — playable, interactive, with sensible "
        "  defaults. Add a title and a clean dark UI."
    ),
    "python": (
        "You are a senior Python developer. Output ONE complete Python "
        "script that runs as `python3 <file>`.\n\n"
        "Constraints:\n"
        "- Output ONLY the Python source. No markdown fences. No prose.\n"
        "- Stdlib only unless the user asked for a specific library.\n"
        "- Include a `if __name__ == '__main__':` entry point."
    ),
    "markdown": (
        "Output ONE complete markdown document. No fences around the "
        "whole thing — just the markdown body. Use headings, lists, "
        "code blocks where appropriate."
    ),
    "shell": (
        "Output ONE complete bash script. Start with `#!/usr/bin/env bash` "
        "and `set -euo pipefail`. No markdown fences."
    ),
}

DEFAULT_TECH = "html"


@dataclass
class BuildResult:
    path: str
    bytes_written: int
    lines: int
    tech_stack: str
    description: str
    wall_seconds: float
    backend: str
    notes: str  # any verification flags (broken-code suspicion, scope refusal)
    code_excerpt: str  # first ~500 chars for telemetry / Telegram echo


def _strip_code_fences(text: str) -> str:
    """Models occasionally wrap output in ```html ... ``` despite being
    told not to. Strip the outermost fence if present so the file isn't
    syntactically broken."""
    text = text.strip()
    fence_match = re.match(
        r"^```[a-zA-Z]*\s*\n(.*?)\n```\s*$", text, re.DOTALL
    )
    if fence_match:
        return fence_match.group(1).strip()
    # Some models emit ``` on its own first line + last line.
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def _strip_thinking(text: str) -> str:
    """qwen3.6 sometimes leaks <think>...</think> chain-of-thought."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _basic_sanity(content: str, tech: str) -> Optional[str]:
    """Return a notes-string describing suspicious output, or None when
    the content looks coherent. Per spec rule 4 we don't try to FIX
    bad output — just flag it."""
    if not content.strip():
        return "code may have issues: empty output"
    if tech == "html":
        low = content.lower()
        if "<!doctype html" not in low and "<html" not in low:
            return "code may have issues: no <!doctype html> or <html> tag"
        if "</html>" not in low:
            return "code may have issues: missing closing </html>"
    if tech == "python":
        # Cheap proxy: should have at least one `def ` or `class ` or run-line.
        if "def " not in content and "class " not in content and "print" not in content:
            return "code may have issues: no def/class/print found"
    return None


def _generate_via_ollama(description: str, tech: str,
                         model: str = OLLAMA_MODEL) -> tuple[str, str]:
    """Call the Ollama chat API and return (code, error_or_empty). The
    `model` param defaults to qwen3.6 (Phase 27 contract); /local and
    SIMPLE_BUILD pass qwen3-coder:30b for stronger code output."""
    try:
        import ollama  # noqa: PLC0415
    except Exception as exc:
        return "", f"ollama package missing: {exc}"
    sys_prompt = TECH_PROMPTS.get(tech, TECH_PROMPTS[DEFAULT_TECH])
    user_prompt = (
        f"Build the following:\n\n{description}\n\n"
        f"Output the {tech} file content directly, nothing else."
    )
    try:
        resp = ollama.Client(host=OLLAMA_HOST).chat(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=False,
            think=False,
            keep_alive=-1,
            options={
                "temperature": 0.4,
                "num_ctx": 8192,
                "num_predict": 4096,
            },
        )
    except Exception as exc:
        return "", f"ollama call failed: {type(exc).__name__}: {exc}"
    raw = (resp.get("message", {}) or {}).get("content", "")
    cleaned = _strip_code_fences(_strip_thinking(raw))
    return cleaned, ""


def build_thing_core(
    description: str,
    target_path: str,
    tech_stack: str = DEFAULT_TECH,
    model: Optional[str] = None,
) -> BuildResult:
    """Programmatic entry point for the @tool wrapper, the conversation-
    handler routing path, and unit tests. Raises only on scope refusal
    or model-call failure; otherwise returns a BuildResult with notes
    flagging any code-suspicion."""
    tech = (tech_stack or DEFAULT_TECH).lower()
    if tech not in TECH_PROMPTS:
        tech = DEFAULT_TECH

    started = time.monotonic()
    chosen_model = model or OLLAMA_MODEL
    code, err = _generate_via_ollama(description, tech, model=chosen_model)
    if err:
        raise RuntimeError(f"local_builder generation failed: {err}")

    notes = _basic_sanity(code, tech) or "ok"

    target, scope_err = _resolve_in_scope(target_path)
    if scope_err or target is None:
        raise RuntimeError(f"target path refused: {scope_err}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")

    wall = round(time.monotonic() - started, 2)
    excerpt = code[:500] + ("\n[...]" if len(code) > 500 else "")
    return BuildResult(
        path=str(target),
        bytes_written=len(code.encode("utf-8")),
        lines=code.count("\n") + 1,
        tech_stack=tech,
        description=description,
        wall_seconds=wall,
        backend=f"ollama:{chosen_model}",
        notes=notes,
        code_excerpt=excerpt,
    )


@tool
def build_thing(
    description: str,
    target_path: str,
    tech_stack: str = "html",
) -> str:
    """Generate a single-file project from a natural-language description
    using local qwen3.6 (no API cost, no Claude Code dispatch).

    Single-file output only — HTML games / widgets, Python scripts,
    markdown docs, bash scripts. For multi-file projects use the
    existing `scaffold_project` tool, or dispatch to claude code.

    Args:
        description: What to build. Be specific about features +
            mechanics. "snake game with arrow key controls, score
            counter, classic mechanics" beats "snake".
        target_path: Where to write the file. Must live under
            ~/AI_Agent, ~/Dev, /tmp, ~/Documents, or ~/Downloads
            (scope-guarded by tools/file_write.py).
        tech_stack: One of html / python / markdown / shell.
            Default html.

    Returns:
        Status line — file path, byte count, lines, wall time, and
        any verification notes. If the model produced obviously broken
        output, the notes column flags it but the file is still written
        per spec rule 4 (let the user iterate, don't try to fix the LLM).
    """
    try:
        result = build_thing_core(description, target_path, tech_stack)
    except RuntimeError as exc:
        return f"build failed: {exc}"
    return (
        f"built {result.path}\n"
        f"  tech    : {result.tech_stack}\n"
        f"  size    : {result.bytes_written} bytes / {result.lines} lines\n"
        f"  wall    : {result.wall_seconds}s on {result.backend}\n"
        f"  notes   : {result.notes}"
    )


LOCAL_BUILDER_TOOLS = [build_thing]

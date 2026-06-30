"""Code intelligence (G6) — real diagnostics for the coding agent.

A full LSP client is a heavy, stateful protocol; what an autonomous agent
actually needs is "what's wrong with this file" after an edit. This runs the
real tools:
  .py            → ruff (lint + errors + undefined names), or ast syntax-check
                   as a stdlib fallback if ruff isn't installed.
  .js/.mjs/.cjs  → node --check (syntax).
  .ts/.tsx       → tsc --noEmit if available, else a note.

code_check_changed() runs diagnostics on every file in the git working tree —
the natural "did my edits break anything" check.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from langchain_core.tools import tool

ROOT = Path.home() / "AI_Agent"
_RUFF = ROOT / "venv" / "bin" / "ruff"


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else ROOT / path


def _diag_py(p: Path) -> str:
    if _RUFF.exists():
        try:
            r = subprocess.run(
                [str(_RUFF), "check", "--output-format=concise", "--no-cache", str(p)],
                capture_output=True, text=True, timeout=30)
            out = (r.stdout or r.stderr).strip()
            return out if out else "✓ ruff: no issues"
        except Exception as exc:
            return f"ruff failed ({type(exc).__name__}); falling back to syntax check"
    try:
        ast.parse(p.read_text(encoding="utf-8", errors="replace"), filename=str(p))
        return "✓ syntax OK (install ruff for full lint/error diagnostics)"
    except SyntaxError as e:
        return f"SyntaxError at line {e.lineno}, col {e.offset}: {e.msg}"


def _diag_js(p: Path) -> str:
    try:
        r = subprocess.run(["node", "--check", str(p)], capture_output=True,
                           text=True, timeout=20)
        return "✓ node: syntax OK" if r.returncode == 0 else (r.stderr or "syntax error").strip()
    except Exception as exc:
        return f"node check unavailable: {type(exc).__name__}"


def _diag_ts(p: Path) -> str:
    import shutil
    tsc = shutil.which("tsc")
    if not tsc:
        return "tsc not installed — can't type-check TS (npm i -g typescript to enable)"
    try:
        r = subprocess.run([tsc, "--noEmit", "--skipLibCheck", str(p)],
                           capture_output=True, text=True, timeout=60)
        out = (r.stdout or r.stderr).strip()
        return out if out else "✓ tsc: no type errors"
    except Exception as exc:
        return f"tsc failed: {type(exc).__name__}"


def _diagnose(p: Path) -> str:
    ext = p.suffix.lower()
    if ext == ".py":
        return _diag_py(p)
    if ext in (".js", ".mjs", ".cjs", ".jsx"):
        return _diag_js(p)
    if ext in (".ts", ".tsx"):
        return _diag_ts(p)
    return f"no diagnostics for {ext or 'this file type'}"


@tool
def code_diagnostics(path: str) -> str:
    """Run real diagnostics on a code file — errors, lint, undefined names
    (Python via ruff), or syntax (JS via node, TS via tsc). Use this after
    editing a file to catch mistakes before running it.

    Args:
        path: file to check (repo-relative or absolute).
    """
    p = _resolve(path)
    if not p.is_file():
        return f"not a file: {path}"
    return f"[{p.name}] {_diagnose(p)}"


@tool
def code_check_changed() -> str:
    """Run diagnostics on every code file in the git working tree (the
    'did my edits break anything' check). Returns one line per changed file."""
    try:
        r = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=15)
        files = [f for f in r.stdout.splitlines() if f.strip()]
    except Exception as exc:
        return f"git diff failed: {type(exc).__name__}"
    if not files:
        return "no changed files in the working tree"
    out = []
    for f in files:
        p = ROOT / f
        if p.is_file() and p.suffix.lower() in (
                ".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"):
            out.append(f"[{f}] {_diagnose(p)}")
    return "\n".join(out) if out else "no checkable code files changed"


CODE_INTEL_TOOLS = [code_diagnostics, code_check_changed]

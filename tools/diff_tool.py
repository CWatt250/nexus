"""Git diff tools for Nexus — read, review (via qwen3.6), approve."""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

import ollama
from langchain_core.tools import tool

OLLAMA_URL = "http://localhost:11434"
REVIEW_MODEL = "qwen3.6:latest"
log = logging.getLogger("nexus.diff")

REVIEW_PROMPT = (
    "You are a senior engineer reviewing this diff. List:\n"
    "1) Any bugs introduced\n"
    "2) Any security issues\n"
    "3) Any missing error handling\n"
    "4) Any tests that should be added\n"
    "Be specific and brief. Output plain text, bullet points per section."
)


def _run(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"


def _resolve(repo_path: str) -> Path:
    return Path(repo_path).expanduser().resolve()


@tool
def get_diff(repo_path: str) -> str:
    """Return the current git diff for `repo_path` (staged + unstaged)."""
    p = _resolve(repo_path)
    if not (p / ".git").exists():
        return f"ERROR: not a git repo: {p}"
    rc1, out1, _ = _run(["git", "diff", "HEAD"], p)
    if rc1 == 0 and out1.strip():
        return out1
    # fall back to unstaged only (fresh repo with no HEAD yet)
    rc2, out2, _ = _run(["git", "diff"], p)
    return out2 or "(no changes)"


@tool
def review_diff(repo_path: str) -> str:
    """Send the current diff to qwen3.6 for a senior-engineer review.
    Returns the review text, or '(no diff)' if there's nothing to review."""
    diff = get_diff.invoke({"repo_path": repo_path})
    if not diff or diff.startswith("ERROR") or diff.strip() == "(no changes)":
        return diff or "(no diff)"
    if len(diff) > 60_000:
        diff = diff[:60_000] + "\n\n[...truncated...]"
    try:
        resp = ollama.Client(host=OLLAMA_URL).chat(
            model=REVIEW_MODEL,
            messages=[
                {"role": "system", "content": REVIEW_PROMPT},
                {"role": "user", "content": diff},
            ],
            stream=False,
            think=False,
            options={"temperature": 0.2, "num_predict": 800, "num_ctx": 16_384},
        )
    except Exception as exc:
        return f"ERROR: reviewer LLM failed — {type(exc).__name__}: {exc}"
    content = resp["message"]["content"] if isinstance(resp, dict) else getattr(resp.message, "content", "")
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL | re.IGNORECASE).strip()
    return content or "(empty review)"


@tool
def approve_diff(repo_path: str, message: str = "auto-commit via Nexus") -> str:
    """If `review_diff` returns without concerns, stage and commit with
    `message`. Returns the commit SHA or an error string."""
    p = _resolve(repo_path)
    if not (p / ".git").exists():
        return f"ERROR: not a git repo: {p}"
    review = review_diff.invoke({"repo_path": repo_path})
    red = re.compile(r"\b(bug|security|vulnerab|missing|unhandled)\b", re.I)
    if red.search(review or ""):
        return f"HOLD: review flagged issues — not committing. Review:\n{review}"
    rc1, _, err1 = _run(["git", "add", "-A"], p)
    if rc1 != 0:
        return f"ERROR: git add failed — {err1.strip()}"
    rc2, out2, err2 = _run(
        ["git", "-c", "user.name=nexus", "-c", "user.email=nexus@wattbott.local",
         "commit", "-m", message],
        p,
    )
    if rc2 != 0:
        return f"ERROR: git commit failed — {(err2 or out2).strip()}"
    rc3, sha, _ = _run(["git", "rev-parse", "HEAD"], p)
    return (sha.strip()[:12] if rc3 == 0 else "committed") + "\n" + out2.strip()


DIFF_TOOLS = [get_diff, review_diff, approve_diff]

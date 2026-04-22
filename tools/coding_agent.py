"""Nexus autonomous coding loop.

Given (task, repo_path), `solve_coding_task` drives a full TDD-ish loop:
  1. index_codebase(repo) → RAG of the codebase
  2. plan the task with qwen3.6 → /tmp/nexus-plan.md
  3. run baseline tests
  4. loop (max_iterations):
        gather context (test output + search results + relevant files)
        ask qwen3.6 for a JSON edit plan
        apply edits (exact string replace)
        rerun tests
        break when all pass
  5. review the diff, stage + commit with descriptive message
  6. emit a Sparky card (and try a Telegram notify if wired)
  7. return a full text report

Designed to run headless — no interactive prompts."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import ollama

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.codebase_tool import index_codebase_raw, search_codebase  # noqa: E402
from tools.diff_tool import get_diff, review_diff  # noqa: E402
from tools.test_runner_tool import run_tests_raw  # noqa: E402

log = logging.getLogger("nexus.coding_agent")

OLLAMA_URL = "http://localhost:11434"
CODE_MODEL = "qwen3.6:latest"
PLAN_PATH = Path("/tmp/nexus-plan.md")
SESSIONS_DIR = Path.home() / "AI_Agent" / "memory" / "coding-sessions"

PLAN_SYSTEM = (
    "You are Nexus, a senior engineer planning a coding task. Break the "
    "task into explicit numbered steps. Keep it brief — 3-6 steps. Output "
    "plain markdown with a '# Plan' heading then numbered steps. No "
    "commentary, no code blocks."
)

EDIT_SYSTEM = (
    "You are Nexus, a senior engineer fixing a failing codebase. You get "
    "the task, the failing test output, a short codebase summary, and the "
    "contents of the most relevant files. Produce a strict JSON object "
    "with an `edits` array. Each edit is "
    "{\"file\": path, \"old_string\": exact text to replace, "
    "\"new_string\": new text, \"rationale\": one line why}. Keep edits "
    "minimal. `old_string` must match the file EXACTLY (copy verbatim); "
    "if it doesn't exist in the file the edit fails. Output JSON only, no "
    "markdown fences, no prose."
)

COMMIT_SYSTEM = (
    "Write a short, descriptive git commit message for the following diff. "
    "One line subject (<=72 chars), imperative mood, no prefix. Output only "
    "the subject line."
)


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def _run(args, cwd: Path, timeout: int = 30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"


def _resolve(path: str) -> Path:
    return Path(path).expanduser().resolve()


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _chat(messages, *, format: Optional[str] = None, num_predict: int = 900,
          num_ctx: int = 16_384, temperature: float = 0.2) -> str:
    try:
        kwargs = dict(
            model=CODE_MODEL,
            messages=messages,
            stream=False,
            think=False,
            options={"temperature": temperature, "num_predict": num_predict, "num_ctx": num_ctx},
        )
        if format:
            kwargs["format"] = format
        resp = ollama.Client(host=OLLAMA_URL).chat(**kwargs)
    except Exception as exc:
        log.warning("qwen3.6 chat failed: %s", exc)
        return ""
    content = resp["message"]["content"] if isinstance(resp, dict) else getattr(resp.message, "content", "")
    content = re.sub(r"<think>.*?</think>", "", content or "", flags=re.DOTALL | re.IGNORECASE).strip()
    return content


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------

def _relevant_files(task: str, test_output: str, repo: Path, limit: int = 5) -> list[Path]:
    """Pick files worth showing the LLM. Priority: files named in test
    failures, then semantic search hits."""
    picks: list[Path] = []
    seen: set[Path] = set()

    # 1. Paths mentioned in test output
    for m in re.finditer(r"([\w./\-]+\.(?:py|ts|tsx|js|jsx|go|rs))", test_output or ""):
        candidate = (repo / m.group(1)).resolve()
        if candidate.exists() and candidate.is_file() and candidate not in seen:
            picks.append(candidate)
            seen.add(candidate)
            if len(picks) >= limit:
                return picks

    # 2. Semantic search using the task string
    hits = search_codebase.invoke({"query": task, "repo_path": str(repo), "k": limit * 2})
    for ln in (hits or "").splitlines():
        m = re.search(r"\] ([\w./\-]+?\.\w+)", ln)
        if not m:
            continue
        candidate = (repo / m.group(1)).resolve()
        if candidate.exists() and candidate.is_file() and candidate not in seen:
            picks.append(candidate)
            seen.add(candidate)
            if len(picks) >= limit:
                break
    return picks


def _file_blob(repo: Path, path: Path) -> str:
    rel = path.relative_to(repo).as_posix() if path.is_absolute() else str(path)
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return f"### {rel}\n(unreadable: {exc})\n"
    if len(text) > 6000:
        text = text[:6000] + "\n# … (trimmed)"
    return f"### {rel}\n```\n{text}\n```"


# ---------------------------------------------------------------------------
# Edits
# ---------------------------------------------------------------------------

@dataclass
class EditResult:
    file: str
    ok: bool
    reason: str


def _apply_edit(repo: Path, edit: dict) -> EditResult:
    rel = edit.get("file", "").lstrip("/")
    target = (repo / rel).resolve()
    try:
        target.relative_to(repo)
    except ValueError:
        return EditResult(rel, False, "path outside repo")
    if not target.exists() or not target.is_file():
        # Allow new-file creation when old_string is empty.
        if not edit.get("old_string") and edit.get("new_string"):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit["new_string"], encoding="utf-8")
            return EditResult(rel, True, "new file")
        return EditResult(rel, False, "file not found")
    text = target.read_text(encoding="utf-8", errors="ignore")
    old = edit.get("old_string", "") or ""
    new = edit.get("new_string", "") or ""
    if old == new:
        return EditResult(rel, False, "old_string == new_string (no-op)")
    if old and old not in text:
        return EditResult(rel, False, "old_string not found verbatim")
    new_text = text.replace(old, new, 1) if old else text + new
    target.write_text(new_text, encoding="utf-8")
    return EditResult(rel, True, edit.get("rationale", "") or "applied")


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git_commit(repo: Path, message: str) -> tuple[bool, str]:
    rc1, _, err1 = _run(["git", "add", "-A"], repo)
    if rc1 != 0:
        return False, f"git add failed: {err1.strip()}"
    rc2, out2, err2 = _run(
        ["git", "-c", "user.name=nexus", "-c", "user.email=nexus@wattbott.local",
         "commit", "-m", message],
        repo,
    )
    if rc2 != 0:
        # Nothing to commit?
        if "nothing to commit" in (out2 + err2).lower():
            return False, "no changes to commit"
        return False, f"git commit failed: {(err2 or out2).strip()}"
    rc3, sha, _ = _run(["git", "rev-parse", "HEAD"], repo)
    return True, sha.strip()[:12]


def _current_branch(repo: Path) -> str:
    rc, out, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo)
    return out.strip() if rc == 0 else ""


# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------

def _sparky_card(repo: Path, title: str, subtitle: str) -> None:
    try:
        import httpx
        body = {"type": "message", "title": title, "subtitle": subtitle[:180]}
        with httpx.Client(timeout=2) as c:
            c.post("http://localhost:11437/card", json=body)
    except Exception:
        pass


def _telegram_notify(text: str) -> None:
    try:
        from tools.telegram_tool import notify_task_complete  # type: ignore
        notify_task_complete(text)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

def _session_log(tag: str) -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d-%H-%M")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", tag or "session")[:40]
    return SESSIONS_DIR / f"{stamp}-{safe}.md"


def solve_coding_task(
    task: str,
    repo_path: str,
    *,
    max_iterations: int = 10,
    do_commit: bool = True,
) -> str:
    repo = _resolve(repo_path)
    if not repo.exists() or not repo.is_dir():
        return f"ERROR: no such repo: {repo}"
    log_path = _session_log(repo.name)
    report: list[str] = []
    start_ts = time.time()

    def _say(s: str) -> None:
        report.append(s)
        log.info(s.splitlines()[0] if s else "")

    _say(f"# Nexus coding session — {repo.name}\n")
    _say(f"**Task:** {task}\n")
    _say(f"**Repo:** `{repo}`\n")
    _say(f"**Started:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 1. Index
    index_info = index_codebase_raw(str(repo))
    _say("## 1. Index\n")
    _say("```json\n" + json.dumps({k: v for k, v in index_info.items() if k != "path"}, indent=2) + "\n```\n")

    # 2. Plan
    plan = _chat(
        [{"role": "system", "content": PLAN_SYSTEM},
         {"role": "user", "content": f"TASK: {task}\n\nREPO SUMMARY: {index_info.get('languages')}\n"
                                     f"ENTRY POINTS: {index_info.get('entrypoints')}\n"
                                     f"ROUTES: {index_info.get('routes_found', 0)}\n"}],
        num_predict=400, temperature=0.3,
    )
    plan = plan or "# Plan\n1. Inspect the failing tests\n2. Apply the minimal fix\n3. Re-run tests"
    try:
        PLAN_PATH.write_text(plan + "\n", encoding="utf-8")
    except OSError:
        pass
    _say("## 2. Plan\n\n" + plan + "\n")

    # 3. Baseline tests
    baseline = run_tests_raw(str(repo))
    _say("## 3. Baseline tests\n")
    _say(f"- command: `{baseline.command}`  rc={baseline.returncode}  "
         f"passed={baseline.passed}  failed={baseline.failed}\n")
    if baseline.passed_all:
        _say("> All tests already pass — no fixes needed.\n")

    # 4. Loop
    _say("## 4. Iterations\n")
    iterations_done = 0
    final_result = baseline
    if not baseline.passed_all:
        for it in range(1, max_iterations + 1):
            iterations_done = it
            test_output = (final_result.stdout + "\n" + final_result.stderr)[-4000:]

            # Context
            files = _relevant_files(task, test_output, repo, limit=5)
            blobs = [_file_blob(repo, f) for f in files]
            user_msg = (
                f"TASK: {task}\n\n"
                f"FAILING TESTS ({final_result.failed}): {final_result.failing}\n\n"
                f"TEST OUTPUT (tail):\n```\n{test_output}\n```\n\n"
                f"RELEVANT FILES:\n\n" + "\n\n".join(blobs)
            )
            raw = _chat(
                [{"role": "system", "content": EDIT_SYSTEM},
                 {"role": "user", "content": user_msg}],
                format="json", num_predict=1100, temperature=0.15,
            )
            parsed = _parse_json(raw)
            edits = parsed.get("edits") or []
            _say(f"### Iteration {it}\n")
            _say(f"- files considered: {[f.relative_to(repo).as_posix() for f in files]}")
            _say(f"- edits proposed: {len(edits)}")
            if not edits:
                _say("- (no edits; LLM gave up)")
                break
            applied = [_apply_edit(repo, e) for e in edits]
            for a in applied:
                _say(f"  - {a.file}: {'ok' if a.ok else 'FAIL'} — {a.reason}")

            final_result = run_tests_raw(str(repo))
            _say(f"- after edits: rc={final_result.returncode}  "
                 f"passed={final_result.passed}  failed={final_result.failed}\n")
            if final_result.passed_all:
                _say("- ✅ all tests pass\n")
                break

    # 5. Diff + review
    _say("## 5. Diff review\n")
    diff_text = get_diff.invoke({"repo_path": str(repo)})
    if diff_text.startswith("ERROR") or diff_text.strip() in ("", "(no changes)"):
        _say("(no diff to review)\n")
    else:
        review = review_diff.invoke({"repo_path": str(repo)})
        _say("```\n" + review[:2500] + "\n```\n")

    # 6. Commit
    commit_sha = None
    if do_commit and diff_text and not diff_text.startswith("ERROR") and diff_text.strip() != "(no changes)":
        commit_msg = _chat(
            [{"role": "system", "content": COMMIT_SYSTEM},
             {"role": "user", "content": diff_text[:25_000]}],
            num_predict=60, temperature=0.2,
        )
        commit_msg = (commit_msg or f"nexus: {task[:60]}").strip().splitlines()[0][:72]
        ok, detail = _git_commit(repo, commit_msg)
        _say("## 6. Commit\n")
        if ok:
            commit_sha = detail
            _say(f"- committed `{commit_sha}` — {commit_msg}\n")
        else:
            _say(f"- skipped — {detail}\n")

    # 7. PR (best-effort on feature branches)
    branch = _current_branch(repo)
    if commit_sha and branch and branch not in ("main", "master"):
        try:
            from tools.github_tool import github_create_pr  # type: ignore
            remote_rc, remote, _ = _run(["git", "config", "--get", "remote.origin.url"], repo)
            if remote_rc == 0 and "github.com" in remote:
                slug = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git|$)", remote)
                if slug:
                    res = github_create_pr.invoke({
                        "repo": slug.group(1), "title": commit_msg,
                        "head": branch, "base": "main",
                        "body": f"Automated PR from Nexus coding agent.\n\nTask: {task}",
                    })
                    _say(f"## 7. PR\n- {res}\n")
        except Exception as exc:
            _say(f"## 7. PR\n- skipped: {type(exc).__name__}: {exc}\n")

    # 8. Sparky + Telegram notifications
    status = "✅ solved" if final_result.passed_all else "⚠️ unresolved"
    _sparky_card(repo, f"{status}: {repo.name}",
                 f"{task} — passed={final_result.passed} failed={final_result.failed}")
    _telegram_notify(f"Nexus {status} on {repo.name}: {task}")

    elapsed = int(time.time() - start_ts)
    _say("## Summary\n")
    _say(f"- iterations: {iterations_done}")
    _say(f"- final: passed={final_result.passed}  failed={final_result.failed}  "
         f"all_passed={final_result.passed_all}")
    _say(f"- elapsed: {elapsed}s")
    _say(f"- log: {log_path}")

    final_report = "\n".join(report) + "\n"
    try:
        log_path.write_text(final_report, encoding="utf-8")
    except OSError:
        pass
    return final_report


# ---------------------------------------------------------------------------
# LangGraph tool
# ---------------------------------------------------------------------------

from langchain_core.tools import tool as _tool


@_tool
def solve_task(task: str, repo_path: str, max_iterations: int = 10) -> str:
    """Autonomous coding loop. Indexes a git repo, plans the task with
    qwen3.6, iteratively edits + re-tests until all tests pass (or
    max_iterations is reached), reviews the diff, commits, opens a PR
    on feature branches, and emits a Sparky card."""
    return solve_coding_task(task, repo_path, max_iterations=int(max_iterations))


CODING_AGENT_TOOLS = [solve_task]

#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Nexus git-activity watcher.

Every 60 seconds, walks ~/Dev and ~/AI_Agent looking for git repositories,
records the HEAD commit of each, and logs any commit not seen before. Each
new commit is:
  - appended to ~/AI_Agent/memory/git-activity.log as one JSONL entry
  - summarized into Chroma RAG tagged `git_activity` for semantic recall

State (which SHAs we've already logged) lives in
~/AI_Agent/memory/git_watcher_seen.json so restarts don't re-ingest history."""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.rag_tool import add_documents  # noqa: E402

WATCH_ROOTS = [Path.home() / "Dev", Path.home() / "AI_Agent"]
POLL_SECONDS = 60
MAX_DEPTH = 3                          # how deep to scan for .git dirs
INITIAL_HISTORY = 0                    # commits per repo to backfill on first run
LOG_PATH = Path.home() / "AI_Agent" / "memory" / "git-activity.log"
STATE_PATH = Path.home() / "AI_Agent" / "memory" / "git_watcher_seen.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s nexus-git-watcher %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("nexus.git_watcher")


def _run(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        res = subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"
    return res.returncode, res.stdout, res.stderr


def _is_repo(p: Path) -> bool:
    return (p / ".git").exists()


def _find_repos() -> list[Path]:
    repos: list[Path] = []
    for root in WATCH_ROOTS:
        if not root.exists():
            continue
        if _is_repo(root):
            repos.append(root.resolve())
            continue
        for depth in range(1, MAX_DEPTH + 1):
            for candidate in root.glob("/".join(["*"] * depth)):
                if candidate.is_dir() and _is_repo(candidate):
                    repos.append(candidate.resolve())
    # de-dupe preserving order
    seen: set[Path] = set()
    out: list[Path] = []
    for r in repos:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _load_state() -> dict[str, list[str]]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, list[str]]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _commit_details(repo: Path, sha: str) -> dict:
    rc, out, _ = _run(
        ["git", "show", "-s", "--format=%H%x1f%an%x1f%ae%x1f%cI%x1f%s%x1f%b", sha],
        repo,
    )
    if rc != 0 or not out:
        return {"sha": sha}
    parts = out.rstrip("\n").split("\x1f")
    rec = {
        "sha": parts[0] if len(parts) > 0 else sha,
        "author": parts[1] if len(parts) > 1 else "",
        "email": parts[2] if len(parts) > 2 else "",
        "when": parts[3] if len(parts) > 3 else "",
        "subject": parts[4] if len(parts) > 4 else "",
        "body": parts[5] if len(parts) > 5 else "",
    }
    rc2, out2, _ = _run(
        ["git", "show", "--stat", "--format=", sha],
        repo,
    )
    if rc2 == 0:
        rec["files_changed"] = [ln.strip() for ln in out2.splitlines() if ln.strip()]
    return rec


def _recent_shas(repo: Path, limit: int) -> list[str]:
    if limit <= 0:
        return []
    rc, out, _ = _run(["git", "log", f"-n{limit}", "--format=%H"], repo)
    if rc != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _log_commit(repo: Path, rec: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "component": "git_watcher",
        "repo": repo.name,
        "repo_path": str(repo),
        "sha": rec.get("sha"),
        "subject": rec.get("subject"),
        "author": rec.get("author"),
        "when": rec.get("when"),
        "files_changed": rec.get("files_changed", []),
    }
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _rag_store(repo: Path, rec: dict) -> None:
    subject = rec.get("subject", "")
    body = rec.get("body", "")
    files = rec.get("files_changed", [])
    text_parts = [
        f"git commit in {repo.name} ({rec.get('sha','')[:8]}) by {rec.get('author','')}",
        f"subject: {subject}",
    ]
    if body:
        text_parts.append(f"body: {body}")
    if files:
        text_parts.append("files:\n" + "\n".join(files))
    text = "\n".join(text_parts)
    meta = {
        "tag": "git_activity",
        "repo": repo.name,
        "sha": rec.get("sha", ""),
        "author": rec.get("author", ""),
        "when": rec.get("when", ""),
    }
    try:
        add_documents([text], metadatas=[meta])
    except Exception as exc:
        log.warning("RAG store failed for %s %s: %s", repo.name, rec.get("sha", "")[:8], exc)


def _scan_once(state: dict[str, list[str]]) -> None:
    repos = _find_repos()
    for repo in repos:
        key = str(repo)
        rc, out, _ = _run(["git", "rev-parse", "HEAD"], repo)
        if rc != 0:
            continue
        head = out.strip()
        seen = state.get(key, [])
        if not seen:
            # First time seeing this repo — baseline: record current HEAD as
            # already seen (plus optional history backfill).
            history = _recent_shas(repo, INITIAL_HISTORY) or [head]
            state[key] = history
            log.info("baseline %s @ %s (%d shas)", repo.name, head[:8], len(history))
            continue
        if head in seen:
            continue
        # New HEAD — find every commit between oldest known seen and HEAD.
        ref_range = f"{seen[0]}..{head}"
        rc2, out2, _ = _run(["git", "log", "--format=%H", ref_range], repo)
        if rc2 != 0:
            new_shas = [head]
        else:
            new_shas = [ln.strip() for ln in out2.splitlines() if ln.strip()] or [head]
        # Log oldest → newest so the log reads chronologically.
        for sha in reversed(new_shas):
            rec = _commit_details(repo, sha)
            _log_commit(repo, rec)
            _rag_store(repo, rec)
            log.info("%s %s %s", repo.name, sha[:8], rec.get("subject", "")[:80])
        # Phase 11: re-index ~/Dev repos and refresh the cached NEXUS.md.
        try:
            from tools.repo_watcher import on_commit as _reindex
            _reindex(repo)
        except Exception as exc:
            log.debug("repo_watcher.on_commit failed for %s: %s", repo.name, exc)
        # Cap the remembered SHA list so it doesn't grow unbounded.
        keep = new_shas + seen
        state[key] = keep[:200]
    _save_state(state)


def main() -> None:
    stop = {"flag": False}

    def handle(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    state = _load_state()
    log.info("nexus-git-watcher starting; watching %s", [str(r) for r in WATCH_ROOTS])

    while not stop["flag"]:
        try:
            _scan_once(state)
        except Exception as exc:
            log.exception("scan failed: %s: %s", type(exc).__name__, exc)
        for _ in range(POLL_SECONDS):
            if stop["flag"]:
                break
            time.sleep(1)
    log.info("nexus-git-watcher stopping")


if __name__ == "__main__":
    main()

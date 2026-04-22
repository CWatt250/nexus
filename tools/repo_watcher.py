"""Repo watcher — auto re-indexes any repo under ~/Dev/ when a new commit
lands, and loads the repo's NEXUS.md into Nexus's active context.

This is a thin, importable module — the existing `tools/git_watcher.py`
daemon calls `on_commit(repo_path)` after it detects a new SHA, which
reuses the already-running `nexus-git-watcher` service."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from tools.codebase_tool import index_codebase_raw

log = logging.getLogger("nexus.repo_watcher")

DEV_ROOT = Path.home() / "Dev"
NEXUS_MD_CACHE_DIR = Path.home() / "AI_Agent" / "memory" / "nexus_md"


def _under_dev(repo: Path) -> bool:
    try:
        repo.resolve().relative_to(DEV_ROOT.resolve())
        return True
    except ValueError:
        return False


def _cache_nexus_md(repo: Path) -> None:
    md = repo / "NEXUS.md"
    if not md.exists():
        return
    NEXUS_MD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dst = NEXUS_MD_CACHE_DIR / f"{repo.name}.md"
    try:
        dst.write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
        log.info("cached NEXUS.md for %s → %s", repo.name, dst)
    except OSError as exc:
        log.warning("could not cache NEXUS.md for %s: %s", repo.name, exc)


def on_commit(repo_path: str | Path) -> None:
    """Fire-and-forget re-index of the repo. Runs in a daemon thread so it
    never blocks the caller (the git watcher poll)."""
    repo = Path(repo_path).resolve()
    # Only auto-index repos under ~/Dev/ — the Nexus workspace itself
    # ships its own git-activity log and doesn't benefit from re-indexing
    # every turn.
    if not _under_dev(repo):
        return

    def _work():
        try:
            result = index_codebase_raw(str(repo))
            if result.get("ok"):
                log.info(
                    "re-indexed %s: %d files, %d languages",
                    repo.name, result["indexed_files"], len(result.get("languages") or {}),
                )
                _cache_nexus_md(repo)
            else:
                log.warning("re-index %s failed: %s", repo.name, result.get("error"))
        except Exception as exc:
            log.exception("re-index %s crashed: %s", repo.name, exc)

    threading.Thread(target=_work, name=f"reindex-{repo.name}", daemon=True).start()


def load_active_context() -> str:
    """Concatenate every cached NEXUS.md so Nexus can inject project
    context into its system prompt. Returns '' if the cache is empty."""
    if not NEXUS_MD_CACHE_DIR.exists():
        return ""
    parts: list[str] = []
    for md in sorted(NEXUS_MD_CACHE_DIR.glob("*.md")):
        try:
            parts.append(f"# {md.stem}\n\n" + md.read_text(encoding="utf-8"))
        except OSError:
            continue
    return ("\n\n---\n\n".join(parts))[:20_000]

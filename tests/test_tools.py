"""Golden-path regression tests for ~15 core tools.

Offline-first: anything that needs network or external creds is skipped
unless the prerequisites are present. The point is a fast nightly canary
that catches refactors breaking the tool surface, not a coverage suite."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


# 1. file_read_tool
def test_file_read_tool(tmp_path: Path) -> None:
    from tools.file_tool import file_read_tool
    target = tmp_path / "hello.txt"
    target.write_text("hello phase14.5")
    out = file_read_tool.invoke({"path": str(target)})
    assert "hello phase14.5" in out


# 2. file_write_tool
def test_file_write_tool(tmp_path: Path) -> None:
    from tools.file_tool import file_write_tool
    target = tmp_path / "out.txt"
    file_write_tool.invoke({"path": str(target), "content": "written-by-test"})
    assert target.read_text() == "written-by-test"


# 3. file_edit_tool
def test_file_edit_tool(tmp_path: Path) -> None:
    from tools.file_tool import file_edit_tool
    target = tmp_path / "edit.txt"
    target.write_text("old-token here")
    file_edit_tool.invoke({"path": str(target), "old_string": "old-token", "new_string": "new-token"})
    assert "new-token" in target.read_text()


# 4. glob_tool
def test_glob_tool(tmp_path: Path) -> None:
    from tools.search_tool import glob_tool
    (tmp_path / "a.py").write_text("# a")
    (tmp_path / "b.py").write_text("# b")
    out = glob_tool.invoke({"pattern": "*.py", "root": str(tmp_path)})
    assert "a.py" in out and "b.py" in out


# 5. grep_tool
def test_grep_tool(tmp_path: Path) -> None:
    from tools.search_tool import grep_tool
    (tmp_path / "hit.txt").write_text("WANT_THIS_LINE\nfiller\n")
    out = grep_tool.invoke({"pattern": "WANT_THIS_LINE", "root": str(tmp_path), "glob": "**/*"})
    assert "WANT_THIS_LINE" in out


# 6. sandbox.run_guarded — safe command runs through
def test_run_guarded_safe() -> None:
    from safety.sandbox import run_guarded
    r = run_guarded("echo phase14.5-pass")
    assert r["returncode"] == 0
    assert "phase14.5-pass" in r["stdout"]
    assert not r.get("blocked")


# 7. sandbox.run_guarded — hard-blocked command refused
def test_run_guarded_hard_block() -> None:
    from safety.sandbox import run_guarded
    r = run_guarded("rm -rf /tmp/never-runs")
    assert r["blocked"] is True
    assert "rm" in r["reason"].lower() or "delete" in r["reason"].lower()


# 8. sandbox.run_guarded — soft-destructive dry-run
def test_run_guarded_dry_run() -> None:
    from safety.sandbox import run_guarded
    r = run_guarded("git reset --hard HEAD~1")
    assert r["blocked"] is True
    assert "DRY-RUN" in r["stdout"]


# 9. destructive.is_destructive coverage
def test_is_destructive_patterns() -> None:
    from safety.destructive import is_destructive
    assert is_destructive("git push --force origin main")[0]
    assert is_destructive("DROP TABLE users")[0]
    assert is_destructive("rm -rf /tmp/x")[0]
    assert not is_destructive("git status")[0]
    assert not is_destructive("echo hello")[0]


# 10. router.classify_and_model
def test_router_classify_fast() -> None:
    import router
    route, model = router.classify_and_model("hi")
    assert route in router.ROUTES
    assert isinstance(model, str) and model


# 11. truncate_tool_result short pass-through
def test_truncate_passthrough() -> None:
    from tools.truncate import truncate_tool_result
    s = "short string"
    assert truncate_tool_result(s) == s


# 12. instant_ack heuristic
def test_instant_ack_heuristic() -> None:
    from tools.sparky_state import looks_long_running
    assert looks_long_running("refactor the auth module") is True
    assert looks_long_running("hi") is False


# 13. parallel_tools.repo_inspect (offline)
def test_repo_inspect_offline(tmp_path: Path) -> None:
    from tools.parallel_tools import repo_inspect
    f = tmp_path / "x.py"
    f.write_text("def foo():\n    return 1\n")
    out = repo_inspect.invoke({"file_path": str(f), "git_n": 3})
    assert "FILE CONTEXT" in out and "GIT LOG" in out


# 14. metrics.record_tool_call writes JSONL
def test_metrics_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from memory import metrics
    fake = tmp_path / "tool.jsonl"
    monkeypatch.setattr(metrics, "TOOL_LOG", fake)
    metrics.record_tool_call(task_id="T", tool="x", latency_ms=1.0, success=True)
    assert fake.exists()
    assert '"tool": "x"' in fake.read_text()


# 15. lessons_aggregator extract+digest path with empty input
def test_lessons_extract_empty() -> None:
    from memory.lessons_aggregator import _extract_lessons, _digest
    assert _extract_lessons("# Retro\n\nno lessons here") == []
    # _digest on empty list returns the no-actionable message — no model call.
    assert "no actionable" in _digest([]).lower()


# Optional / skipped if creds missing
@pytest.mark.skipif(
    not os.getenv("BRAVE_SEARCH_API_KEY") and not Path.home().joinpath("AI_Agent/.env").read_text().find("BRAVE_SEARCH_API_KEY=") if Path.home().joinpath("AI_Agent/.env").exists() else True,
    reason="BRAVE_SEARCH_API_KEY not configured",
)
def test_brave_search_offline_safe() -> None:
    from tools.brave_search_tool import brave_search
    out = brave_search.invoke({"query": "openai", "count": 1})
    assert isinstance(out, str)

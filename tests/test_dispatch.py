"""Phase 22 — dispatch system tests.

Unit-level: round-trip of meta + body, risky pattern matching, approval
flow, queue summary. Integration: full dispatch → archived state via the
real watcher, but mocking the claude subprocess so we don't burn API
budget on every run.

Run with:
    cd ~/AI_Agent && venv/bin/python3 -m pytest tests/test_dispatch.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import cc_dispatch  # noqa: E402


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """Redirect every cc_dispatch path to a temp dir so tests don't
    pollute the real inbox/archive."""
    monkeypatch.setattr(cc_dispatch, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(cc_dispatch, "PENDING", tmp_path / "inbox" / ".pending")
    monkeypatch.setattr(cc_dispatch, "ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(cc_dispatch, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(cc_dispatch, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(cc_dispatch, "METRICS", tmp_path / "metrics")
    monkeypatch.setattr(cc_dispatch, "METRICS_LOG", tmp_path / "metrics" / "dispatches.jsonl")
    cc_dispatch.ensure_dirs()
    return tmp_path


def test_risky_detection_obvious():
    assert cc_dispatch.is_risky("drop database users")
    assert cc_dispatch.is_risky("git push --force origin main")
    assert cc_dispatch.is_risky("rm -rf /tmp/data")
    assert cc_dispatch.is_risky("deploy to PRODUCTION now")
    assert cc_dispatch.is_risky("skip tests please")


def test_risky_detection_clean():
    assert cc_dispatch.is_risky("add a /health endpoint to nexus_api.py") == ""
    assert cc_dispatch.is_risky("write unit tests for the dispatcher") == ""
    assert cc_dispatch.is_risky("refactor the search router") == ""


def test_meta_roundtrip(isolated_dirs):
    meta = cc_dispatch.DispatchMeta.new(
        label="add /health endpoint", time_budget_minutes=60,
    )
    body = "Add a /health endpoint that returns {'ok': True}."
    path = cc_dispatch.write_prompt(meta, body, pending=False)
    assert path.exists()

    meta2, body2 = cc_dispatch.read_prompt(path)
    assert meta2 is not None
    assert meta2.dispatch_id == meta.dispatch_id
    assert meta2.label == meta.label
    assert meta2.time_budget_minutes == 60
    assert body2.strip() == body.strip()


def test_pending_approval_flow(isolated_dirs):
    meta = cc_dispatch.DispatchMeta.new(
        label="risky test", time_budget_minutes=60,
        risky_match="drop database",
    )
    cc_dispatch.write_prompt(meta, "drop database users", pending=True)

    # Pending file lives in .pending/, not inbox/.
    assert cc_dispatch.find_pending(meta.dispatch_id) is not None
    assert cc_dispatch.find_inbox(meta.dispatch_id) is None
    assert len(cc_dispatch.list_inbox()) == 0
    assert len(cc_dispatch.list_pending()) == 1

    # Approve → moves to inbox.
    new_path = cc_dispatch.approve(meta.dispatch_id)
    assert new_path is not None
    assert cc_dispatch.find_pending(meta.dispatch_id) is None
    assert cc_dispatch.find_inbox(meta.dispatch_id) is not None


def test_cancel_pending(isolated_dirs):
    meta = cc_dispatch.DispatchMeta.new(
        label="cancel test", time_budget_minutes=60, risky_match="rm -rf",
    )
    cc_dispatch.write_prompt(meta, "rm -rf /tmp/junk", pending=True)
    cc_dispatch.cancel(meta.dispatch_id)

    # File moves out of pending into archive, plus a result file is written
    # so the reporter knows it was cancelled pre-flight.
    assert cc_dispatch.find_pending(meta.dispatch_id) is None
    archive_path = cc_dispatch.ARCHIVE / f"{meta.dispatch_id}.md"
    assert archive_path.exists()
    result = cc_dispatch.read_result(meta.dispatch_id)
    assert result is not None
    assert result.status == "cancelled"


def test_queue_summary_orders_by_mtime(isolated_dirs):
    ids = []
    for i in range(3):
        meta = cc_dispatch.DispatchMeta.new(label=f"job{i}", time_budget_minutes=30)
        cc_dispatch.write_prompt(meta, f"task {i}", pending=False)
        ids.append(meta.dispatch_id)
        time.sleep(0.01)

    snap = cc_dispatch.queue_summary()
    assert snap["queued_count"] == 3
    queued_ids = [q["dispatch_id"] for q in snap["queued"]]
    assert queued_ids == ids  # FIFO order


def test_lock_marks_running(isolated_dirs):
    cc_dispatch.write_lock("cc_abcdef12")
    snap = cc_dispatch.queue_summary()
    assert snap["running"] is not None
    assert snap["running"]["dispatch_id"] == "cc_abcdef12"
    cc_dispatch.clear_lock()
    snap2 = cc_dispatch.queue_summary()
    assert snap2["running"] is None


def test_cost_estimate_scales_with_duration():
    cost1, in1, out1 = cc_dispatch.estimate_cost(60)   # 1 min
    cost2, in2, out2 = cc_dispatch.estimate_cost(600)  # 10 min
    assert cost2 > cost1 * 5
    assert in2 > in1
    assert out2 > out1


def test_log_dispatch_writes_jsonl(isolated_dirs):
    meta = cc_dispatch.DispatchMeta.new(label="log test", time_budget_minutes=30)
    result = cc_dispatch.DispatchResult(
        dispatch_id=meta.dispatch_id, status="done",
        duration_seconds=42.5, commits_made=["fix: x", "feat: y"],
        files_changed=3, estimated_cost_usd=0.05,
    )
    cc_dispatch.log_dispatch(meta, result)

    lines = cc_dispatch.METRICS_LOG.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["dispatch_id"] == meta.dispatch_id
    assert rec["status"] == "done"
    assert rec["commits"] == 2


def test_month_spend_sums_only_this_month(isolated_dirs):
    cc_dispatch.METRICS.mkdir(parents=True, exist_ok=True)
    # Record from an old month should be ignored.
    cc_dispatch.METRICS_LOG.write_text(
        json.dumps({"ts": "2020-01-15T00:00:00+00:00", "estimated_cost_usd": 99.0}) + "\n"
        + json.dumps({"ts": "2020-01-16T00:00:00+00:00", "estimated_cost_usd": 99.0}) + "\n"
    )
    assert cc_dispatch.month_spend_usd() == 0.0

    # Now add a current-month record.
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    with cc_dispatch.METRICS_LOG.open("a") as f:
        f.write(json.dumps({"ts": now_iso, "estimated_cost_usd": 1.25}) + "\n")
    assert abs(cc_dispatch.month_spend_usd() - 1.25) < 0.001


def test_dispatch_tool_blocks_when_over_budget(isolated_dirs, monkeypatch):
    """Tool path: budget exhausted → no inbox file created, returns block message."""
    monkeypatch.setattr(cc_dispatch, "budget_status",
                         lambda *a, **kw: ("over", 60.0, 50.0))
    from tools import cc_dispatch_tool
    out = cc_dispatch_tool.dispatch_to_claude_code.invoke(
        {"prompt": "do something normal", "time_budget_minutes": 30}
    )
    assert "Blocked" in out
    assert len(cc_dispatch.list_inbox()) == 0


def test_dispatch_tool_pends_risky(isolated_dirs, monkeypatch):
    """Risky prompt → file lands in .pending/ instead of inbox/."""
    # Stub Telegram so the test doesn't try to hit the network.
    from tools import cc_dispatch_tool
    monkeypatch.setattr(cc_dispatch_tool, "_telegram_notify", lambda _t: None)
    out = cc_dispatch_tool.dispatch_to_claude_code.invoke(
        {"prompt": "drop database users", "time_budget_minutes": 30}
    )
    assert "Held for approval" in out
    assert len(cc_dispatch.list_pending()) == 1
    assert len(cc_dispatch.list_inbox()) == 0


def test_dispatch_tool_clean_path(isolated_dirs, monkeypatch):
    """Non-risky prompt → file lands in inbox/, ready for the watcher."""
    from tools import cc_dispatch_tool
    monkeypatch.setattr(cc_dispatch_tool, "_telegram_notify", lambda _t: None)
    out = cc_dispatch_tool.dispatch_to_claude_code.invoke(
        {"prompt": "add /health endpoint", "time_budget_minutes": 30,
         "label": "health endpoint"}
    )
    assert "Dispatched" in out
    assert "dispatch_id=cc_" in out
    assert len(cc_dispatch.list_inbox()) == 1


def test_restart_tool_refuses_non_nexus():
    from tools import restart_services_tool
    out = restart_services_tool.nexus_restart_services.invoke(
        {"services": "sshd,httpd", "dry_run": True}
    )
    assert "refused" in out.lower()


def test_restart_tool_dry_run_default_set():
    from tools import restart_services_tool
    out = restart_services_tool.nexus_restart_services.invoke({"services": "", "dry_run": True})
    assert "dry-run" in out
    assert "nexus-api" in out
    assert "nexus-cc-dispatcher" in out

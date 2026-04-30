#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Phase 22 — Claude Code dispatcher daemon.

Watches `cc_inbox/` for new prompt files and runs them through
`claude --dangerously-skip-permissions` one at a time, capturing output
to `cc_logs/<id>.log`. Enforces the per-dispatch time budget, sends a
heads-up at 80%, and SIGTERM/SIGKILLs at 100%.

When a dispatch finishes (clean exit, kill, or inactivity timeout) the
daemon writes `cc_results/<id>.json` so the reporter daemon can fan
the outcome to Telegram and the dashboard.

Runs as `nexus-cc-dispatcher.service` (Restart=always). Stop with
SIGTERM — the daemon finishes the current dispatch's cleanup before
exiting.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import cc_dispatch, event_bus  # noqa: E402

POLL_SECONDS = 2.0
INACTIVITY_KILL_SECONDS = 5 * 60  # log dead for 5 min → assume stuck
TERM_GRACE_SECONDS = 10           # SIGTERM → wait → SIGKILL

log = logging.getLogger("nexus.cc_dispatcher")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_repo_head() -> str:
    """Return current AI_Agent git HEAD short-sha, or '' on failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def _git_commits_since(start_sha: str) -> list[str]:
    """List commit subjects after `start_sha` on the AI_Agent repo."""
    if not start_sha:
        return []
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "log",
             f"{start_sha}..HEAD", "--pretty=format:%s"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return []
        return [l for l in proc.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def _git_files_changed_since(start_sha: str) -> int:
    if not start_sha:
        return 0
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "diff", "--name-only", f"{start_sha}..HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return 0
        return len([l for l in proc.stdout.splitlines() if l.strip()])
    except Exception:
        return 0


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _summarize_log_tail(log_path: Path) -> str:
    """Pluck a one-liner from the last lines of the log. Strips ANSI.
    Falls back to the last non-empty line if no obvious summary."""
    if not log_path.exists():
        return ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = _ANSI_RE.sub("", text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return ""
    # Look for a line that reads like a summary — e.g. "✓ Done", "Tests
    # passed", "Successfully committed". Otherwise just take the tail.
    for line in reversed(lines[-30:]):
        low = line.lower()
        if any(k in low for k in ("done", "complete", "summary", "tests pass", "committed")):
            return line[:200]
    return lines[-1][:200]


def _error_tail(log_path: Path, n: int = 100) -> str:
    if not log_path.exists():
        return ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = _ANSI_RE.sub("", text)
    return text.strip()[-n:]


def _spawn_claude(prompt_body: str, log_path: Path) -> subprocess.Popen:
    """Start `claude --dangerously-skip-permissions` with the prompt on
    stdin and stdout+stderr piped to log_path."""
    log_fh = log_path.open("wb", buffering=0)
    claude_bin = shutil.which("claude") or "/usr/local/bin/claude"
    proc = subprocess.Popen(
        [claude_bin, "--dangerously-skip-permissions", "--print"],
        stdin=subprocess.PIPE,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
        start_new_session=True,
        env={**os.environ, "CI": "1"},
    )
    if proc.stdin:
        try:
            proc.stdin.write(prompt_body.encode("utf-8"))
            proc.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
    return proc


def _kill_tree(proc: subprocess.Popen) -> None:
    """SIGTERM, wait TERM_GRACE_SECONDS, then SIGKILL the entire process
    group (claude can spawn helpers — kill the whole session)."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + TERM_GRACE_SECONDS
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.5)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _file_idle_seconds(path: Path) -> float:
    if not path.exists():
        return 0.0
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return 0.0


def _remote_publish(event: str, **fields) -> None:
    try:
        event_bus.publish_remote(event, **fields)
    except Exception:
        pass


def _telegram(text: str) -> None:
    """Best-effort proactive Telegram from a worker process."""
    try:
        from tools import telegram_tool  # noqa: PLC0415
        telegram_tool.notify_sync(text)
    except Exception as exc:
        log.debug("telegram notify failed: %s", exc)


def _run_one(prompt_path: Path, stop_event_check) -> None:
    """Process a single inbox file. Always writes a result + archives the
    prompt, even on crash."""
    meta, body = cc_dispatch.read_prompt(prompt_path)
    if meta is None:
        log.warning("skipping malformed prompt: %s", prompt_path)
        prompt_path.rename(cc_dispatch.ARCHIVE / (prompt_path.stem + ".malformed.md"))
        return

    cc_dispatch.write_lock(meta.dispatch_id)
    log_path = cc_dispatch.LOGS / f"{meta.dispatch_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    started_iso = _now_iso()
    started_mono = time.monotonic()
    head_before = _git_repo_head()
    budget_seconds = max(60, meta.time_budget_minutes * 60)
    eighty_pct = budget_seconds * 0.8
    eighty_pct_sent = False

    log.info("dispatch %s starting (budget=%dm)", meta.dispatch_id, meta.time_budget_minutes)
    _remote_publish("cc_dispatch_started", dispatch_id=meta.dispatch_id,
                    label=meta.label, time_budget_minutes=meta.time_budget_minutes)
    _telegram(
        f"⚙️ Starting `{meta.dispatch_id}` — {meta.label} "
        f"(budget {meta.time_budget_minutes}m)"
    )

    proc = _spawn_claude(body, log_path)
    killed_by_timeout = False
    killed_by_inactivity = False
    exit_code: Optional[int] = None

    try:
        while True:
            if stop_event_check():
                log.info("dispatcher shutting down — killing %s", meta.dispatch_id)
                _kill_tree(proc)
                break
            ret = proc.poll()
            if ret is not None:
                exit_code = ret
                break
            elapsed = time.monotonic() - started_mono
            if not eighty_pct_sent and elapsed >= eighty_pct:
                eighty_pct_sent = True
                pct_left = max(0, meta.time_budget_minutes - int(elapsed / 60))
                _telegram(
                    f"⏱️ `{meta.dispatch_id}` — {meta.label} at 80% of budget "
                    f"({pct_left}m left)."
                )
                _remote_publish("cc_dispatch_warn80",
                                dispatch_id=meta.dispatch_id,
                                elapsed_seconds=round(elapsed, 1))
            if elapsed >= budget_seconds:
                log.warning("dispatch %s hit budget — killing", meta.dispatch_id)
                killed_by_timeout = True
                _kill_tree(proc)
                exit_code = proc.returncode
                break
            if _file_idle_seconds(log_path) >= INACTIVITY_KILL_SECONDS:
                log.warning("dispatch %s log idle %ds — killing",
                            meta.dispatch_id, INACTIVITY_KILL_SECONDS)
                killed_by_inactivity = True
                _kill_tree(proc)
                exit_code = proc.returncode
                break
            time.sleep(POLL_SECONDS)
    except Exception as exc:
        log.exception("dispatcher loop crashed: %s", exc)
        _kill_tree(proc)
        exit_code = -1

    duration = time.monotonic() - started_mono
    commits = _git_commits_since(head_before)
    files_changed = _git_files_changed_since(head_before)
    summary = _summarize_log_tail(log_path)
    err_tail = ""

    if killed_by_timeout:
        status = "timeout"
    elif killed_by_inactivity:
        status = "failed"
        err_tail = "killed: log inactivity (5+ min)"
    elif exit_code == 0:
        status = "done"
    else:
        status = "failed"
        err_tail = _error_tail(log_path)

    cost_usd, in_tok, out_tok = cc_dispatch.estimate_cost(duration)

    result = cc_dispatch.DispatchResult(
        dispatch_id=meta.dispatch_id,
        status=status,
        exit_code=exit_code,
        duration_seconds=round(duration, 2),
        started_at=started_iso,
        finished_at=_now_iso(),
        killed_by_timeout=killed_by_timeout,
        commits_made=commits,
        files_changed=files_changed,
        one_line_summary=summary,
        error_tail=err_tail[:500],
        estimated_cost_usd=cost_usd,
        estimated_input_tokens=in_tok,
        estimated_output_tokens=out_tok,
    )
    cc_dispatch.write_result(result)
    cc_dispatch.archive_after_run(meta.dispatch_id)
    cc_dispatch.clear_lock()
    cc_dispatch.log_dispatch(meta, result)

    _remote_publish(
        "cc_dispatch_finished",
        dispatch_id=meta.dispatch_id,
        label=meta.label,
        status=status,
        duration_seconds=result.duration_seconds,
        commits=len(commits),
        files_changed=files_changed,
    )

    log.info("dispatch %s finished status=%s commits=%d duration=%.1fs",
             meta.dispatch_id, status, len(commits), duration)


def _main_loop() -> None:
    cc_dispatch.ensure_dirs()
    log.info("cc_dispatcher ready (pid=%d, root=%s)", os.getpid(), ROOT)
    stop = {"flag": False}

    def _handler(_signum, _frame):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)

    while not stop["flag"]:
        files = cc_dispatch.list_inbox()
        if not files:
            time.sleep(POLL_SECONDS)
            continue
        # Skip the lock file's owning prompt if any (shouldn't happen, but guard).
        for p in files:
            if stop["flag"]:
                break
            try:
                _run_one(p, lambda: stop["flag"])
            except Exception as exc:
                log.exception("run_one crashed for %s: %s", p, exc)
                cc_dispatch.clear_lock()
            break  # one at a time — re-check inbox order each loop
        if not stop["flag"]:
            time.sleep(POLL_SECONDS)

    cc_dispatch.clear_lock()
    log.info("cc_dispatcher exiting cleanly")


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    _main_loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

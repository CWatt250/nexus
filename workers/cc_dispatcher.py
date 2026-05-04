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
# Safety fallback when a queued prompt's metadata is missing/garbage —
# never kill before 30 minutes. The tool path passes time_budget_minutes
# explicitly (default 30 there too); this only fires for hand-written
# inbox files or upgrade-mid-flight cases.
DEFAULT_TIME_BUDGET_MINUTES = 30
INACTIVITY_KILL_SECONDS = 10 * 60  # log dead for 10 min → assume stuck
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

# Phase 28 — slash-built dispatches inject a "Write the output to
# ~/AI_Agent/games/<slug>.html" hint into the prompt body. We pluck
# that path back out so the reporter can auto-attach the file to
# Telegram. Match a leading "Write the ... output to <path>" line.
_TARGET_PATH_RE = re.compile(
    r"Write\s+the\s+(?:complete,\s+self-contained\s+)?output\s+to\s+(\S+\.\w{1,5})",
    re.IGNORECASE,
)


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


# Phase 28 + 29 — tier → ~/.claude-* env file the launcher sources
# before exec'ing claude.
#   flash/pro → DeepSeek Anthropic-compatible endpoint
#   api       → real Anthropic via API key (renamed from "real" in P29)
#   max       → NO env file sourced — claude uses the user's Max plan
#               auth straight from ~/.claude/ (Phase 29 default for
#               complex builds, $0 marginal cost)
_TIER_ENV_FILE = {
    "flash": "~/.claude-deepseek-flash",
    "pro":   "~/.claude-deepseek-pro",
    "api":   "~/.claude-anthropic",
    # Legacy alias kept readable for any pre-Phase-29 inbox files that
    # land mid-rollout. New writes always carry tier="api".
    "real":  "~/.claude-anthropic",
}


def _load_secret(name: str) -> str:
    """Read a key from config/secrets.yaml. Best-effort, returns ''."""
    try:
        from core import secrets as _secrets  # noqa: PLC0415
        return _secrets.get(name) or ""
    except Exception:
        return ""


def _build_dispatch_env(tier: str) -> dict[str, str]:
    """Compose the subprocess env. The ~/.claude-* env files reference
    ${DEEPSEEK_API_KEY} / ${REAL_ANTHROPIC_KEY}; we resolve those from
    secrets.yaml before sourcing so the subprocess sees concrete tokens
    (the dispatcher service runs without an interactive bash, so the
    .bashrc exports aren't loaded).

    Phase 29 — for tier='max' we DROP every Anthropic-related key from
    the inherited env so claude falls through to the Max plan auth
    stored in ~/.claude/ (.credentials.json). Otherwise a stray
    ANTHROPIC_API_KEY from the parent shell would silently switch the
    subprocess to API billing."""
    env = {**os.environ, "CI": "1"}
    if tier == "max":
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                  "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
                  "ANTHROPIC_DEFAULT_OPUS_MODEL",
                  "ANTHROPIC_DEFAULT_SONNET_MODEL",
                  "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                  "CLAUDE_CODE_SUBAGENT_MODEL"):
            env.pop(k, None)
        return env
    env["DEEPSEEK_API_KEY"] = _load_secret("DEEPSEEK_API_KEY")
    env["REAL_ANTHROPIC_KEY"] = _load_secret("ANTHROPIC_API_KEY")
    return env


def _spawn_claude(prompt_body: str, log_path: Path,
                  tier: str = "api") -> subprocess.Popen:
    """Start `claude --dangerously-skip-permissions` with the prompt on
    stdin and stdout+stderr piped to log_path.

    Phase 29 tier dispatch:
      max          — exec claude directly, no env file sourced. claude
                     reads ~/.claude/ for Max plan auth.
      flash / pro  — source ~/.claude-deepseek-* before exec → DeepSeek
                     via Anthropic-compatible endpoint.
      api          — source ~/.claude-anthropic before exec → real
                     Anthropic via API key.
      real         — alias for api (back-compat for in-flight prompts).
    """
    log_fh = log_path.open("wb", buffering=0)
    canonical = cc_dispatch.normalize_tier(tier) or "api"
    if canonical == "max":
        # Phase 29 — Max plan path. Skip env-file source so claude picks
        # up Colton's existing ~/.claude/ session credentials. Same
        # bash-c wrapper as the other tiers so killpg / signal-handling
        # behaviour is uniform across the dispatcher.
        cmd = [
            "bash", "-c",
            "exec claude --dangerously-skip-permissions --print",
        ]
    else:
        env_file = _TIER_ENV_FILE.get(canonical, _TIER_ENV_FILE["api"])
        cmd = [
            "bash", "-c",
            f"source {env_file} 2>/dev/null && exec claude "
            f"--dangerously-skip-permissions --print",
        ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
        start_new_session=True,
        env=_build_dispatch_env(canonical),
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


def _run_local_qwen(prompt_body: str, log_path: Path,
                    budget_seconds: float, stop_event_check) -> tuple[int, bool, bool]:
    """Phase 28 — tier='local'. Calls qwen3-coder:30b via Ollama instead
    of spawning claude. Streams output into log_path so the same
    inactivity / budget machinery in _run_one applies. Returns
    (exit_code, killed_by_timeout, killed_by_inactivity)."""
    log_fh = log_path.open("w", encoding="utf-8")
    started = time.monotonic()
    last_chunk = started
    killed_timeout = False
    killed_inactivity = False
    exit_code = 0
    try:
        import ollama  # noqa: PLC0415
    except Exception as exc:
        log_fh.write(f"[local-tier] ollama package missing: {exc}\n")
        log_fh.close()
        return 1, False, False
    try:
        client = ollama.Client(host="http://localhost:11434")
        stream = client.chat(
            model="qwen3-coder:30b",
            messages=[
                {"role": "system", "content": (
                    "You are a senior software engineer. Output complete, working code "
                    "with brief explanations. No markdown fences around full files."
                )},
                {"role": "user", "content": prompt_body},
            ],
            stream=True,
            think=False,
            keep_alive=300,
            options={"temperature": 0.4, "num_ctx": 16384, "num_predict": 8192},
        )
        for chunk in stream:
            if stop_event_check():
                killed_inactivity = False
                exit_code = -1
                break
            elapsed = time.monotonic() - started
            if elapsed >= budget_seconds:
                killed_timeout = True
                exit_code = -1
                log_fh.write("\n[local-tier] killed: time budget exceeded\n")
                break
            content = (chunk.get("message", {}) or {}).get("content", "")
            if content:
                log_fh.write(content)
                log_fh.flush()
                last_chunk = time.monotonic()
            if (time.monotonic() - last_chunk) > INACTIVITY_KILL_SECONDS:
                killed_inactivity = True
                exit_code = -1
                log_fh.write("\n[local-tier] killed: inactivity\n")
                break
    except Exception as exc:
        log_fh.write(f"\n[local-tier] failed: {type(exc).__name__}: {exc}\n")
        exit_code = 1
    finally:
        log_fh.close()
    return exit_code, killed_timeout, killed_inactivity


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


def _detect_artifact_paths(prompt_body: str, started_iso: str) -> list[str]:
    """Phase 28 — find files claude (or qwen) wrote during this dispatch.
    Two sources:
      1. Explicit target path injected by `_enqueue_tiered_dispatch`
         (prepended "Write the output to <path>" hint).
      2. Recently-modified files under ~/AI_Agent/games/ (the conventional
         slash-build landing zone).
    Returns absolute paths, deduped, capped at 10."""
    out: list[str] = []
    seen: set[str] = set()

    m = _TARGET_PATH_RE.search(prompt_body or "")
    if m:
        candidate = Path(m.group(1)).expanduser()
        if candidate.exists() and candidate.is_file():
            p = str(candidate.resolve())
            if p not in seen:
                seen.add(p)
                out.append(p)

    try:
        started_epoch = datetime.fromisoformat(started_iso).timestamp()
    except (ValueError, TypeError):
        started_epoch = time.time() - 600  # last 10 min as a sane fallback

    games_dir = Path.home() / "AI_Agent" / "games"
    if games_dir.exists():
        for f in sorted(games_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not f.is_file():
                continue
            if f.stat().st_mtime < started_epoch:
                continue
            p = str(f.resolve())
            if p in seen:
                continue
            seen.add(p)
            out.append(p)
            if len(out) >= 10:
                break
    return out


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
    # Fall back to DEFAULT_TIME_BUDGET_MINUTES if the meta header is
    # missing/zeroed; clamp lowest valid value to 60 seconds for sanity.
    budget_minutes = meta.time_budget_minutes or DEFAULT_TIME_BUDGET_MINUTES
    budget_seconds = max(60, budget_minutes * 60)
    eighty_pct = budget_seconds * 0.8
    eighty_pct_sent = False

    # Phase 28 + 29 — pre-flight cost ceiling check. Tier-specific:
    #   max / local / quick → uncapped (no marginal cost)
    #   flash / pro / api   → per-tier ceiling from cost_limits.yaml
    #   per-day ceiling     → applies only to PAID_TIERS
    # Fall back gracefully so a missing yaml never blocks dispatch.
    tier = cc_dispatch.normalize_tier(getattr(meta, "tier", "api") or "api")
    projected_max, _, _ = cc_dispatch.estimate_cost(budget_seconds, tier)
    tier_ceiling = cc_dispatch.per_dispatch_ceiling(tier)
    if tier_ceiling is not None and projected_max > tier_ceiling:
        msg = (
            f"⛔ `{meta.dispatch_id}` — {meta.label} refused: projected cost "
            f"${projected_max:.3f} > {tier} ceiling ${tier_ceiling:.2f}. "
            f"Edit ~/AI_Agent/config/cost_limits.yaml to raise, or use /max "
            f"(uncapped, covered by Max plan)."
        )
        _telegram(msg)
        result_blocked = cc_dispatch.DispatchResult(
            dispatch_id=meta.dispatch_id, status="failed",
            error_tail=f"blocked: {tier} per-dispatch cost ceiling exceeded",
            tier=tier, model_used=cc_dispatch.TIER_MODELS.get(tier, ""),
            started_at=started_iso, finished_at=_now_iso(),
        )
        cc_dispatch.write_result(result_blocked)
        cc_dispatch.archive_after_run(meta.dispatch_id)
        cc_dispatch.clear_lock()
        cc_dispatch.log_dispatch(meta, result_blocked)
        return
    if cc_dispatch.is_paid_tier(tier):
        day_limit = cc_dispatch.get_cost_limits()["per_day_usd"]
        day_so_far = cc_dispatch.day_spend_usd()
        if day_so_far >= day_limit:
            msg = (
                f"⛔ `{meta.dispatch_id}` — {meta.label} refused: paid-tier "
                f"daily spend ${day_so_far:.2f} ≥ ${day_limit:.2f}. "
                f"Resets at UTC midnight, or use /max to bypass."
            )
            _telegram(msg)
            result_blocked = cc_dispatch.DispatchResult(
                dispatch_id=meta.dispatch_id, status="failed",
                error_tail="blocked: paid-tier daily cost ceiling exceeded",
                tier=tier, model_used=cc_dispatch.TIER_MODELS.get(tier, ""),
                started_at=started_iso, finished_at=_now_iso(),
            )
            cc_dispatch.write_result(result_blocked)
            cc_dispatch.archive_after_run(meta.dispatch_id)
            cc_dispatch.clear_lock()
            cc_dispatch.log_dispatch(meta, result_blocked)
            return

    log.info("dispatch %s starting (budget=%dm tier=%s)",
             meta.dispatch_id, meta.time_budget_minutes, tier)
    _remote_publish("cc_dispatch_started", dispatch_id=meta.dispatch_id,
                    label=meta.label, time_budget_minutes=meta.time_budget_minutes,
                    tier=tier)
    _telegram(
        f"⚙️ Starting `{meta.dispatch_id}` — {meta.label} "
        f"(tier {tier}, budget {meta.time_budget_minutes}m)"
    )

    killed_by_timeout = False
    killed_by_inactivity = False
    exit_code: Optional[int] = None
    proc: Optional[subprocess.Popen] = None

    if tier == "local":
        # No subprocess to poll — _run_local_qwen handles its own
        # streaming + budget/inactivity/stop checks and writes the
        # log inline.
        try:
            exit_code, killed_by_timeout, killed_by_inactivity = _run_local_qwen(
                body, log_path, budget_seconds, stop_event_check
            )
        except Exception as exc:
            log.exception("local-tier dispatch crashed: %s", exc)
            exit_code = -1
    else:
        proc = _spawn_claude(body, log_path, tier=tier)
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

    cost_usd, in_tok, out_tok = cc_dispatch.estimate_cost(duration, tier)

    artifacts = _detect_artifact_paths(body, started_iso)
    needs_review = False
    review_notes = ""
    # Phase 28 — visual verification step. For HTML artifacts, screenshot
    # via Playwright and ask qwen2.5vl whether anything looks broken.
    # Best-effort: any verification failure leaves needs_review=False
    # and the dispatch reports normally.
    html_artifacts = [p for p in artifacts if p.lower().endswith((".html", ".htm"))]
    if html_artifacts and status == "done":
        try:
            from tools import visual_verify  # noqa: PLC0415
            verdict = visual_verify.verify_html_artifact(html_artifacts[0])
            needs_review = bool(verdict.get("needs_review"))
            review_notes = verdict.get("notes", "") or ""
            screenshot_path = verdict.get("screenshot_path", "")
            if screenshot_path:
                artifacts.append(screenshot_path)
        except Exception as exc:
            log.warning("visual_verify failed for %s: %s", html_artifacts[0], exc)

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
        tier=tier,
        model_used=cc_dispatch.TIER_MODELS.get(tier, ""),
        artifact_paths=artifacts,
        needs_review=needs_review,
        review_notes=review_notes,
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

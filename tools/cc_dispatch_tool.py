"""Phase 22.1 — dispatch_to_claude_code tool.

Hand a prompt to a background Claude Code session running on this box,
return a dispatch_id immediately, and let the watcher daemon
(workers/cc_dispatcher.py) and the result reporter handle the rest.

Tagged MEDIUM tier in TOOLS.md — uses real Claude API budget, runs
arbitrary code, can take hours. Nexus should only call this when Colton
explicitly asks for delegation, not for quick edits."""
from __future__ import annotations

from langchain_core.tools import tool

from core import cc_dispatch
from tools import telegram_tool


def _telegram_notify(text: str) -> None:
    """Best-effort proactive Telegram. Never raises — this tool returns
    quickly even if Telegram is down."""
    try:
        telegram_tool.notify_sync(text)
    except Exception:
        pass


@tool
def dispatch_to_claude_code(
    prompt: str,
    time_budget_minutes: int = 120,
    label: str = "",
) -> str:
    """Hand a coding/research prompt to a background Claude Code session.

    The prompt is queued in cc_inbox/, picked up by the dispatcher daemon,
    and run with `claude --dangerously-skip-permissions`. Returns
    immediately with a dispatch_id. Telegram gets a notification when the
    job finishes (success, failure, or timeout).

    Use this for tasks that should run autonomously while Colton does
    something else — building a feature, fixing a bug, writing tests,
    refactoring. NOT for quick lookups or single-file edits.

    Risky prompts (containing 'production', 'force push', 'rm -rf',
    'drop database', etc.) are held in cc_inbox/.pending/ and require
    Telegram approval ('go cc_xxx') before they actually run.

    Args:
        prompt: The full task prompt to hand to Claude Code.
        time_budget_minutes: Hard kill at this many minutes (default 120,
            range 5-480). 80% mark sends a Telegram heads-up.
        label: Short human-readable name shown in notifications.

    Returns:
        Status line including dispatch_id, queue position, and approval
        state if applicable.
    """
    if not prompt or not prompt.strip():
        return "Error: empty prompt — nothing to dispatch."
    minutes = max(5, min(int(time_budget_minutes or 120), 480))

    # Budget gate before we touch the queue.
    level, spend, budget = cc_dispatch.budget_status()
    if level == "over":
        return (
            f"Blocked: monthly Claude Code budget exhausted "
            f"(${spend:.2f}/${budget:.2f}). Reply 'force dispatch: <prompt>' "
            f"or raise CLAUDE_CODE_MONTHLY_BUDGET in secrets.yaml."
        )

    risky = cc_dispatch.is_risky(prompt)
    meta = cc_dispatch.DispatchMeta.new(
        label=label or _summarize_label(prompt),
        time_budget_minutes=minutes,
        risky_match=risky,
    )
    cc_dispatch.write_prompt(meta, prompt, pending=bool(risky))

    snap = cc_dispatch.queue_summary()
    # `queued_count` includes the file we just wrote, so subtract self
    # to report "jobs ahead of you".
    ahead = max(0, snap["queued_count"] - 1)
    running = snap["running"]
    approx_start = ""
    if running and ahead > 0:
        approx_start = f" | running job + {ahead} ahead"
    elif running:
        approx_start = " | will start when current job finishes"
    elif ahead > 0:
        approx_start = f" | {ahead} ahead in queue"

    if risky:
        _telegram_notify(
            f"🚨 Risky prompt held for approval (matched: `{risky}`).\n"
            f"dispatch_id: `{meta.dispatch_id}` — label: {meta.label}\n"
            f"Reply `go {meta.dispatch_id}` to dispatch or `cancel "
            f"{meta.dispatch_id}` to abort."
        )
        return (
            f"Held for approval. dispatch_id={meta.dispatch_id} "
            f"(matched risky pattern: {risky!r}). "
            f"Reply 'go {meta.dispatch_id}' on Telegram to release it."
        )

    _telegram_notify(
        f"🚀 Dispatched. id `{meta.dispatch_id}` — {meta.label} "
        f"(budget {minutes}m){approx_start}."
    )
    warn_suffix = ""
    if level in ("warn50", "warn80"):
        pct = int(round(spend / budget * 100)) if budget else 0
        warn_suffix = f" (budget {pct}% used: ${spend:.2f}/${budget:.2f})"
    return (
        f"Dispatched. dispatch_id={meta.dispatch_id} budget={minutes}m"
        f"{approx_start}{warn_suffix}"
    )


def _summarize_label(prompt: str) -> str:
    """First sentence of the prompt, capped at 60 chars. Cheap, no LLM."""
    s = prompt.strip().splitlines()[0] if prompt else ""
    s = s.strip().rstrip(".")
    if len(s) > 60:
        s = s[:57] + "…"
    return s or "(unlabeled)"


CC_DISPATCH_TOOLS = [dispatch_to_claude_code]

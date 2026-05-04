#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Phase 22.3 — CC dispatch result reporter.

Watches `cc_results/` for new JSON files and fans the outcome to
Telegram + the dashboard event bus. Tracks which dispatches it has
already reported via `cc_results/.reported` so a daemon restart
doesn't double-notify.

Runs as `nexus-cc-reporter.service` (Restart=always)."""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import cc_dispatch, event_bus  # noqa: E402

REPORTED_INDEX = cc_dispatch.RESULTS / ".reported"
POLL_SECONDS = 3.0

log = logging.getLogger("nexus.cc_reporter")


def _load_reported() -> set[str]:
    if not REPORTED_INDEX.exists():
        return set()
    try:
        return set(line.strip() for line in REPORTED_INDEX.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return set()


def _mark_reported(dispatch_id: str) -> None:
    REPORTED_INDEX.parent.mkdir(parents=True, exist_ok=True)
    with REPORTED_INDEX.open("a", encoding="utf-8") as f:
        f.write(dispatch_id + "\n")


def _format_telegram(meta_label: str, result: cc_dispatch.DispatchResult) -> str:
    """Compose the user-facing Telegram message for one dispatch."""
    dur_min = result.duration_seconds / 60
    cost = f"~${result.estimated_cost_usd:.3f}" if result.estimated_cost_usd else ""
    tier_line = ""
    if getattr(result, "tier", "") and getattr(result, "model_used", ""):
        tier_line = f"\nTier: {result.tier} ({result.model_used})"
    if result.status == "done":
        commits_line = ""
        if result.commits_made:
            preview = ", ".join(result.commits_made[:3])
            if len(preview) > 200:
                preview = preview[:200] + "…"
            commits_line = f"\n{len(result.commits_made)} commit(s): {preview}"
        files_line = f"\nFiles changed: {result.files_changed}" if result.files_changed else ""
        artifact_line = ""
        artifacts = getattr(result, "artifact_paths", []) or []
        if artifacts:
            names = [Path(p).name for p in artifacts[:5]]
            artifact_line = f"\nArtifacts: {', '.join(names)}"
        summary_line = f"\nSummary: {result.one_line_summary}" if result.one_line_summary else ""
        cost_line = f"\nCost: {cost}" if cost else ""
        review_line = ""
        if getattr(result, "needs_review", False):
            notes = (getattr(result, "review_notes", "") or "")[:240]
            review_line = f"\n⚠️ needs_review: {notes}"
        return (
            f"✅ `{result.dispatch_id}` — {meta_label} done in {dur_min:.1f}m."
            f"{tier_line}{commits_line}{files_line}{artifact_line}"
            f"{summary_line}{cost_line}{review_line}"
            f"\n\nReply `restart {result.dispatch_id}` to bounce nexus-* services, "
            f"or `restart nexus-api` for one."
        )
    if result.status == "timeout":
        partial = ""
        if result.commits_made:
            partial = f" Made {len(result.commits_made)} commit(s) before kill."
        return (
            f"⏰ `{result.dispatch_id}` — {meta_label} hit time limit "
            f"at {dur_min:.1f}m.{partial}\nLogs: ~/AI_Agent/cc_logs/{result.dispatch_id}.log"
            f"\nReply `extend {result.dispatch_id} <minutes>` to re-dispatch with more budget."
        )
    if result.status == "cancelled":
        return f"🛑 `{result.dispatch_id}` — {meta_label} cancelled before dispatch."
    err = (result.error_tail or "")[-200:].replace("\n", " ")
    return (
        f"⚠️ `{result.dispatch_id}` — {meta_label} failed after {dur_min:.1f}m."
        f"\nLast error: `{err}`"
        f"\nLogs: ~/AI_Agent/cc_logs/{result.dispatch_id}.log"
        f"\nReply `retry {result.dispatch_id}` to re-run the same prompt."
    )


def _meta_for(dispatch_id: str) -> str:
    """Pull the original meta label out of cc_archive (or fall back)."""
    archive_path = cc_dispatch.ARCHIVE / f"{dispatch_id}.md"
    if not archive_path.exists():
        return dispatch_id
    meta, _ = cc_dispatch.read_prompt(archive_path)
    return meta.label if meta else dispatch_id


def _telegram(text: str) -> None:
    try:
        from tools import telegram_tool  # noqa: PLC0415
        telegram_tool.notify_sync(text, parse_mode=None)
    except Exception as exc:
        log.debug("telegram notify failed: %s", exc)


# Phase 28 — keep attachment payloads small enough for Telegram (50 MB
# hard cap; we cap at 10 to leave headroom + skip surprising junk).
_MAX_ATTACH_BYTES = 10 * 1024 * 1024


def _telegram_send_file(path: str, caption: str = "") -> None:
    """Best-effort Telegram document send. Skips files over 10 MB or
    missing on disk; never raises."""
    try:
        from tools import telegram_tool  # noqa: PLC0415
        p = Path(path).expanduser()
        if not p.exists() or not p.is_file():
            log.debug("attach skip — missing %s", path)
            return
        if p.stat().st_size > _MAX_ATTACH_BYTES:
            log.debug("attach skip — %s > 10MB", path)
            return
        telegram_tool.send_file_sync(str(p), caption=caption)
    except Exception as exc:
        log.debug("telegram file send failed for %s: %s", path, exc)


def _attach_artifacts(result: cc_dispatch.DispatchResult) -> None:
    """Auto-attach the build artifacts (HTML + screenshot) to Telegram.
    Phase 28 fixes the Phase 27 auto-attach bug — slash builds now
    deliver the actual file alongside the completion message."""
    artifacts = getattr(result, "artifact_paths", []) or []
    for p in artifacts[:5]:
        caption = f"{Path(p).name} (from {result.dispatch_id})"
        _telegram_send_file(p, caption=caption)


# Phase 28 — memory bridge: every successful slash dispatch becomes
# one line in wiki/log.md and bumps cumulative stats in
# wiki/entities/coding-router.md so `wiki coding router` returns
# something current.
_LOG_PATH = Path.home() / "AI_Agent" / "wiki" / "log.md"
_ROUTER_ENTITY_PATH = Path.home() / "AI_Agent" / "wiki" / "entities" / "coding-router.md"


def _log_dispatch_to_wiki(label: str, result: cc_dispatch.DispatchResult) -> None:
    """Append a one-line dispatch summary to wiki/log.md (newest at top
    per the log's existing convention)."""
    if result.status not in ("done", "failed", "timeout"):
        return
    if not getattr(result, "tier", ""):
        return  # Only Phase 28 tier-aware dispatches log here.
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        line = (
            f"\n{ts} — Phase 28 dispatch | tier={result.tier} | "
            f'"{label[:50]}" | {result.duration_seconds:.1f}s | '
            f"${result.estimated_cost_usd:.4f} | {result.status}"
        )
        if not _LOG_PATH.exists():
            return
        existing = _LOG_PATH.read_text(encoding="utf-8")
        # Insert just below the "---" header line so newest stays near top.
        if "\n---\n" in existing:
            head, body = existing.split("\n---\n", 1)
            new_text = head + "\n---\n" + line + "\n" + body
        else:
            new_text = existing + line + "\n"
        _LOG_PATH.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        log.debug("wiki log append failed: %s", exc)


def _bump_router_entity_stats(result: cc_dispatch.DispatchResult) -> None:
    """Recompute + rewrite wiki/entities/coding-router.md with cumulative
    dispatch stats (count by tier, total $, success rate). Source of
    truth = cc_metrics/dispatches.jsonl. Phase 29: legacy tier='real'
    is normalized to 'api' so the historical /real dispatches roll up
    into the renamed bucket cleanly."""
    if not getattr(result, "tier", ""):
        return
    try:
        from datetime import datetime as _dt
        import json as _json
        metrics = cc_dispatch.METRICS_LOG
        if not metrics.exists():
            return
        per_tier: dict[str, dict] = {}
        total_cost = 0.0
        total_done = 0
        total_count = 0
        with metrics.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = _json.loads(raw)
                except _json.JSONDecodeError:
                    continue
                raw_tier = rec.get("tier") or ""
                if not raw_tier:
                    continue  # Skip pre-Phase-28 entries.
                tier = cc_dispatch.normalize_tier(raw_tier)
                total_count += 1
                cost = float(rec.get("estimated_cost_usd") or 0)
                total_cost += cost
                done = rec.get("status") == "done"
                if done:
                    total_done += 1
                stats = per_tier.setdefault(tier, {"count": 0, "done": 0, "cost": 0.0})
                stats["count"] += 1
                stats["cost"] += cost
                if done:
                    stats["done"] += 1
        success_rate = (total_done / total_count * 100) if total_count else 0.0
        # Stable order matches the Phase 29 ladder (cheapest marginal
        # cost first, paid fallbacks last).
        tier_order = ["max", "local", "quick", "flash", "pro", "api"]
        rows = []
        for tier in tier_order + sorted(t for t in per_tier if t not in tier_order):
            if tier not in per_tier:
                continue
            s = per_tier[tier]
            sr = (s["done"] / s["count"] * 100) if s["count"] else 0.0
            rows.append(
                f"| {tier} | {s['count']} | {s['done']} | ${s['cost']:.4f} | {sr:.0f}% |"
            )
        body = (
            f"---\n"
            f"name: coding-router\n"
            f"type: entity\n"
            f"updated: {_dt.now(timezone.utc).isoformat()}\n"
            f"---\n\n"
            f"# Coding Router (Phase 28 + 29)\n\n"
            f"Tracks dispatches routed through the tier-aware Claude Code "
            f"dispatcher. Source: `cc_metrics/dispatches.jsonl`. Last "
            f"update auto-rewritten by `workers/cc_result_reporter`.\n\n"
            f"Phase 29 made `/max` the default for complex builds — Colton "
            f"already pays for the Max subscription, so the API-key path "
            f"became a rare fallback.\n\n"
            f"## Cumulative\n\n"
            f"- Total dispatches: **{total_count}**\n"
            f"- Successful: **{total_done}** ({success_rate:.0f}%)\n"
            f"- Total estimated cost (API-billed only): **${total_cost:.4f}**\n\n"
            f"## By tier\n\n"
            f"| tier | count | done | est. cost | success |\n"
            f"|------|-------|------|-----------|---------|\n"
            + ("\n".join(rows) if rows else "| (none yet) | 0 | 0 | $0.00 | 0% |")
            + "\n\n"
            f"## Slash commands (Phase 29 ladder)\n\n"
            f"- `/max <prompt>`   — Claude Sonnet 4.6 via Max plan ($0 marginal — uses subscription) **default for complex builds**\n"
            f"- `/code <prompt>`  — DeepSeek V4-Flash (~$0.005 — saves Max quota on small builds)\n"
            f"- `/pro <prompt>`   — DeepSeek V4-Pro (~$0.05 — DeepSeek mid-tier)\n"
            f"- `/api <prompt>`   — Sonnet 4.6 via API key (~$0.10–1.00 — fallback if Max limits hit)\n"
            f"- `/local <prompt>` — qwen3-coder:30b local ($0 — offline)\n"
            f"- `/quick <prompt>` — qwen3:4b chat ($0 — chat, not code)\n"
            f"- `/real <prompt>`  — *deprecated alias for /api; logged to "
            f"  `cc_logs/_deprecation.log` whenever used*\n\n"
            f"## Routing without a slash\n\n"
            f"- Casual chat → `/quick`\n"
            f"- `make a quick/simple/tiny X` → `/local`\n"
            f"- `build me X` / `create X` / `make me X` / `code X` → "
            f"  `/max` (Phase 29 default; was `/code` in Phase 28)\n"
        )
        _ROUTER_ENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ROUTER_ENTITY_PATH.write_text(body, encoding="utf-8")
    except Exception as exc:
        log.debug("router entity update failed: %s", exc)


def _wiki_ingest_dispatch(dispatch_id: str, label: str, result_path: Path) -> None:
    """Phase 25 integration — every dispatch result becomes a wiki source so
    its findings can be extracted into entities/concepts/decisions.

    We pass the raw JSON result path to wiki_ingest so the file lands in
    wiki/sources/ with frontmatter and triggers the extractor worker.
    Best-effort: silent on import or write failure (the dispatch is
    already reported via Telegram + event_bus, the wiki is bonus durability).
    """
    try:
        from tools import wiki_tool  # noqa: PLC0415
        # wiki_ingest expects a string source — the result file's path works.
        msg = wiki_tool.wiki_ingest.invoke({
            "source": str(result_path),
            "source_type": "dispatch_result",
        })
        log.info("wiki_ingest dispatch=%s → %s", dispatch_id, msg)
    except Exception as exc:
        log.debug("wiki_ingest failed for %s: %s", dispatch_id, exc)


def _process_new_results(reported: set[str]) -> None:
    if not cc_dispatch.RESULTS.exists():
        return
    files = sorted(cc_dispatch.RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for f in files:
        dispatch_id = f.stem
        if dispatch_id in reported:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result = cc_dispatch.DispatchResult(**data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            log.warning("skipping bad result %s: %s", f, exc)
            reported.add(dispatch_id)
            continue
        label = _meta_for(dispatch_id)
        msg = _format_telegram(label, result)
        log.info("reporting dispatch %s status=%s tier=%s",
                 dispatch_id, result.status, getattr(result, "tier", ""))
        _telegram(msg)
        # Phase 28 — auto-attach generated artifacts (HTML + screenshot)
        # to Telegram. Fixes the Phase 27 bug where the file path was
        # mentioned but the file itself never landed in chat.
        if result.status == "done":
            try:
                _attach_artifacts(result)
            except Exception as exc:
                log.debug("attach_artifacts crashed for %s: %s", dispatch_id, exc)
        try:
            event_bus.publish_remote(
                "cc_dispatch_reported",
                dispatch_id=dispatch_id, label=label, status=result.status,
                duration_seconds=result.duration_seconds,
                commits=len(result.commits_made),
                tier=getattr(result, "tier", ""),
            )
        except Exception:
            pass
        # Phase 28 — memory bridge. Append one line to wiki/log.md and
        # rewrite wiki/entities/coding-router.md cumulative stats so
        # `wiki coding router` from Telegram returns current numbers.
        try:
            _log_dispatch_to_wiki(label, result)
            _bump_router_entity_stats(result)
        except Exception as exc:
            log.debug("memory bridge crashed for %s: %s", dispatch_id, exc)
        # Phase 25 — fan dispatch result into the wiki sources layer so
        # the extractor can fold findings back into curated pages. Skip
        # for wiki-extract dispatches themselves to avoid feedback loops.
        if not label.startswith("wiki-extract:"):
            _wiki_ingest_dispatch(dispatch_id, label, f)
        _mark_reported(dispatch_id)
        reported.add(dispatch_id)


def main() -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    cc_dispatch.ensure_dirs()
    reported = _load_reported()
    log.info("cc_reporter ready (pid=%d, %d previously reported)",
             os.getpid(), len(reported))

    stop = {"flag": False}

    def _handler(_signum, _frame):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)

    while not stop["flag"]:
        try:
            _process_new_results(reported)
        except Exception as exc:
            log.exception("reporter loop error: %s", exc)
        time.sleep(POLL_SECONDS)

    log.info("cc_reporter exiting cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())

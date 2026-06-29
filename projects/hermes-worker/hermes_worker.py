#!/usr/bin/env python3
"""Hermes Task Poller — polls the Command Center for nexus tasks and executes them."""

import json
import re
import sys
import time
import logging
import hashlib
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── config ──────────────────────────────────────────────────────────────────
BASE_URL = "https://cwatt-commandcenter.vercel.app/api/hermes"
AGENT_TYPE = "nexus"
HERMES_KEY = "43ee1404aac27f577712a230313f72cbf732cec18486b81c647ced1e6b799f46"
POLL_INTERVAL = 60  # seconds
LOG_DIR = Path("/home/cwatt250/AI_Agent/projects/hermes-worker/logs")
CLAIMED_FILE = LOG_DIR / "claimed_jobs.jsonl"
CLAIMED_SET = set()  # dedup by task_id

# Agent memory: pull wiki context before a task, post a wiki entry after.
WIKI_AUTHOR = "nexus-worker-1"
WIKI_CATEGORY = "decisions"

# Low-priority debounce: only auto-run low priority every Nth poll
LOW_PRIORITY_MIN_INTERVAL = 300  # 5 minutes between low-priority auto-runs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("hermes-worker")

HEADERS = {
    "X-Hermes-Key": HERMES_KEY,
    "Content-Type": "application/json",
}


def ensure_log_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_claimed_ids() -> set:
    if not CLAIMED_FILE.exists():
        return set()
    ids = set()
    for line in CLAIMED_FILE.read_text().strip().splitlines():
        if line:
            ids.add(line.strip())
    return ids


def save_claimed_id(task_id: str):
    CLAIMED_SET.add(task_id)
    with open(CLAIMED_FILE, "a") as f:
        f.write(f"{task_id}\n")


def poll_tasks() -> list[dict]:
    """Fetch pending tasks from Hermes."""
    url = f"{BASE_URL}/tasks"
    params = {"agent_type": AGENT_TYPE}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "tasks" in data:
            return data["tasks"]
        return data if isinstance(data, list) else []
    except Exception as e:
        log.error("poll_tasks failed: %s", e)
        return []


# ── agent memory: wiki context fetch + wiki entry post ──────────────────────
def resolve_slug(task: dict) -> str:
    """Determine the project slug for a task. Tries common field names."""
    for key in ("slug", "project_slug", "project"):
        val = task.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    proj = task.get("project")
    if isinstance(proj, dict):
        slug = proj.get("slug") or proj.get("name")
        if slug:
            return str(slug).strip()
    return ""


def fetch_project_context(slug: str) -> str:
    """GET wiki context for a project. Returns context text, '' on any failure.

    Graceful by design: if the API is down or the project has no wiki yet,
    the task still runs — just without prepended context.
    """
    if not slug:
        return ""
    url = f"{BASE_URL}/projects/{slug}/context"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "application/json" in ctype:
            data = resp.json()
            if isinstance(data, dict):
                return data.get("context") or data.get("wiki") or data.get("content") or ""
            if isinstance(data, str):
                return data
            return json.dumps(data)
        return resp.text or ""
    except Exception as e:
        log.warning("⚠️  Wiki context fetch failed for '%s' (continuing without): %s", slug, e)
        return ""


def inject_context(task: dict, context: str) -> dict:
    """Prepend fetched wiki context to the task brief. Mutates and returns task."""
    if not context:
        return task
    task["wiki_context"] = context
    block = f"## Wiki Context (from CommandCenter)\n{context}\n\n---\n\n"
    for field in ("brief", "description", "body", "prompt"):
        if task.get(field):
            task[field] = block + str(task[field])
            return task
    task["brief"] = block
    return task


_PR_URL_RE = re.compile(r"https?://github\.com/[\w.-]+/[\w.-]+/pull/(\d+)")
_PR_NUM_RES = [
    re.compile(r"\bPR\s*#?(\d+)\b", re.IGNORECASE),
    re.compile(r"\bpull request\s*#?(\d+)\b", re.IGNORECASE),
]


def extract_pr(result: str):
    """Find a PR reference + URL in a result string. Returns (pr_ref, pr_url)."""
    if not result:
        return None, None
    url_match = _PR_URL_RE.search(result)
    pr_url = url_match.group(0) if url_match else None
    if url_match:
        return f"#{url_match.group(1)}", pr_url
    for pat in _PR_NUM_RES:
        m = pat.search(result)
        if m:
            return f"#{m.group(1)}", pr_url
    return None, pr_url


def post_wiki_entry(slug: str, result: str):
    """POST a wiki entry summarizing a completed task. Graceful on failure."""
    if not slug:
        log.warning("⚠️  No slug resolved; skipping wiki entry")
        return
    pr_ref, pr_url = extract_pr(result)
    summary = ""
    if result:
        for line in result.strip().splitlines():
            if line.strip():
                summary = line.strip()[:280]
                break
    prefix = f"PR {pr_ref}" if pr_ref else "Task completed"
    content = f"{prefix} - {summary}" if summary else prefix
    if pr_url:
        content += f"\n{pr_url}"

    url = f"{BASE_URL}/projects/{slug}/wiki"
    payload = {"content": content, "category": WIKI_CATEGORY, "created_by": WIKI_AUTHOR}
    try:
        resp = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        log.info("📝 Posted wiki entry to '%s': %s", slug, content.splitlines()[0])
    except Exception as e:
        log.warning("⚠️  Wiki entry post failed for '%s' (continuing): %s", slug, e)


def is_task_brief_ready(task: dict) -> bool:
    return task.get("status") in ("brief_ready",)


def is_task_auto_runnable(task: dict) -> bool:
    """Check if task priority qualifies for auto-run."""
    prio = (task.get("priority") or "").lower()
    return prio in ("low", "medium")


def claim_task(task_id: str) -> bool:
    """Claim a task by setting status to 'running'."""
    url = f"{BASE_URL}/{task_id}/claim"
    payload = {"agent_type": AGENT_TYPE, "status": "running"}
    try:
        resp = requests.patch(url, json=payload, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        log.info("✅ Claimed task %s", task_id)
        return True
    except Exception as e:
        log.error("❌ Failed to claim task %s: %s", task_id, e)
        return False


def run_skill(skill_name: str, task: dict) -> str:
    """Run a skill with the given task payload. Returns result summary."""
    # Resolve skill name -> script / module
    skills_dir = Path("/home/cwatt250/AI_Agent/projects/hermes-worker/skills")
    skill_script = skills_dir / f"{skill_name}.sh"
    skill_py = skills_dir / f"{skill_name}.py"

    task_payload = json.dumps(task, indent=2)

    if skill_script.exists():
        log.info("Running skill via shell: %s", skill_script)
        import subprocess

        result = subprocess.run(
            ["bash", str(skill_script), task_payload],
            capture_output=True, text=True, timeout=300,
        )
        return result.stdout or result.stderr or "(no output)"

    elif skill_py.exists():
        log.info("Running skill via python: %s", skill_py)
        import subprocess

        result = subprocess.run(
            ["python3", str(skill_py), task_payload],
            capture_output=True, text=True, timeout=300,
        )
        return result.stdout or result.stderr or "(no output)"

    else:
        # Default: use qwen3:4b local to execute the task
        log.info("Executing task via qwen3:4b (default executor)")
        import subprocess

        prompt = f"""You are executing a Hermes task. Run this task and report results.

Task payload:
{task_payload}

Execute the task, then respond with a concise summary of what you did and the result."""

        result = subprocess.run(
            [
                "ollama", "run", "qwen3:4b",
            ],
            input=prompt,
            capture_output=True, text=True, timeout=300,
        )
        return result.stdout or result.stderr or "(no output)"


def complete_task(task_id: str, result: str):
    """Mark task as completed with result."""
    url = f"{BASE_URL}/{task_id}/complete"
    payload = {
        "agent_type": AGENT_TYPE,
        "result": result[:50000],  # cap result length
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        log.info("✅ Task %s completed successfully", task_id)
    except Exception as e:
        log.error("❌ Failed to complete task %s: %s", task_id, e)
        # Try fallback: PATCH to set status=completed
        try:
            resp2 = requests.patch(
                url.replace("/complete", ""),
                json={"agent_type": AGENT_TYPE, "status": "completed"},
                headers=HEADERS, timeout=15,
            )
            log.info("Fallback status update: %s", resp2.status_code)
        except Exception as e2:
            log.error("Fallback also failed: %s", e2)


def fail_task(task_id: str, error: str):
    """Mark task as failed."""
    url = f"{BASE_URL}/{task_id}/fail"
    payload = {
        "agent_type": AGENT_TYPE,
        "error": error,
        "status": "failed",
        "failed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        log.error("❌ Task %s marked as failed: %s", task_id, error)
    except Exception as e:
        log.error("❌ Failed to fail task %s: %s", task_id, e)


def main():
    ensure_log_dir()
    global CLAIMED_SET
    CLAIMED_SET = load_claimed_ids()
    log.info("🚀 Hermes Worker started. Polling %s every %ds", BASE_URL, POLL_INTERVAL)
    log.info("📋 Already claimed: %d jobs in history", len(CLAIMED_SET))

    last_low_prio_run = 0

    while True:
        try:
            tasks = poll_tasks()
            if tasks:
                log.info("📨 Received %d tasks from Hermes", len(tasks))
            else:
                log.debug("📨 No tasks from Hermes")

            for task in tasks:
                task_id = task.get("id", task.get("task_id", ""))
                if not task_id:
                    continue

                # Dedup
                if task_id in CLAIMED_SET:
                    continue

                # Filter: only brief_ready with low/medium priority
                if not is_task_brief_ready(task):
                    log.debug("Skipping task %s (status=%s, not brief_ready)", task_id, task.get("status"))
                    continue

                if not is_task_auto_runnable(task):
                    log.debug("Skipping task %s (priority=%s, not auto-runnable)", task_id, task.get("priority"))
                    continue

                # Low-priority debounce
                now = time.time()
                priority = (task.get("priority") or "low").lower()
                if priority == "low":
                    if now - last_low_prio_run < LOW_PRIORITY_MIN_INTERVAL:
                        log.debug("Throttling low-priority task %s (interval=%ds)", task_id, now - last_low_prio_run)
                        continue

                # Claim & run
                if not claim_task(task_id):
                    continue
                save_claimed_id(task_id)
                last_low_prio_run = now

                # Pre-task: pull wiki context and prepend to the brief.
                slug = resolve_slug(task)
                context = fetch_project_context(slug)
                if context:
                    log.info("📖 Prepended %d chars of wiki context for '%s'", len(context), slug)
                task = inject_context(task, context)

                skill = task.get("skill", "default")
                try:
                    log.info("🔧 Executing task %s (skill=%s)", task_id, skill)
                    result = run_skill(skill, task)
                    log.info("✅ Task %s done: result preview: %s", task_id, result[:200])
                    complete_task(task_id, result)
                    # Post-task: record what happened (incl. PR) to the project wiki.
                    post_wiki_entry(slug, result)
                except Exception as e:
                    err_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                    log.error("❌ Task %s error: %s", task_id, err_msg)
                    fail_task(task_id, err_msg)

        except Exception as e:
            log.error("❌ Poll loop error: %s", e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()

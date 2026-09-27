"""Skills — reusable step-by-step procedures Nexus writes for itself (step 4).

lessons.md holds one-line takeaways; a skill is a whole procedure: "how to
deploy a static site", "how to check why a service is down". Each is one
markdown file in ~/AI_Agent/skills/<slug>.md:

    ---
    name: check-service-down
    when: a service/site is down or unresponsive and Colton asks why
    uses: 3
    ---
    1. systemctl status <unit> ...

The heavy agent sees `name — when` for every skill in its prompt and pulls
the body with skill_read before a matching task. After a multi-step task
succeeds, review_task() asks the brain whether the procedure is worth
saving: create a new skill, patch an existing one, or (usually) nothing.
Policy ported from Hermes's background review, incl. its anti-poisoning list.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

log = logging.getLogger("nexus.skills")

SKILLS_DIR = Path.home() / "AI_Agent" / "skills"
MIN_TOOL_CALLS = 3      # fewer than this isn't a procedure worth saving
MAX_LISTED = 40         # cap on skills listed in the prompt
MAX_BODY = 4000         # chars per skill body

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
_lock = threading.Lock()


def slugify(name: str) -> str:
    return _SLUG_RE.sub("-", (name or "").lower()).strip("-")[:60]


def _parse(text: str) -> dict:
    m = _FM_RE.match(text)
    meta, body = {}, text
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return {"name": meta.get("name", ""), "when": meta.get("when", ""),
            "uses": int(meta.get("uses", "0") or 0), "body": body.strip()}


def _render(s: dict) -> str:
    return (f"---\nname: {s['name']}\nwhen: {s['when']}\nuses: {s['uses']}\n---\n"
            f"{s['body'].strip()[:MAX_BODY]}\n")


def list_skills() -> list[dict]:
    if not SKILLS_DIR.exists():
        return []
    out = []
    for p in sorted(SKILLS_DIR.glob("*.md")):
        s = _parse(p.read_text(encoding="utf-8"))
        s["name"] = s["name"] or p.stem
        out.append(s)
    return out


def get(name: str) -> dict | None:
    p = SKILLS_DIR / f"{slugify(name)}.md"
    return _parse(p.read_text(encoding="utf-8")) if p.exists() else None


def save(name: str, when: str, body: str) -> str:
    """Create or replace a skill. Returns its slug. Keeps the use count."""
    slug = slugify(name)
    if not slug or not body.strip():
        raise ValueError("skill needs a name and a body")
    with _lock:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        old = get(slug)
        (SKILLS_DIR / f"{slug}.md").write_text(_render({
            "name": slug, "when": " ".join((when or "").split())[:200],
            "uses": old["uses"] if old else 0, "body": body}), encoding="utf-8")
    return slug


def read(name: str) -> str:
    """Body of a skill for the agent, and count the use."""
    with _lock:
        s = get(name)
        if s is None:
            return ""
        s["uses"] += 1
        (SKILLS_DIR / f"{slugify(name)}.md").write_text(_render(s), encoding="utf-8")
    return s["body"]


def prompt_block() -> str:
    """`name — when` list for the agent's system prompt (most-used first)."""
    ss = sorted(list_skills(), key=lambda s: -s["uses"])[:MAX_LISTED]
    if not ss:
        return ""
    lines = [f"- {s['name']} — {s['when']}" for s in ss]
    return ("# SKILLS (your saved procedures — call skill_read(name) BEFORE a "
            "matching task and follow it)\n" + "\n".join(lines))


# ── background review ────────────────────────────────────────────────────

REVIEW_PROMPT = """You maintain Nexus's library of SKILLS: reusable step-by-step procedures for recurring tasks. You just watched a task Nexus completed successfully. Decide if it taught a procedure worth keeping.

Return ONLY JSON: {"action": "none"|"create"|"patch", "name": "<kebab-case>", "when": "<one line: the situation that should trigger this skill>", "body": "<numbered steps: concrete commands, tool names, paths, gotchas>"}

Preference order: patch an existing skill that covers this > create a new one > none. Most tasks deserve "none" — only save a procedure that is likely to RECUR and took real figuring out (several steps, a non-obvious gotcha, a specific command sequence). For "patch", use the existing skill's exact name and return its FULL improved body (merge, don't drop old steps that still hold).

NEVER save (these poison the library and harden into refusals):
- environment failures: missing binary, service down, expired/missing key, "command not found";
- negative capability claims ("X doesn't work", "can't do Y");
- one-off facts about a single request, or anything with secrets/tokens in it;
- procedures that just restate a single tool call.

Existing skills (name — when):
{existing}"""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["none", "create", "patch"]},
        "name": {"type": "string"},
        "when": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["action", "name", "when", "body"],
}

_SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*\S{8,}|"
                        r"\b(sk-|ghp_|github_pat_|xox[bp]-)[A-Za-z0-9_-]{10,}")


def review_task(task: str, reply: str, tool_trace: list[str], *, chat=None) -> dict:
    """Ask the brain whether a finished task yields a skill; apply it.

    Returns the decision dict plus "saved": slug|None. `chat` is injectable
    for tests (defaults to core.brain.chat)."""
    if len(tool_trace) < MIN_TOOL_CALLS:
        return {"action": "none", "saved": None, "why": "too few steps"}
    if chat is None:
        from core.brain import chat  # noqa: PLC0415
    existing = "\n".join(f"- {s['name']} — {s['when']}" for s in list_skills()) or "(none yet)"
    user = (f"TASK:\n{task[:2000]}\n\nTOOL CALLS IN ORDER:\n" +
            "\n".join(f"{i + 1}. {t[:300]}" for i, t in enumerate(tool_trace[:40])) +
            f"\n\nFINAL REPLY:\n{reply[:2000]}")
    raw = chat([{"role": "system", "content": REVIEW_PROMPT.replace("{existing}", existing)},
                {"role": "user", "content": user}],
               fmt=REVIEW_SCHEMA, options={"temperature": 0.1, "num_predict": 1200},
               timeout=180.0, allow_degraded=False)
    d = json.loads(raw)
    d["saved"] = None
    if d.get("action") not in ("create", "patch") or not d.get("body", "").strip():
        return d
    if _SECRET_RE.search(d["body"]):
        log.warning("skills: dropped %r — body looks like it holds a secret", d.get("name"))
        return {**d, "action": "none", "why": "secret"}
    if d["action"] == "patch" and get(d["name"]) is None:
        d["action"] = "create"          # model named a skill that doesn't exist
    d["saved"] = save(d["name"], d["when"], d["body"])
    log.info("skills: %s %s", d["action"], d["saved"])
    return d


def review_task_async(task: str, reply: str, tool_trace: list[str]) -> None:
    """Fire-and-forget review on a daemon thread. Never raises."""
    if len(tool_trace) < MIN_TOOL_CALLS:
        return

    def _run():
        try:
            review_task(task, reply, tool_trace)
        except Exception as exc:  # noqa: BLE001
            log.warning("skills review failed: %s: %s", type(exc).__name__, exc)

    threading.Thread(target=_run, name="skills-review", daemon=True).start()

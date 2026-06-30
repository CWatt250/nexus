"""Lifecycle hooks (G5, Hermes-inspired).

Run user-defined scripts on agent events without editing core code. Config
lives in config/hooks.yaml:

    session_start:
      - name: warm-brain
        command: "curl -s localhost:11434/api/ps >/dev/null"
    session_end:
      - name: auto-commit
        command: "cd ~/AI_Agent && git add -A && git commit -m 'auto' || true"
    on_error:
      - name: notify
        command: "echo \"$NEXUS_HOOK_ERROR\" >> ~/AI_Agent/memory/errors.log"

Events: session_start, session_end, on_error, pre_tool, post_tool.
Context is passed to each hook as NEXUS_HOOK_<KEY> env vars AND as a JSON
object on stdin. Hooks are best-effort: a failing/slow hook is logged and
skipped, never propagated into the agent. A hook's stdout (when
`feed_context: true`) is returned so callers can inject it.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("nexus.hooks")

ROOT = Path.home() / "AI_Agent"
HOOKS_FILE = ROOT / "config" / "hooks.yaml"
EVENTS = ("session_start", "session_end", "on_error", "pre_tool", "post_tool")
_DEFAULT_TIMEOUT = 30


def _load() -> dict:
    if not HOOKS_FILE.exists():
        return {}
    try:
        import yaml  # noqa: PLC0415
        return yaml.safe_load(HOOKS_FILE.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.warning("hooks.yaml load failed: %s", exc)
        return {}


def run_hooks(event: str, **context) -> list[str]:
    """Run every enabled hook for `event`. Returns the stdout of hooks that
    opt in with `feed_context: true` (for the caller to inject). Never raises."""
    cfg = _load()
    hooks = cfg.get(event) or []
    if not isinstance(hooks, list):
        return []
    env = dict(os.environ)
    env["NEXUS_HOOK_EVENT"] = event
    for k, v in context.items():
        env[f"NEXUS_HOOK_{k.upper()}"] = str(v)[:2000]
    payload = json.dumps(context, ensure_ascii=False, default=str)
    fed: list[str] = []
    for h in hooks:
        if not isinstance(h, dict) or not h.get("enabled", True):
            continue
        cmd = h.get("command")
        if not cmd:
            continue
        try:
            r = subprocess.run(cmd, shell=True, env=env, input=payload, text=True,
                               capture_output=True,
                               timeout=int(h.get("timeout", _DEFAULT_TIMEOUT)))
            if r.returncode != 0:
                log.warning("hook %s exited %s: %s",
                            h.get("name", cmd[:24]), r.returncode, (r.stderr or "")[:200])
            if h.get("feed_context") and r.stdout.strip():
                fed.append(r.stdout.strip()[:4000])
        except subprocess.TimeoutExpired:
            log.warning("hook %s timed out", h.get("name", cmd[:24]))
        except Exception as exc:
            log.warning("hook %s failed: %s", h.get("name", cmd[:24]), exc)
    return fed

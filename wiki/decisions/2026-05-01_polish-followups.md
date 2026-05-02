---
title: May 1 Polish Pass — Followups
date: 2026-05-02
status: open
tags: [followup, prompt-cache, conversation-handler]
---

# Context

May 1 polish-pass dispatch + May 2 retro re-dispatch shipped fixes for 12+ bugs across multiple commits. Fix 2 from the May 2 re-dispatch (slang glossary in SOUL.md) failed verification on the qwen3:4b quick_chat code path. This file tracks what's left.

# Fix 2 — Slang glossary not visible to quick_chat

## What's done

- `SOUL.md` updated with the canonical glossary (commits `14ec2aa` + `eb5d04f`). `nexus-agent.service` restarted clean.
- Heavy agent path picks up the glossary on next launch (it loads `SOUL.md` via `nexus.load_static_prefix()`).

## Verification result

```
$ python3 -c "from workers import conversation_handler; print(conversation_handler.route_message('what does lfg mean'))"
kind  : query_inline
reply : 'lfg = looking for group'
VERIFICATION: FAIL
```

## Why it fails

`workers/conversation_handler.py:quick_chat` (qwen3:4b path used for `CHAT` and `QUERY_INLINE` intents) constructs its system prompt from the module-level constant `QUICK_CHAT_SYSTEM_PROMPT_BASE` plus `_datetime_context()` — not from `nexus.load_static_prefix()`. So `SOUL.md` content is not injected on the quick_chat path even after a service restart.

Routing decision for "what does lfg mean":
- Intent classifier (`qwen3.6` via `classify_intent_llm`) picks `QUERY_INLINE` (factual, answerable in 1-2 sentences).
- `_route_message_inner` routes QUERY_INLINE to `quick_chat`.
- `quick_chat` uses `QUICK_CHAT_SYSTEM_PROMPT_BASE` (hardcoded), not `SOUL.md`.
- Model answers from training data ("looking for group" — gaming-community default).

## Manual recovery options (pick one)

### Option A — inject SOUL.md slang section into quick_chat (recommended)

Touches one file (`workers/conversation_handler.py`), adds ~15 lines, ~80 token overhead per quick_chat call. Single source of truth stays in `SOUL.md`.

Sketch (NOT applied — needs explicit user authorization since the May 2 spec said "do not modify other files"):

```python
# In conversation_handler.py, near QUICK_CHAT_SYSTEM_PROMPT_BASE:

def _slang_overlay() -> str:
    """Pull the '## User slang glossary' section out of SOUL.md so the
    quick_chat path doesn't answer 'lfg = looking for group' from
    qwen3:4b training data."""
    soul = (Path.home() / "AI_Agent" / "SOUL.md").read_text(encoding="utf-8")
    m = re.search(r"## User slang glossary.*?(?=\n## )", soul, re.DOTALL)
    return m.group(0) if m else ""

# Then in quick_chat():
system_prompt = (
    f"{QUICK_CHAT_SYSTEM_PROMPT_BASE}\n\n"
    f"{_slang_overlay()}\n\n"
    f"{_datetime_context()}"
)
```

After applying:
1. `git add workers/conversation_handler.py && git commit -m "fix(quick-chat): inject SOUL.md slang glossary into quick_chat prompt"`
2. `sudo systemctl restart nexus-api.service nexus-task-worker.service nexus-telegram.service nexus-agent.service`
3. Re-run verification: `python3 -c "from workers import conversation_handler; print(conversation_handler.route_message('what does lfg mean'))"` — expect `let's fucking go`.

### Option B — accept the partial fix

The heavy agent (TASK route) sees `SOUL.md` and answers correctly. To force TASK route on slang lookups:
- User-facing workaround: prefix `queue: ` (e.g. `queue: what does lfg mean`).
- Cost: 30-min worker timeout budget vs ~1.5s quick_chat. Wasteful for a one-word answer.

### Option C — extend the intent classifier

Teach `INTENT_SYSTEM_PROMPT` (in same file) to route "what does <slang> mean" questions to TASK so the heavy agent answers. Same one-file constraint as Option A; simpler conceptually but slower per-question (~30s vs 1.5s).

# Fix 1 — EOD timer move (RESOLVED)

Verified 2026-05-02 08:50 PDT: `systemctl list-timers nexus-eod-summary.timer` shows next trigger `Sat 2026-05-02 20:01:02 PDT`. Daily, no Mon..Fri restriction, `America/Los_Angeles` TZ-encoded so DST flips don't break it.

# Fix 3 — SUDO script hardening (ALREADY SHIPPED)

Commit `5462d1e` (this morning) shipped both `SUDO_DISPATCH.sh` and `SUDO_COMMANDS_R3.sh` with the loud-fail pattern. May 2 re-dispatch verified the existing scripts already match spec; no new commit needed.

# Recommendation

Apply Option A in the next dispatch when the user authorizes touching `workers/conversation_handler.py`. The 80 token overhead per quick_chat call is negligible (current quick_chat prompt is ~600 tokens; adds ~13%). One-file change, one source of truth preserved.

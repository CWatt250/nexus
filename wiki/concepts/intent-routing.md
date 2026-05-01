---
name: Intent Routing
description: Five-way classifier (CHAT / QUERY_INLINE / QUERY_TOOL / TASK / STATUS) that decides how a user message gets handled.
type: concept
last_updated: 2026-05-01
sources: []
tags: [routing, classifier, conversation-handler, qwen3-4b]
---

# Intent Routing

Every Telegram / API message hits the conversation handler first. A small qwen3:4b classifier picks one of five intents and the handler dispatches accordingly. This is what keeps the bot snappy — heavy turns never run inline.

## Intents

| Intent | Routes to | Latency target | Example |
|---|---|---|---|
| `CHAT` | Inline qwen3:4b reply | <2s | "yo what's up" |
| `QUERY_INLINE` | Inline qwen3:4b reply with retrieved facts | <3s | "what's my role at Argus" |
| `QUERY_TOOL` | Tool-using mini-agent (search, fetch, etc.) | <8s | "what's the weather in Cleveland" |
| `TASK` | Enqueue to `nexus-task-worker` (returns task_id immediately) | <1s ack, hours of work | "refactor the dispatch state machine" |
| `STATUS` | Read from `memory/active_tasks.jsonl` | <1s | "what's task cc_abc up to" |

## Why split QUERY into two

A personal-fact recall ("who do I work for") doesn't need a tool call — the answer is in memory or system prompt context. A real web question does. Treating them the same wastes time on every greeting-style message.

## Bypass prefix

Power-user shortcut: `queue: <text>` skips classification and enqueues the message as a `TASK`. Useful when the classifier mis-routes.

## Where the code lives

- Classifier + dispatcher: `workers/conversation_handler.py:route_message()`.
- Telegram entry point: `tools/telegram_listener.py:handle_message()` calls `route_message` via `asyncio.to_thread` with a 25s timeout.
- Task queue: `core/task_queue.py` (SQLite WAL).
- Worker: `workers/task_worker.py`.

## Related
- [Dispatch system](dispatch-system.md) — when even a TASK is too heavy, the worker may escalate to Claude Code.

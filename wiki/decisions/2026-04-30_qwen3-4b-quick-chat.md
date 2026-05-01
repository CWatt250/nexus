---
name: qwen3:4b for quick-chat router
description: Use qwen3:4b (not qwen3.6:35b-a3b) for the conversation handler classifier and for inline CHAT/QUERY_INLINE replies.
type: decision
last_updated: 2026-05-01
sources: []
tags: [routing, ollama, qwen, conversation-handler, latency]
---

# 2026-04-30 — qwen3:4b for quick-chat router

## Decision
The conversation handler (`workers/conversation_handler.py`) uses qwen3:4b only — for classification and for inline CHAT / QUERY_INLINE replies. Heavy intents (QUERY_TOOL, TASK) escalate to qwen3.6:35b-a3b inside the worker.

## Why
- Latency. qwen3:4b first-token is ~5x faster than qwen3.6 on WattBott. Telegram users notice the difference.
- Predictability. Classification accuracy on a 5-class task is fine at 4B; using 35B for it is wasted compute and cache pressure.
- Cache discipline. Keeping qwen3:4b permanently warm (`OLLAMA_KEEP_ALIVE=-1`) leaves headroom for qwen3.6 to stay warm too without thrashing.

## How to apply
- Don't replace qwen3:4b in the handler with the heavy model "to make it smarter." If a classification is wrong, fix the prompt or the few-shots.
- `nexus-prewarm.service` keeps both warm — never disable it.
- Performance Guardian's LRU rules (Phase 16.7) explicitly never unload qwen3:4b or qwen3.6:35b-a3b. Don't change that.

## Related
- Concept: [Intent routing](../concepts/intent-routing.md)
- Phase 13 — Speed layer (where the router-model latency target was set)

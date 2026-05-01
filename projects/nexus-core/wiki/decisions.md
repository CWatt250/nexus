# Decisions

## Routing: fast model for trivial, heavy for complex
- **Date:** 2026-05-01
- **Rationale:** Save API compute and latency on simple queries (math, greeting, factual). Reserve qwen3.6 (or equivalent heavy model) for tool-use, coding, and reasoning tasks.
- **Heuristic:** Message length < 50 chars + no tool keywords → fast model. Otherwise → heavy model.
- **Fallback:** If fast model fails, escalate to heavy.

## Memory: Chroma for raw passages, Mem0 for durable facts
- **Date:** 2026-05-01
- **Rationale:** Chroma RAG handles passage recall efficiently. Mem0's LLM fact extraction is better for persistent preferences/decisions.

## Safety: guardrails layer, not license
- **Date:** 2026-05-01
- **Rationale:** Guardrails prevent catastrophic mistakes; they don't replace thinking. Always check before write/execute/delete operations.

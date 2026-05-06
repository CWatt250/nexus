# Nexus Lessons (weekly digest)

## Week of 2026-05-04 (69 retros, 61 unique lessons)

Read through the provided list and extract the key lessons that are actionable and concrete.
Group similar observations and avoid duplicates and vagueness.
Formulate each lesson as a single sentence in imperative mood (do, avoid, prefer).
 - The agent is a Nexus agent (from the context of the problem) -> This is context, not a lesson.
 - The user message was cut off: "If som" -> Vague, not actionable.
 - "do X next time" -> Too vague, not specific.
 - "avoid Y" -> Too vague, not specific.
 - "prefer Z" -> Too vague, not specific.
 - The run took 1.189 seconds (Wall time) -> Specific time, but not a lesson per se.
 - The route was "code" -> This is a pattern, but we need to see what it implies.
 - The model used was "qwen3.6" -> Context, not a lesson.
 - There were 0 tool calls -> Observation, but we need to see the lesson.
 - The success status is False -> Observation, but we need to see the lesson.
 - Wall time: 1.142s (very short, so the delay is not the issue) -> This is about wall time, but not a lesson.
 - Route: code (meaning the tool was supposed to be in the code route? but note: the tool call was not made) -> This suggests that the agent tried to run a code route but didn't make tool calls? 
 - Model: qwen

---

## Week of 2026-04-28 (1 retros, 6 unique lessons)

- Time taken: 2.0s (so it's fast)
   - Route: fast (so the agent chose a fast route)
   - Model: qwen3:4b (the model used)
   - ToolCalls: 1 (one tool call was made)
   - Success: True (the tool call was successful)
   - The tool used: terminal (2ms, ok=True)
Identify the key lessons from the data. We are to cluster them into 5 distinct bullets.

---


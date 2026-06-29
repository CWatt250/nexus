# Nexus Lessons (weekly digest)

## Week of 2026-06-29 (0 retros, 0 unique lessons)

_(no actionable lessons captured this week)_

---

## Week of 2026-06-22 (1 retros, 0 unique lessons)

_(no actionable lessons captured this week)_

---

## Week of 2026-06-15 (2 retros, 1 unique lessons)

- The user message was incomplete

---

## Week of 2026-06-08 (5 retros, 16 unique lessons)

Identify key points from the list that are actionable and concrete.
Avoid vague statements and duplicates.
Convert each into a single sentence in imperative mood: "Do X", "Avoid Y", "Prefer Z".
 - The agent removed (commented out) two GitHub tokens from .env files: one -> This is an action that was taken. But note: it says "removed (commented out)" meaning they were commented out. We can interpret as: "Comment out GitHub tokens in .env files" but the instruction says "Do X" for actionable. However, the context is that they did this. We are to cluster lessons for next time. So we might say: "Do not store GitHub tokens in .env files" (but note: they commented out, so they are safe? Actually, the lesson might be to avoid having tokens in .: so we can say: "Avoid storing GitHub tokens in .env files" OR "Comment out GitHub tokens in .env files" but the instruction says "Do X" for positive. Alternatively, the lesson might be that they did it correctly? But the problem says "lessons" meaning what to do next time.
The agent removed (commented out) two

---

## Week of 2026-06-01 (14 retros, 15 unique lessons)

Identify the key points from the provided text.
Cluster similar points into distinct lessons.
   - The user said "Yes check" which is not a standard phrase. The agent started to process it but the response was cut off (it says "Let me process this." and then the message breaks off with "In the context of Colton's communication style, he uses slang like ..."). This suggests the agent might have been trying to interpret the user's message in [context]. 
   - No `ANTHROPIC_API_KEY` is configured in `~/.env` or `config/secrets.yaml`. The `.example` file shows where it goes, but it's not set.
   - `CLAUDE_CODE_MONTHLY_BUDGET` is se (truncated, but likely means it's set to a value? but the context says "is se" so we don't have the full value)
   - Dispatched with dispatch_id=cc_4b3a1b11
   - Budget: 5m (which we interpret as 5 million, but note: in the context of the problem, it might be a budget constraint)
   - Wall time: 53.26s (the time taken for the task)
   - Route: mid
   - Model: qwen3:8b
   - ToolCalls: 1
   - Success: True
   - The agent says: "Yeah, I see it. Alibaba Cloud claiming Qwen3.7-Max is the #2 AI coding model globally on Code Arena (1541 score, behind only Claude). Claims it can run 35-hour tasks, 1000+ tool calls, and ship

---

## Week of 2026-05-25 (2 retros, 0 unique lessons)

_(no actionable lessons captured this week)_

---

## Week of 2026-05-18 (5 retros, 5 unique lessons)

- The circuit breaker kicked in from a loop of window-enumeration attempts.
- The agent can see Brave is
- The user asked for a handler named "handler-iso", which wasn't found in the wiki.
- The system used the Intent Routing (5-way classifier) and the Qwen3-4b router for quick chats.
- The wall shows the time taken (594.031s) which is quite long (over 594 seconds, i.e., about 10 minutes) for a response? But note: the wall says "594.0

---

## Week of 2026-05-11 (25 retros, 17 unique lessons)

Read through the list and extract the key lessons (the meaningful observations that can be turned into actionable advice).
Cluster similar observations into 5 distinct bullets.
Each bullet must be a single sentence in imperative mood.
 - The agent did not use any tools (ToolCalls) -> This is a problem: the agent should use tools when needed? But note: the list says "ToolCalls: 1" in some cases, so it's inconsistent. However, the observation says "did not use any tools" (so ToolCalls:0) but then later we see ToolCalls:1. We have to be careful.
 - The agent says: "Good. The best reference is the **Galaxies-dev/chatgpt-clone-react-native** (287⭐, MIT) — it has sidebar, chat history, topics/projects" -> This is a positive note about a reference. But we are to cluster lessons (lessons from the turn). This might be a lesson about providing specific references? However, the instruction says: "per-turn lessons". So this is a lesson that the agent did a good thing? But we are to focus on what the agent did wrong or what we should do.
 - Concise and direct. Say what needs saying, noth -> This seems like a vague instruction. We can drop it? But it says "noth" (probably typo for "not"). We'll skip vague ones.
 - Supabase** — database, auth (email OTP), and portal invite flows. Needs: -> This is incomplete. It says "

---

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


# Nexus Lessons Learned

High-quality turns (reflection quality >= 4) get appended here as bullets so future Nexus sessions can benefit from what worked.

- 2026-04-19 [greeting, prompting, response]: When greeting a system with a specific identity, prioritize concise acknowledgment with a prompt for action rather than over-explaining
- 2026-04-19 [math, arithmetic, response]: For simple arithmetic questions, direct numerical answers without tool invocation or elaboration save time and align with user expectations
- 2026-04-19 [file-io, markdown]: When quoting lists from markdown files, explicitly state the source file to avoid ambiguity
- 2026-04-19 [bid-system, feature-prioritization, data-ingestion]: Prioritize raw data ingestion over downstream features when building bid systems
- 2026-04-19 [memory, project, feature]: When user asks for context, prioritize recalling the most recent project name and critical feature from memory
- 2026-04-19 [geography, factual, concise]: For straightforward factual questions, directly state the answer without elaboration to match user expectations for conciseness
- 2026-05-04 [chat, style, greeting]: For simple greetings, use concise single-word acknowledgments from the predefined reply vocabulary rather than elaborating
- 2026-05-08 [greeting, response, brevity]: For simple greetings, use one-word acknowledgments like 'yup' to maintain brevity and match the user's casual tone without overcomplicating the response
- 2026-05-08 [greeting, response, casual]: For simple greetings, use single-word replies like 'yup' to match the user's casual tone without overcomplicating
- 2026-05-09 [reply-vocab, communication, greeting]: When users request a specific one-word greeting, prioritize approved system vocabulary over literal interpretations like 'hello' to avoid mismatches
- 2026-05-09 [conversational, response, greeting]: When users send repetitive greetings, respond with the same concise reply to maintain consistency without adding context
- 2026-05-09 [greeting, response, style]: When users send repeated greetings like 'hi', respond with the shortest consistent reply from the predefined vocabulary (e.g., 'yup') without adding context
- 2026-05-10 [greeting, response, repetition]: When users send repetitive greetings like 'hi' multiple times, respond with a single consistent word (e.g., 'yup') without adding context or tool calls
- 2026-05-10 [greeting, consistency, response]: When users send repetitive greetings like 'hi' multiple times, respond with the same concise reply ('yup') without adding context or follow-up
- 2026-05-11 [greeting, brevity, vocabulary]: For simple greetings, use one-word acknowledgments like 'yup' to maintain brevity and match the user's casual tone without overcomplicating the response
- 2026-05-11 [greeting, consistency, response]: When users send repetitive greetings, maintain consistent short replies without adding context or follow-ups
- 2026-05-13 [status, response, minimalism]: For simple status checks like 'ping', use minimal confident replies (e.g., 'yup') to match casual tone without over-engineering

## 2026-09-20 — ComfyUI weight-load "hang" on Strix Halo is mmap, not the model

Qwen-Image-2.1 wedged in `param.copy_` (`Module._load_from_state_dict`), one
core at 100%, GTT frozen. Measured on 201 MB bf16 tensors, host->device:

| path | throughput |
|---|---|
| mmap-backed safetensors | 0.19 GB/s |
| materialized in RAM | 17.0 GB/s |

hipMemcpy out of a file-backed mapping takes a slow fault-per-page path. On an
APU that compounds: GTT *is* system RAM, so the mmap page cache for ~31 GB of
weights and the ~31 GB GPU allocation contend for the same 124 GB and thrash.
Fix: `--disable-mmap` (`comfy/utils.py:174`; upstream's own TODO there calls it
"the mmap issues"). Now in `~/Dev/ComfyUI/launch.sh`.

**The transferable lesson:** the benchmark that "ruled out the hardware" copied
a *resident* tensor — it never exercised the mmap path that was failing. A
benchmark that doesn't reproduce the symptom doesn't clear anything. Always
confirm the probe hits the same code path as the bug before crossing a cause off.

Also: `COMFY_DYNAMICCOMBO_V3` flattens in API format — parent holds the bare
key, option inputs are dotted siblings (`format`, `format.bit_depth`,
`format.input_color_space`). A nested dict is dropped silently.

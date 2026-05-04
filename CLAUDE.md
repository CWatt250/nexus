# Nexus Agent Workspace

This directory is the Nexus agent workspace. Everything you do here operates under the Nexus identity and conventions.

## Before starting any work
Always read these two files first:
1. `~/AI_Agent/SOUL.md` — identity, values, operating principles
2. `~/AI_Agent/STYLE.md` — communication style rules

Do not skip this. They define how you behave in this workspace.

## Project layout
- All projects live under `~/AI_Agent/projects/`
- Each project has a `wiki/` subfolder with: roadmap.md, decisions.md, architecture.md, tasks.md, lessons-learned.md, runbook.md, scratchpad.md
- Each project has a `run-log.jsonl` at its root

## Creating new projects
Use `~/AI_Agent/new-project.sh <name>` — don't scaffold by hand.

## Run log
After completing a task in any project, append one JSON line to that project's `run-log.jsonl`. Minimum fields: `ts` (ISO-8601), `task`, `result`, `notes`. Append only — never rewrite.

## Host environment
- Machine: WattBott
- OS: Ubuntu 24.04
- GPU stack: ROCm
- Local inference: Ollama

Prefer local tools (Ollama, ROCm-aware libs) over cloud services when a local option exists.

## Git workflow
This workspace is a git repo. Nexus auto-commits after every turn (see `git_sync.py`), staging only content paths: `projects/`, `memory/lessons.md`, `memory/improvements.md`, `memory/patterns.md`. Runtime state (`memory/checkpoints.db`, `memory/current_thread.txt`, `memory/sessions.json`, `designs/`, `venv/`, `chroma/`, `__pycache__/`) is `.gitignore`'d.

Commit identity is injected per-command (`nexus <nexus@wattbott.local>`) — nothing lives in `~/.gitconfig` or the repo's stored config.

When you (Claude Code) modify files in this workspace, create a commit with a descriptive message before finishing the task. The auto-commit helper only stages the paths above, so any code changes outside those paths (e.g. `nexus.py`, `tools/*.py`, `*.md` at the root) need a manual `git add <path> && git commit`. Use `git_sync.get_log(n)` or `python3 ~/AI_Agent/git_sync.py log` to check recent activity.

If the user asks about history or drift, `git log --oneline` is the fastest read. Don't force-push. Don't rewrite pushed commits.

## Phase 11 — Autonomous coding agent (what's already here)
Nexus can now act as a coding agent on any repo, not just chat. The tools live in `~/AI_Agent/tools/`:

- **Codebase indexer** — `tools/codebase_tool.py`: `index_codebase(repo)` reads every git-tracked file, extracts symbols / imports / routes / deps, stores per-file previews in a dedicated Chroma collection (`nexus-codebase`), and writes a `NEXUS.md` summary at the repo root. Companions: `search_codebase`, `get_file_context`, `list_repo_structure`.
- **Test runner** — `tools/test_runner_tool.py`: auto-detects pytest / jest / vitest / cargo / go, runs with timeout, parses `N passed, M failed` + the failing test names. Python is invoked via `sys.executable -m pytest` so it works without pytest on `$PATH`. Entry points: `run_tests`, `run_specific_test`, `watch_tests`.
- **Diff reviewer** — `tools/diff_tool.py`: `get_diff`, `review_diff` (sends the diff to qwen3.6 as a senior-engineer review), `approve_diff` (commits only when the review is clean).
- **Autonomous coding loop** — `tools/coding_agent.py → solve_coding_task(task, repo, max_iterations=10)`: index → plan → baseline tests → iterate (LLM-driven JSON edit plans applied via exact string replace) → diff review → commit → optional PR on feature branches → Sparky card + Telegram notify. Also exposed as LangGraph tool `solve_task`.
- **Repo watcher** — `tools/repo_watcher.py → on_commit(repo)`: auto re-indexes any repo under `~/Dev` whenever the existing `nexus-git-watcher` service sees a new commit, and caches `NEXUS.md` to `memory/nexus_md/<repo>.md` so Nexus can pull it into context.
- **CLI mode** — `python3 ~/AI_Agent/nexus.py --code "<task>" --repo <path>` runs the full coding loop headless and writes a markdown report to `memory/coding-sessions/YYYY-MM-DD-HH-MM-<repo>.md`.
- **Integration test repo** — `test_repos/hello_world/` (buggy `add()` fixed end-to-end: 3/3 tests pass, committed `baf5ab97`).

Every tool is registered in both `nexus.TOOLS` and `mcp/server.py`. Total tool count after this phase: **75 native + MCP**.

## Phases 28 + 29 — Coding Router (slash-command tier ladder)
When you (or an automated path) need to dispatch coding work, prefer
the slash commands over the legacy `dispatch:` prefix. Phase 29 made
`/max` the default for complex builds because Colton already pays for
a Claude Max subscription, so the API-key path is now a fallback —
not the first choice.

Tier ladder (cheapest marginal cost first):

| Slash | Backend | Marginal cost | When to use |
|-------|---------|---------------|-------------|
| `/max` | Claude Sonnet 4.6 via Max plan | $0 | **Default for complex builds.** Multi-file work, refactors, anything you'd reach for Claude Code on. |
| `/local` | qwen3-coder:30b via Ollama | $0 | Offline work, simple builds, "make a quick X". |
| `/quick` | qwen3:4b chat | $0 | One-shot Q&A, no thinking trace, no tools. Not for code. |
| `/code` | DeepSeek V4-Flash | ~$0.005 | Save Max plan quota when the build is small + cheap. |
| `/pro` | DeepSeek V4-Pro | ~$0.05 | DeepSeek mid-tier; rarely needed. |
| `/api` | Sonnet 4.6 via API key | ~$0.10–1.00 | Fallback when Max session limits hit. Spends real $$. |
| `/real` | _alias for `/api`_ | _same as /api_ | DEPRECATED — logs to `cc_logs/_deprecation.log`. Update muscle memory. |

Routing without an explicit slash:
- Casual chat → `/quick` (qwen3:4b, fast no-thinking)
- `make a quick/simple/tiny X` → `/local` (qwen3-coder:30b)
- `build me X` / `create X` / `make me X` / `code X` → `/max`
  (Phase 29 default — was `/code` in Phase 28)

Cost guardrails live in `config/cost_limits.yaml` with tier-specific
ceilings (`max`/`local`/`quick` are uncapped). Daily ceiling applies
to paid tiers only (`flash`/`pro`/`api`). Edit the YAML to change.

Cumulative router stats are auto-rewritten to
`wiki/entities/coding-router.md` after every dispatch — query with
`wiki coding router` from Telegram.

---

# Karpathy Coding Principles
_source: https://raw.githubusercontent.com/forrestchang/andrej-karpathy-skills/main/CLAUDE.md_

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
# NEXUS MASTER BUILD CONTEXT
# WattBott — AMD Ryzen AI Max+ 395, 128GB, Ubuntu 24.04
# Agent: Nexus (LangGraph + Ollama + Qwen3.6)
# Owner: Colton Watt — Project Estimator, Irex Argus
# Mission: Build Nexus into a fully autonomous AI agent stack

## RULES — READ BEFORE EVERY TASK
- Work through phases in order, top to bottom
- Never stop between tasks — keep going until everything is done
- After every task, update CHANGELOG.md with what was completed
- Read CHANGELOG.md at the start of every session to know where you left off
- Write all service files to /tmp/ — never run sudo yourself
- Collect ALL sudo commands into /tmp/sudo-commands.sh at the end of each phase
- If a pip install fails, try with --break-system-packages flag
- All Python goes into ~/AI_Agent/venv
- All new tools go into ~/AI_Agent/tools/
- All new services go into ~/AI_Agent/safety/ or ~/AI_Agent/tools/
- Commit after every phase with a descriptive message
- If something fails 3 times, skip it, log it in CHANGELOG.md, and move on
- Never touch existing working services unless the task specifically requires it
- Test everything before marking complete

## ENVIRONMENT
- venv: ~/AI_Agent/venv
- main agent: ~/AI_Agent/nexus.py
- API server: ~/AI_Agent/nexus_api.py
- tools dir: ~/AI_Agent/tools/
- safety dir: ~/AI_Agent/safety/
- memory dir: ~/AI_Agent/memory/
- models dir: ~/AI_Agent/models/
- mcp dir: ~/AI_Agent/mcp/
- projects dir: ~/AI_Agent/projects/
- .env file: ~/AI_Agent/.env
- SOUL.md: ~/AI_Agent/SOUL.md
- roadmap: ~/AI_Agent/projects/nexus-core/wiki/roadmap.md
- Ollama running on port 11434
- Nexus API running on port 11435
- Open WebUI running on port 8080
- Nexus Design running on port 11436
- Tailscale IP: 100.124.210.84

## KARPATHY PRINCIPLES (always follow)
- Write simple, readable code over clever code
- Delete code that isn't needed
- Don't abstract prematurely
- Test at every step
- Read the error message carefully before fixing
- If confused, add print statements and re-read the output

---

## CHANGELOG
<!-- Claude Code updates this file after every completed task -->
<!-- Format: [PHASE] [TASK] [STATUS] [NOTES] -->

---

## PHASE 4 — VOICE SYSTEM
Check if already complete by reading existing tools/whisper_tool.py and 
tools/tts_tool.py. If they exist and are registered in nexus.py, skip to Phase 5.

### Task 4.1 — Whisper STT
- Install: ~/AI_Agent/venv/bin/pip install faster-whisper sounddevice soundfile numpy
- Create ~/AI_Agent/tools/whisper_tool.py
- Uses faster-whisper base model
- record_and_transcribe() — records mic up to 30s, stops on silence, returns text
- transcribe_file(path) — transcribes audio file
- Model saves to ~/AI_Agent/models/whisper/
- Register as LangGraph tool in nexus.py

### Task 4.2 — Kokoro TTS
- Install: ~/AI_Agent/venv/bin/pip install kokoro-onnx sounddevice
- Create ~/AI_Agent/tools/tts_tool.py
- speak(text) — converts text to speech, plays through speakers
- save_audio(text, path) — saves speech to file
- Default voice: af_heart
- Model saves to ~/AI_Agent/models/kokoro/
- Register as LangGraph tool in nexus.py

### Task 4.3 — Voice Loop
- Create ~/AI_Agent/voice_loop.py
- Press Enter to start recording
- Whisper transcribes
- Nexus processes through full agent pipeline
- Kokoro speaks response
- Loops until user types quit
- Run with: python3 ~/AI_Agent/voice_loop.py

---

## PHASE 5 — KNOWLEDGE & RESEARCH
Check what's already built before starting each task.

### Task 5.1 — Brave Search Tool
- Install: ~/AI_Agent/venv/bin/pip install httpx
- Create ~/AI_Agent/tools/brave_search_tool.py
- Reads BRAVE_SEARCH_API_KEY from ~/AI_Agent/.env
- brave_search(query, count=5) — returns results with title, URL, snippet
- brave_search_news(query) — searches news specifically
- If no API key: return clear message "Add BRAVE_SEARCH_API_KEY to .env to enable web search"
- Register both tools in nexus.py

### Task 5.2 — YouTube Transcript Tool
- Install: ~/AI_Agent/venv/bin/pip install youtube-transcript-api
- Create ~/AI_Agent/tools/youtube_tool.py
- youtube_transcript(url) — extracts full transcript from YouTube URL
- youtube_summary(url) — extracts transcript then summarizes with qwen3:4b
- Register both in nexus.py

### Task 5.3 — Nexus Chronicle
- Install deps: write "sudo apt install -y scrot tesseract-ocr" to /tmp/sudo-commands.sh
- Install pip: ~/AI_Agent/venv/bin/pip install pytesseract Pillow
- Create ~/AI_Agent/tools/chronicle.py
- Screenshots every 5 minutes using scrot
- pytesseract OCR extracts text
- qwen3:4b summarizes what user is working on in 2-3 sentences
- Saves to ~/AI_Agent/memory/chronicle/YYYY-MM-DD.md with timestamp
- Stores in Chroma RAG tagged as chronicle
- Skips if text under 50 chars
- Make systemd service: nexus-chronicle
- Write to /tmp/nexus-chronicle.service
- Add sudo commands to /tmp/sudo-commands.sh

---

## PHASE 6 — NOTIFICATIONS & PHONE CONTROL

### Task 6.1 — Telegram Bot Integration
- Install: ~/AI_Agent/venv/bin/pip install python-telegram-bot
- Create ~/AI_Agent/tools/telegram_tool.py
- Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from ~/AI_Agent/.env
- telegram_notify(message) — sends message to Colton's Telegram
- telegram_send_file(path, caption) — sends file with caption
- Nexus automatically notifies via Telegram when:
  - A long task completes
  - A service crashes and restarts
  - A new GitHub PR is opened
  - An error occurs that needs attention
  - sudo commands need to be run manually
- Register in nexus.py
- Add TELEGRAM_BOT_TOKEN= and TELEGRAM_CHAT_ID= placeholders to .env.example
- Make nexus-telegram a background listener service that receives commands from Telegram and routes them to Nexus
- Write to /tmp/nexus-telegram.service
- Add sudo commands to /tmp/sudo-commands.sh
- Document setup steps in ~/AI_Agent/docs/telegram-setup.md:
  1. Message @BotFather on Telegram to create bot
  2. Get bot token
  3. Message @userinfobot to get chat ID
  4. Add both to ~/AI_Agent/.env

### Task 6.2 — Context Compression
- Create ~/AI_Agent/tools/context_compressor.py
- Runs every 10 conversation turns automatically
- Compresses full conversation history to 500-token summary via qwen3:4b
- Replaces old messages with compressed summary
- Logs to ~/AI_Agent/memory/compression-log.md
- Wire into nexus.py conversation loop

### Task 6.3 — Weekly Pattern Digest
- Upgrade ~/AI_Agent/memory/patterns.py to track:
  - Most common time of day Nexus is used
  - Most frequent topics from reflections
  - Average response quality trend
  - Most used GitHub repos
  - Most frequently read/written files
- Output weekly digest to ~/AI_Agent/memory/weekly-digest.md every Monday 6am
- Write systemd timer files to /tmp/nexus-patterns.service and /tmp/nexus-patterns.timer
- Add to /tmp/sudo-commands.sh

---

## PHASE 7 — COMPUTER USE & MEDIA

### Task 7.1 — Computer Use (langgraph-cua-py)
- Install: ~/AI_Agent/venv/bin/pip install langgraph-cua-py pyautogui pillow
- Create ~/AI_Agent/tools/computer_use_tool.py
- mouse_move(x, y) — moves mouse to coordinates
- mouse_click(x, y, button) — clicks at coordinates
- mouse_drag(x1, y1, x2, y2) — drags
- keyboard_type(text) — types text
- keyboard_press(key) — presses key (enter, escape, etc)
- screenshot() — takes screenshot and returns as base64
- find_on_screen(description) — uses vision to find UI element, returns coordinates
- open_app(name) — opens application by name
- Register all in nexus.py with safety guardrails — always confirm before destructive actions
- Wire screenshot into Chronicle pipeline

### Task 7.2 — ERNIE Image Generation
- Install: ~/AI_Agent/venv/bin/pip install requests
- Create ~/AI_Agent/tools/image_gen_tool.py
- Reads ERNIE_API_KEY from ~/AI_Agent/.env (add placeholder to .env.example)
- generate_image(prompt, size, style) — generates image, saves to ~/AI_Agent/output/images/
- If no API key, document alternative: use local Stable Diffusion via Ollama when available
- Register in nexus.py

### Task 7.3 — OpenGame Integration
- Install OpenGame: git clone https://github.com/leigest519/OpenGame.git ~/AI_Agent/tools/OpenGame && cd ~/AI_Agent/tools/OpenGame && npm install && npm run build && npm link
- Create ~/AI_Agent/tools/opengame_tool.py
- opengame_create(prompt, output_dir) — generates complete playable web game from prompt
- Saves game to ~/AI_Agent/output/games/{game_name}/
- Returns path to generated index.html
- Register in nexus.py

### Task 7.4 — Vercel Deploy Tool
- Install: npm install -g vercel (write sudo npm install -g vercel to /tmp/sudo-commands.sh)
- Create ~/AI_Agent/tools/vercel_tool.py
- Reads VERCEL_TOKEN from ~/AI_Agent/.env (add placeholder)
- vercel_deploy(project_dir, project_name) — deploys project to Vercel, returns URL
- vercel_list_deployments() — lists recent deployments
- Register in nexus.py

---

## PHASE 8 — SPARKY AVATAR SYSTEM

### Task 8.1 — Design Sparky
Create ~/AI_Agent/sparky/ directory for all avatar files.

Design Sparky as an SVG sprite sheet with these specifications:
- Small round creature, approximately 200x200px canvas per frame
- Electric blue (#0EA5E9) body, white highlights, yellow (#FACC15) accent details
- Lightning bolt marking on chest in yellow
- Big round eyes — primary expression vehicle, pupils shift to track direction
- Simple curved mouth — animates between closed smile, open talking O shape, frown
- Stubby little arms and legs
- Friendly, cute, slightly mischievous energy

Create ~/AI_Agent/sparky/sparky.svg with all expression frames:
- idle: gentle floating bob, slow blink
- thinking: eyes look up-left, pupils swirl
- idea: eyes wide, tiny bounce
- whammy: charging (shake), eyes glow yellow, lightning bolt shoots forward, smoke puff
- happy: big grin, squinty eyes, small hop
- excited: vibrating body, sparks fly off
- working: focused squint, determined brow furrow
- sleeping: droopy eyes, zzz float up
- error: red tint flash, head shake
- looking_left: pupils shift left
- looking_right: pupils shift right
- looking_up: pupils shift up
- talking: mouth open/close cycle

Create ~/AI_Agent/sparky/sparky_animations.json defining all animation states,
transitions, durations, and trigger conditions.

### Task 8.2 — Sparky Desktop Overlay
- Install: npm install -g electron (write to /tmp/sudo-commands.sh)
- Create ~/AI_Agent/sparky/overlay/ as Electron app
- Always-on-top transparent window, 200x200px
- Renders Sparky SVG with CSS animations
- Tracks cursor position — Sparky's eyes follow the cursor within 200px radius
- Floats in bottom-right corner of screen by default
- Draggable to any screen position
- Double-click to hide/show
- Emoji bubbles appear above Sparky based on state:
  - 💭 when thinking
  - 💡 when idea/planning
  - ⚡ during WHAMMY
  - 🔥 when excited
  - 💤 when idle too long
  - ❌ on error

### Task 8.3 — Nexus State Bridge
- Create ~/AI_Agent/sparky/state_bridge.py
- FastAPI server on port 11437
- Nexus posts its current state here after every action
- Electron overlay polls this endpoint every 500ms
- States: idle, thinking, working, excited, error, sleeping, whammy
- WHAMMY state triggers when Nexus receives a complex multi-step task
- Wire state_bridge.py calls into nexus.py at key execution points:
  - Start of thinking: post thinking state
  - Tool call start: post working state
  - Long task detected: post whammy state with WHAMMY animation trigger
  - Task complete: post happy state
  - Error: post error state
  - No activity 5 min: post sleeping state

### Task 8.4 — Sparky Voice Sync
- Wire Kokoro TTS output into Sparky mouth animation
- When Nexus speaks, Sparky's mouth opens and closes in sync
- Simple amplitude-based approach — detect audio chunks, toggle mouth open/closed
- Sparky talking state triggers during all TTS playback

### Task 8.5 — Sparky Systemd Service
- Make Sparky overlay auto-start on Ubuntu login
- Write ~/.config/autostart/sparky.desktop file
- Add startup command to /tmp/sudo-commands.sh if needed

---

## PHASE 9 — MULTI-AGENT SWARMS

### Task 9.1 — Nexus Orchestrator
- Create ~/AI_Agent/agents/orchestrator.py
- Top-level agent that receives tasks and delegates to sub-agents
- Maintains task queue in ~/AI_Agent/memory/task-queue.json
- Routes tasks based on type: coding→Coder, research→Researcher, builds→Builder, design→Designer
- Tracks all running agents and their status
- Reports progress to Telegram when tasks complete

### Task 9.2 — Sub-Agent Framework
- Create ~/AI_Agent/agents/base_agent.py — base class all sub-agents inherit
- Create ~/AI_Agent/agents/coder_agent.py — coding tasks, uses qwen3.6, coding system prompt
- Create ~/AI_Agent/agents/researcher_agent.py — research tasks, uses Brave Search + browser
- Create ~/AI_Agent/agents/builder_agent.py — builds, tests, deploys
- Create ~/AI_Agent/agents/designer_agent.py — connected to Nexus Design Studio
- All agents share Chroma RAG memory pool
- All agents can hand off tasks to each other
- All agents send status updates to orchestrator

### Task 9.3 — Agent Dashboard
- Add /agents endpoint to nexus_api.py
- Returns JSON of all running agents, their current task, and status
- Open WebUI can display this dashboard

---

## PHASE 10 — GAME DEVELOPMENT STUDIO

### Task 10.1 — Godot Integration
- Install Godot 5 headless: write install commands to /tmp/sudo-commands.sh
- Create ~/AI_Agent/tools/godot_tool.py
- godot_create_project(name, template) — creates new Godot project
- godot_run_export(project_dir, platform) — exports game for web/desktop
- godot_run_headless(project_dir) — runs project headless for testing
- Register in nexus.py

### Task 10.2 — AudioCraft Sound Generation
- Install: ~/AI_Agent/venv/bin/pip install audiocraft
- Create ~/AI_Agent/tools/audio_gen_tool.py
- generate_sfx(prompt, duration) — generates sound effect, saves to ~/AI_Agent/output/audio/
- generate_music(prompt, duration) — generates background music track
- Register in nexus.py

### Task 10.3 — Bark Voice Acting
- Install: ~/AI_Agent/venv/bin/pip install bark
- Create ~/AI_Agent/tools/bark_tool.py
- bark_speak(text, voice_preset) — generates character voice acting
- Available presets: v2/en_speaker_0 through v2/en_speaker_9
- Saves to ~/AI_Agent/output/audio/voices/
- Register in nexus.py

### Task 10.4 — Game Pipeline Orchestrator
- Create ~/AI_Agent/tools/game_pipeline.py
- create_game(prompt) — full end-to-end pipeline:
  1. Generate game design document
  2. Call OpenGame to scaffold the code
  3. Generate all sprites via ERNIE-Image
  4. Generate all SFX via AudioCraft
  5. Generate background music via MusicGen
  6. Generate voice acting via Bark
  7. Assemble in Godot or web output
  8. Deploy to GitHub Pages via GitHub tool
  9. Notify Colton via Telegram with play link
- Register in nexus.py

---

## FINAL TASKS (run after all phases complete)

### F1 — Install nonstop-agent
- Install: pip install nonstop-agent OR git clone https://github.com/seolcoding/nonstop-agent
- Configure to point at ~/AI_Agent/
- Document run command in ~/AI_Agent/docs/autonomous-run.md

### F2 — Claude Code Routines Setup
- Create ~/AI_Agent/docs/routines-setup.md documenting:
  - How to set up Claude Code Routines via /schedule command
  - How to trigger via GitHub webhooks
  - Recommended routine: run full nexus health check every morning at 6am
  - Recommended routine: weekly pattern digest every Monday

### F3 — Master Sudo Commands
- Collect every sudo command from all /tmp/sudo-*.sh files into one master file
- Write to ~/AI_Agent/SUDO_COMMANDS.sh
- Format clearly with comments explaining each command
- This is what Colton runs manually to activate everything

### F4 — Update Roadmap
- Mark all completed phases in ~/AI_Agent/projects/nexus-core/wiki/roadmap.md
- Add completion dates
- Note any tasks that were skipped and why

### F5 — Final Commit
- git add -A
- git commit -m "feat: phases 4-10 complete — voice, notifications, sparky avatar, multi-agent, game studio"
- git push origin main if remote is configured

### F6 — Final Report
- Write ~/AI_Agent/BUILD_REPORT.md with:
  - Every task completed
  - Every task skipped and why
  - All sudo commands still needed
  - All API keys still needed
  - What to test first when Colton gets home
  - Current tool count
  - Services running vs pending

---

## WHEN YOU FINISH EVERYTHING
Send a Telegram message to Colton (if token is configured) with:
"🔥 Sparky here. Build complete. X tasks done, Y skipped.
Check ~/AI_Agent/BUILD_REPORT.md for full details.
Run ~/AI_Agent/SUDO_COMMANDS.sh to activate everything.
WHAMMY. ⚡"

If Telegram isn't configured yet, write that message to
~/AI_Agent/BUILD_COMPLETE.txt instead.

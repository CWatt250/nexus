# Nexus External Integrations — Master Map

_Last audited: 2026-04-29 (R5 / SearXNG bring-up; corrected after Telegram-active feedback)_

> **Audit correction (2026-04-29 second pass):** the first pass marked
> `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as missing because
> `core/secrets.py` had a parser bug — it tried `:` as the key/value
> separator before `=`, so any `.env` line whose value contained a
> colon (Telegram bot tokens are `<bot_id>:<auth>`) had its key
> corrupted. `tools/telegram_listener.py` and `tools/telegram_tool.py`
> use `python-dotenv` directly so they were never affected — the
> Telegram surface has been live the whole time. Parser fixed in this
> pass; all four `.env` keys now resolve correctly via
> `core.secrets.get()`. github-mcp also re-rated OPTIONAL given the 9
> native authenticated GitHub tools cover the same ground.

Single source of truth for every external service, API key, MCP server, CLI tool, and local service Nexus depends on — across current functionality and the locked roadmap.

If a "command not found" or "missing key" error appears, this doc tells you what's needed, where to get it, what tier of priority it sits at, and how to fix it.

Companion docs: `DEPENDENCIES.md` (apt/pip/node/ollama package detail), `docs/searxng-setup.md`.

---

## SECTION 1 — API KEYS & SECRETS

Stored in `~/AI_Agent/config/secrets.yaml` (preferred, gitignored), with fallback to `~/AI_Agent/.env`. Lookup is `core.secrets.get(KEY)`.

| Key | Service | Status | Tier | Used by | Free tier | Get it from | Cost @ Colton load |
|---|---|---|---|---|---|---|---|
| `GITHUB_PAT` | GitHub | ✅ Configured (`config/secrets.yaml`, 93 chars) | CRITICAL | All `github_*` tools, future PR/issue automation | Free unlimited (auth'd: 5k req/h) | github.com/settings/personal-access-tokens (fine-grained) | $0 |
| `GITHUB_TOKEN` | GitHub | ✅ Configured (`.env`, 40 chars — classic PAT) | OPTIONAL | Legacy fallback path in `tools/github_tool.py` (PAT preferred). Keep both wired so a fine-grained-token failure can fall through to the classic. | Free | same | $0 |
| `TELEGRAM_BOT_TOKEN` | Telegram | ✅ Configured (`.env`, 46 chars — actively serving Telegram chats from phone) | CRITICAL | telegram_tool, telegram_listener, task_notifier | Free | message @BotFather → /newbot | $0 |
| `TELEGRAM_CHAT_ID` | Telegram | ✅ Configured (`.env`, 10 chars) | CRITICAL | All Telegram message routing | Free | message @userinfobot for your chat ID | $0 |
| `BRAVE_SEARCH_API_KEY` | Brave Search | ❌ Missing (deprecated by SearXNG) | OPTIONAL | `brave_search`, fallback in `web_search()` chain | 2k queries/mo free | brave.com/search/api/ | $0 / ~$5 if you hit free tier |
| `TAVILY_API_KEY` | Tavily | ❌ Missing (stub only — no client wired) | OPTIONAL | First slot in `web_search()` priority chain (overrides Brave + SearXNG) | 1k req/mo free | tavily.com (signup) | $0 free, then $30/mo |
| `Z_AI_API_KEY` | Zhipu / Z.ai (GLM) | ❌ Missing (`.env` has key, empty) | IMPORTANT | `glm_tool` (Phase 14.6 escalation when local fails 3 retries) | Trial credits | open.bigmodel.cn / z.ai console | ~$0.60/M in, $2.20/M out — typically <$5/mo |
| `ANTHROPIC_API_KEY` | Anthropic | ❌ Missing | OPTIONAL | Not currently called by any tool. claude-agent-sdk is installed but Nexus runs through Ollama. | n/a | console.anthropic.com | n/a |
| `OPENAI_API_KEY` | OpenAI | ❌ Missing | OPTIONAL | Not currently called. `openai` pkg installed transitively (likely langchain-community pull). | $5 trial | platform.openai.com | n/a unless wired |
| `DEEPSEEK_API_KEY` | DeepSeek | ❌ Missing (no code path) | IMPORTANT (Phase 14.6 v2) | Not yet wired — roadmap mentions DeepSeek V4 Flash as escalation alternative to GLM | $0 free trial | platform.deepseek.com | ~$0.14/M in, $0.28/M out — pennies/mo |
| `HIGGSFIELD_API_KEY` | Higgsfield | ❌ Missing (no code path) | IMPORTANT (Phase 21) | Not yet wired — Phase 21 video/image gen | Limited free | higgsfield.ai | TBD |
| `ERNIE_API_KEY` | Baidu ERNIE | ❌ Missing (`image_gen_tool.py:16`) | OPTIONAL | `image_gen_tool` (image generation). Will fall back to error-string until set. | Limited | ai.baidu.com | per-image, varies |
| `HF_TOKEN` | HuggingFace | ❌ Missing | OPTIONAL | Faster model downloads + access to gated repos. sentence-transformers warns about it but works without. | Free | huggingface.co/settings/tokens | $0 |
| `NOTION_API_KEY` / `NOTION_TOKEN` | Notion | ❌ Missing (`tools/notion_sync.py:28`) | IMPORTANT (Phase 18.x) | `notion_sync` tool (queued — sync Notion → RAG) | Free for personal | notion.so/my-integrations | $0 |
| `NOTION_DATABASE_ID` | Notion | ❌ Missing | IMPORTANT (Phase 18.x) | `notion_sync` target database | Free | from a Notion DB share URL | $0 |
| `BIDWATT_SUPABASE_URL` / `BIDWATT_SUPABASE_ANON_KEY` | Supabase (BidWatt) | ❌ Missing (in `.env` template only) | IMPORTANT (Phase 16.4) | `bidwatt_*` tools (read-only Supabase client) | Free | BidWatt project settings → API | $0 |
| `VERCEL_TOKEN` | Vercel | ❌ Missing (`tools/vercel_tool.py:16`) | IMPORTANT | `vercel_deploy` tool — Phase 7.4 / Phase 24 marketplace | Free hobby tier | vercel.com/account/tokens | $0 |
| `TAILSCALE_API_KEY` | Tailscale | ❌ Missing (`.env` placeholder) | OPTIONAL | Future programmatic device management. Tailscale itself runs without an API key. | Free | login.tailscale.com/admin/settings/keys | $0 |
| `WEATHER_API_KEY` | n/a | ✅ Not needed | n/a | No weather tool exists — weather queries go through `web_search` → SearXNG → weather.com scrape | n/a | n/a | $0 |
| `YOUTUBE_API_KEY` | YouTube | ✅ Not needed | n/a | `youtube_transcript_api` doesn't require a key — it scrapes transcript pages | n/a | n/a | $0 |
| `SPARKY_VOICE` | n/a | ⚠️ Optional | OPTIONAL | TTS voice selection (`tts_tool.py:78`) — defaults to `af_heart` if unset | n/a | n/a (just a string) | $0 |

### Secrets file shape today (verified via `core.secrets.get()` after parser fix)

```
~/AI_Agent/config/secrets.yaml      ← GITHUB_PAT (set, 93 chars)
~/AI_Agent/.env                     ← keys present:
                                       GITHUB_TOKEN          (set, 40 chars)
                                       TELEGRAM_BOT_TOKEN    (set, 46 chars) ✅
                                       TELEGRAM_CHAT_ID      (set, 10 chars) ✅
                                       BRAVE_SEARCH_API_KEY  (empty placeholder)
                                       TAILSCALE_API_KEY     (empty placeholder)
                                       Z_AI_API_KEY          (empty placeholder) ← top remaining gap
```

### Recommended priority for filling the remaining gaps

1. **Z_AI_API_KEY** (or DeepSeek key — see Section 6) — unblocks Phase 14.6 escalation (when local model fails 3 retries). Do this week.
2. **BIDWATT_SUPABASE_URL/KEY** — unblocks Phase 16.4 BidWatt tools. Do when you start that phase.
3. **Everything else** — fill as the matching phase comes up.

---

## SECTION 2 — MCP SERVERS

Configured in `~/AI_Agent/mcp/servers.json`. Loaded by `mcp/client.py` at agent startup.

| Server | Status | Tier | Tools provided | Required env | Phase |
|---|---|---|---|---|---|
| `markitdown` | ✅ Connected (1 tool loaded per nexus-agent journal) | KEEP | document → markdown conversion | none | currently active |
| `github` | ⚠️ Configured but auto-skipped — env empty | **OPTIONAL** (downgraded from CRITICAL) | issue/PR/repo ops via @modelcontextprotocol/server-github | `GITHUB_TOKEN` and/or `GITHUB_PERSONAL_ACCESS_TOKEN` (servers.json hardcodes the empty placeholder) | re-evaluate before wiring — see note below |
| Filesystem MCP | ❌ Not configured | sandboxed file ops | n/a (path arg) | Phase 16.10 |
| Obsidian MCP | ❌ Not configured | Obsidian vault read/write | `OBSIDIAN_VAULT_PATH` | Phase 18.7 |
| Excel MCP | ❌ Not configured | xlsx read/write | n/a | Phase 18.8 |
| Higgsfield MCP | ❌ Not configured | image/video gen | `HIGGSFIELD_API_KEY` | Phase 21 |
| Stripe MCP | ❌ Not configured | payments / customer ops | `STRIPE_API_KEY` | Phase 23.2 |
| Supabase MCP | ❌ Not configured | DB schema / row ops | `SUPABASE_ACCESS_TOKEN` + project ref | Phase 23.2 |

### Re-evaluation: do we actually want github-mcp?

Nexus already has 9 native authenticated GitHub tools (`github_auth_status`, `github_commit_file`, `github_create_issue`, `github_create_pr`, `github_create_repo`, `github_get_file`, `github_list_issues`, `github_list_my_repos`, `github_list_repos`) all wired through `core.secrets.get("GITHUB_PAT")`. They cover Colton's day-to-day surface: read a file, push a commit, open a PR, list repos/issues, check auth.

What `@modelcontextprotocol/server-github` *adds* beyond that (per its tool list): code search across all accessible repos, PR file diffs and PR review comments, workflow run inspection, fork-repo, user search, repo-level branch protection edits. Real but niche — not load-bearing for current or near-term work.

Recommendation: **don't wire github-mcp now**. Reasons:

- 100 % feature overlap with native tools for routine ops; pulling in two surfaces just doubles the agent's tool-selection space (more wrong picks, longer prompts, slower routing).
- Native tools pass through `core.secrets.redact()` for log scrubbing. The MCP server doesn't — it logs to its own stderr.
- Wiring it requires either copy-pasting the PAT into `mcp/servers.json` or adding envsubst-style indirection. Both are footguns.

**When to revisit:** the moment you actually need code-search-across-repos, PR-file-diff inspection, or workflow-run inspection. Add a single native `github_search_code` tool then if it's the only missing capability — cheaper than spinning up the whole MCP server.

If you decide to wire it anyway: edit `~/AI_Agent/mcp/servers.json`, paste the PAT into both `GITHUB_TOKEN` and `GITHUB_PERSONAL_ACCESS_TOKEN`, restart `nexus-agent`. You'll see `[mcp] loaded 2 external tools`.

---

## SECTION 3 — CLI TOOLS

Tools that Nexus shells out to (or that the operator runs to maintain Nexus).

| Tool | Status | Version | Used by | If missing |
|---|---|---|---|---|
| `ollama` | ✅ | 0.21.0 | local LLM serving (port 11434) | already installed |
| `docker` | ✅ | 29.4.1 | SearXNG container, future MCP servers | already installed |
| `git` | ✅ | 2.43.0 | repo ops, codebase indexer, auto-commit | apt install git |
| `gh` | ❌ MISSING | n/a | optional — convenience for PR/issue ops from terminal (Nexus uses PyGithub + github-mcp instead, so non-blocking) | apt install gh OR `wget` from cli.github.com |
| `vercel` | ✅ | 52.0.0 | `vercel_tool` deploys | already installed |
| `supabase` | ❌ MISSING | n/a | Phase 16.4 BidWatt + Phase 23.2 marketplace | `npm install -g supabase` |
| `npm` / `node` | ✅ | npm 9.2.0 / node 18.19.1 | MCP servers, electron, vercel, opengame | already installed |
| `pnpm` / `yarn` | ❌ MISSING | n/a | only when a cloned repo pins them | `npm install -g pnpm yarn` |
| `pip` / `pip3` | ✅ | 24.0 | Python pkg mgmt | apt install python3-pip |
| `uv` | ❌ MISSING | n/a | optional — faster pip | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `ffmpeg` | ❌ MISSING | n/a (despite earlier audit false positive) | pydub, faster-whisper audio I/O, future bark/audiocraft | apt install ffmpeg |
| `imagemagick` (`convert`) | ✅ | 6.9.12-98 | `image_gen_tool`, game_pipeline | already installed |
| `gitleaks` | ✅ (just installed) | recent | Phase 12.2 secrets scan | already installed |
| `restic` | ✅ (just installed) | recent | future backups | already installed |
| `claude` | ✅ | 2.1.114 | this CLI (Claude Code) | already installed |
| `tailscale` | ✅ | 1.96.4 | LAN/phone VPN | already installed |
| `playwright` (CLI via venv) | ✅ | 1.58.0 | browser_render, browser_tool | venv/bin/playwright |
| `ripgrep` (`rg`) | ✅ | 14.1.1 | grep_tool, codebase indexer | apt install ripgrep |
| `fdfind` / `fd` | ❌ MISSING | n/a (apt false positive earlier) | optional — fast find | apt install fd-find (binary lands as `fdfind`) |
| `jq` | ✅ | 1.7 | shell scripts, run-log inspection | apt install jq |
| `sqlite3` | ✅ | 3.45.1 | DB inspection from shell | apt install sqlite3 |
| `tesseract` | ✅ | 5.3.4 | Chronicle OCR | apt install tesseract-ocr |
| `scrot` | ✅ | 1.10 | Chronicle screenshots | apt install scrot |
| `xclip` / `xsel` | ✅ | 0.13 | clipboard tool | apt install xclip xsel |

> The earlier `DEPENDENCIES.md` flagged `ffmpeg` and `fd-find` as installed. That was a bug in the dpkg-status check (it returned OK on `un` packages too). Both are corrected here and added to `SUDO_INTEGRATIONS.sh`.

### gh CLI auth

When you install `gh`, run `gh auth login` to authorise it with the same fine-grained PAT (or login via web). The `gh` CLI is **optional** — Nexus uses PyGithub + github-mcp for all programmatic GitHub work — but it's handy for one-off shell ops.

---

## SECTION 4 — LOCAL SERVICES

State as of audit. Check live with `systemctl status <name>`.

| Service | Status | Port | Auto-start | Health endpoint | Notes |
|---|---|---|---|---|---|
| `ollama.service` | ✅ active | 11434 | yes | `curl 127.0.0.1:11434/api/version` | system-installed |
| `nexus-agent.service` | ✅ active | — | yes | journalctl | LangGraph agent |
| `nexus-api.service` | ✅ active | 11435 | yes | `curl 127.0.0.1:11435/health` (or 404 on `/`) | OpenAI-compat API |
| `nexus-task-worker.service` | ✅ active | — | yes | `memory/active_tasks.jsonl` tail | TASK runner |
| `nexus-telegram.service` | ✅ active | — | yes | journalctl | currently no-op until TELEGRAM_BOT_TOKEN set |
| `nexus-watchdog.service` | ✅ active | — | yes | journalctl | service health monitor |
| `nexus-scheduler.service` | ✅ active | — | yes | `memory/scheduled_tasks.db` | cron-style scheduler |
| `nexus-perf-guardian.service` | ✅ active | — | yes | `memory/perf-guardian.jsonl` | RAM/GPU/temp watchdog |
| `nexus-dashboard.service` | ✅ active | 11438 | yes | HTTP 200 on / | React + websocket |
| `nexus-prewarm.service` | ✅ ran (oneshot — inactive=normal) | — | yes | n/a | pre-loads qwen3:4b + qwen3.6 at boot |
| `nexus-searxng.service` | ✅ ran (oneshot — inactive=normal) | 8888 | yes | `curl 127.0.0.1:8888/healthz` | container-managed by docker compose |
| `nexus-chronicle.service` | ⏸️ inactive, disabled | — | no | n/a | screenshot pipeline; enable when you want it |
| `nexus-wakeword.service` | ⏸️ inactive, disabled | — | no | n/a | "Hey Nexus" voice — enable when you've placed the mic |
| Open WebUI | ✅ active | 8080 | (external) | HTTP 200 on / | not in Nexus systemd; runs separately |
| Sparky bridge | ⚪ on-demand | 11437 | no | n/a | spawns from desktop login |

### Timers (cron-equivalent)

| Timer | Next fire | Service it runs |
|---|---|---|
| `nexus-test.timer` | nightly 03:03 | `nexus-test.service` (regression suite) |
| `nexus-eod-summary.timer` | daily 17:00 | `nexus-eod-summary.service` |
| `nexus-patterns.timer` | weekly Mon 06:00 | `nexus-patterns.service` |
| `nexus-lessons.timer` | weekly Mon 08:01 | `nexus-lessons.service` |
| `nexus-model-watcher.timer` | weekly Mon 09:02 | `nexus-model-watcher.service` |

> Two earlier-listed names (`nexus-tools-refresh`, `nexus-eod`) didn't resolve — the actual unit is `nexus-eod-summary` and `nexus-tools-refresh` either was renamed or never made it past the plan. Not blocking — flag it if you want me to dig.

---

## SECTION 5 — BROWSER & WEB TOOLING

| Item | Status | Notes |
|---|---|---|
| Chromium (Playwright managed) | ✅ chromium-1208 in `~/.cache/ms-playwright/` | version 145.0.7632.6 confirmed via live launch |
| Headless-shell variant | ✅ chromium_headless_shell-1208 | for `browser_render`'s networkidle path |
| Webkit (Playwright) | ❌ not installed | not used; cross-browser testing not on roadmap |
| Firefox (Playwright) | ❌ not installed | same |
| Browser extensions | n/a | Nexus drives a fresh context each run; no persistent profile |
| Cookie / session storage | n/a | every Playwright session is ephemeral. If a future tool needs auth (e.g. LinkedIn Sales Nav), we'd add a persistent context dir under `~/AI_Agent/searxng/` (no — that's wrong) — actually under `~/AI_Agent/browser-state/`. Not built yet. |
| User agent string | ✅ desktop Chrome 131 spoof in `browser_render.py` | bypasses bot login walls on x.com / linkedin.com |
| System libs for Chromium | ⚠️ 5 packages flagged missing on 24.04 (libatk-bridge2.0-0, libatk1.0-0, libatspi2.0-0, libcups2, libgtk-3-0) — Chromium currently launches anyway via `*t64` variants. Listed CRITICAL safety-net in `SUDO_DEPENDENCIES.sh`. |

---

## SECTION 6 — AI MODELS

### Local (Ollama) — `ollama list`

| Model | Status | Size | Used for |
|---|---|---|---|
| `qwen3:4b` | ✅ pulled, currently loaded | 2.5 GB | router (`router.py`), conversation handler quick-chat, fast route |
| `qwen3:8b` | ✅ pulled | 5.2 GB | mid route (currently the default mid backstop) |
| `qwen3.6` | ✅ pulled, currently loaded | 23 GB | heavy / code / design route, EOD summary, intent classifier, glm-fallback context window |
| `qwen3:14b` | ✅ pulled | 9.3 GB | reserve heavy fallback (not currently keep-alive) |
| `qwen2.5vl:7b` | ⚠️ pulled but fails to load with `ROCm out of memory` | 6.0 GB | vision (`computer_use_tool.find_on_screen_vision`) — Phase 16.2 |
| `nomic-embed-text` | ✅ pulled | 274 MB | embeddings for Chroma RAG and mem0 |

Disk: 113 GB used / 1.7 TB free — plenty of headroom.

VRAM: shared with system RAM (Radeon 8060S iGPU). The qwen2.5vl OOM happens because qwen3.6 (23 GB), qwen3:4b, and the embedder were all keep_alive at once. **Phase 16.7 Performance Guardian's LRU rules need to allow unloading qwen3.6 when qwen2.5vl is requested**, or the vision path stays broken.

### Cloud (used or planned)

| Provider | Model | Status | Used by | Cost |
|---|---|---|---|---|
| Z.ai (Zhipu) | `glm-4.6` (default in `glm_tool.py`) | ❌ no key set | `glm_consult` — Phase 14.6 escalation | $0.60/M in, $2.20/M out — capped at $50/mo by `BUDGET_USD` env |
| Z.ai (Zhipu) | `glm-5.1` | ❌ no key set, model also pinned in pricing table | same path | same |
| DeepSeek | `deepseek-chat` (V4 flash class) | ❌ NOT WIRED — code path doesn't exist yet | future Phase 14.6 v2 escalation alternative | ~$0.14/M in, $0.28/M out (cheaper than GLM) |
| Anthropic | claude-* | ❌ NOT WIRED — `claude-agent-sdk` is installed but no nexus tool calls it | n/a | n/a |
| OpenAI | gpt-* | ❌ NOT WIRED — `openai` pkg installed transitively, no nexus call site | n/a | n/a |
| Higgsfield | n/a | ❌ NOT WIRED — Phase 21 | image/video gen | TBD |
| ERNIE (Baidu) | n/a | ❌ no key set | `image_gen_tool` | per-image |

### Recommendation on cloud-LLM strategy

The roadmap mentioned both GLM and DeepSeek for Phase 14.6 escalation. Today there's only a GLM client. **DeepSeek is materially cheaper for the same quality on most tasks**; if Phase 14.6 v2 is still open, swap to DeepSeek (or wire both with a router that picks the cheaper one for each call).

---

## SECTION 7 — PYTHON / NODE PACKAGES

Reference `DEPENDENCIES.md` for the full table — re-validated this turn:

- 51 of 52 audit-listed Python imports green. The lone fail (`pyautogui`) is X11 display-auth, not a missing package — works under a logged-in desktop session.
- `aiofiles`, `pytest-asyncio`, `pypdf`, `python-docx`, `structlog` newly installed in the dep audit — all import clean.
- Node globals: `npm`, `node`, `electron`, `vercel`, `@anthropic-ai/claude-code` all present. `pnpm` / `yarn` / `supabase` still missing (covered in Section 3).

No new failing imports surfaced.

---

## SECTION 8 — GAPS PRIORITIZED

### CRITICAL (blocks current functionality)

| Gap | Unlocks | Fix | Cost / time |
|---|---|---|---|
| `ffmpeg` system binary | Pydub / Whisper audio I/O, future TTS pipelines (currently silently broken — pydub warns at import) | `sudo apt install ffmpeg` (in `SUDO_INTEGRATIONS.sh`) | $0 / 30 sec |
| `qwen2.5vl:7b` ROCm OOM | Vision path (`find_on_screen_vision`, Phase 16.2 full computer use) | Configuration: drop `qwen3:14b` from keep-alive list; teach Performance Guardian to unload qwen3.6 when vision is requested | $0 / 30 min code |

### IMPORTANT (blocks queued roadmap)

| Gap | Phase | Fix | Cost / time |
|---|---|---|---|
| `Z_AI_API_KEY` (or DeepSeek key — pick one) | Phase 14.6 escalation | sign up at z.ai or platform.deepseek.com → fill `.env` | $0 trial / 10 min |
| `BIDWATT_SUPABASE_URL` + `BIDWATT_SUPABASE_ANON_KEY` | Phase 16.4 BidWatt tools | Copy from your existing BidWatt project's API settings → `~/AI_Agent/.env` | $0 / 2 min |
| `VERCEL_TOKEN` | Phase 7.4 / 24 deploys | vercel.com/account/tokens → `~/AI_Agent/.env` | $0 / 3 min |
| `supabase` CLI | Phase 16.4 / Phase 23.2 | `sudo npm install -g supabase` (added to `SUDO_INTEGRATIONS.sh`) | $0 / 1 min |
| `NOTION_API_KEY` + `NOTION_DATABASE_ID` | Phase 18.x Notion sync | notion.so/my-integrations → `.env` | $0 / 5 min |
| `HIGGSFIELD_API_KEY` | Phase 21 | higgsfield.ai → `.env` | TBD |

### OPTIONAL (nice-to-have)

| Gap | Why | Fix |
|---|---|---|
| `BRAVE_SEARCH_API_KEY` | Faster + better-ranked search than SearXNG when Tavily isn't on. Still useful as middle-tier in `web_search` chain. | brave.com/search/api → `.env`. $0 free tier covers 2k/mo. |
| `TAVILY_API_KEY` | LLM-tuned snippets (good for research agent in Phase 16.3). Wire the actual client in `tools/search_router.py::_call_tavily`. | tavily.com signup → `.env`. $0 covers 1k/mo. |
| `HF_TOKEN` | Faster model downloads, suppresses sentence-transformers warning, unlocks gated repos. | huggingface.co/settings/tokens → `.env`. |
| `gh` CLI | Convenience only — Nexus uses PyGithub. | apt install gh + `gh auth login` |
| `pnpm` / `yarn` | Only when a cloned repo pins them. | npm install -g (commented in SUDO file) |
| `uv` | Faster Python deps install. | one-line curl install (no sudo needed) |
| 5 Playwright libs (libatk-bridge2.0-0 etc) | Safety net — Chromium already launches on 24.04 t64 variants. | already in `SUDO_DEPENDENCIES.sh` |
| Webkit / Firefox via Playwright | Cross-browser testing — not on roadmap | `playwright install webkit firefox` if ever needed |

---

## SECTION 9 — ACTIONABLE NEXT STEPS

### A) What I (Nexus / Claude Code) can do right now

- ✅ Patched `searxng_health()` — bumped timeout, added `/healthz` fast path. Test suite green (10/10).
- ✅ Generated `SUDO_INTEGRATIONS.sh` with `ffmpeg` + `gh` + `supabase` + `pnpm` + `uv` (uv is no-sudo but documented anyway).
- ✅ Three smoke searches against the live SearXNG container — all returned real results in <3s.
- ✅ Updated `DEPENDENCIES.md` indirectly through this doc (Section 3 corrects the ffmpeg / fd-find false positives).
- ✅ Fixed `core/secrets.py` parser bug — was splitting on `:` before `=`, corrupting any `.env` line whose value contained a colon (Telegram bot tokens, URLs). All four currently-set keys now resolve correctly through `core.secrets.get()`.

### B) What you (Colton) need to do

**Today (~30 sec):**

1. Install ffmpeg + (optional) fd-find / gh / supabase CLI:
   ```bash
   bash ~/AI_Agent/SUDO_INTEGRATIONS.sh
   ```

**This week:**

2. Pick GLM **or** DeepSeek for Phase 14.6 cloud escalation, sign up, drop key in `.env`. DeepSeek is cheaper; GLM has the working Python tool. If you want DeepSeek, ask me to wire it (clean swap on `glm_tool.py`).

3. Drop the BidWatt Supabase URL + anon key when you're ready to start Phase 16.4.

**Defer to phase start:**

4. Notion / Vercel / Higgsfield / ERNIE / HF — only when the matching phase fires. No urgency.

5. github-mcp — only if a real need surfaces that the 9 native tools can't cover. Default plan: leave it off and add a targeted native tool (e.g. `github_search_code`) if/when something specific comes up.

### C) Things to defer until specific phases

| When | What |
|---|---|
| Phase 14.6 v2 (cloud escalation) | Z_AI or DeepSeek key |
| Phase 16.2 (computer use vision) | Fix qwen2.5vl OOM via Performance Guardian rules |
| Phase 16.3 (research agent) | Tavily key (optional but helps) |
| Phase 16.4 (BidWatt) | BIDWATT_SUPABASE_* |
| Phase 16.10 (Filesystem MCP) | add to `mcp/servers.json` |
| Phase 18.7 (Obsidian MCP) | OBSIDIAN_VAULT_PATH + MCP entry |
| Phase 18.8 (Excel MCP) | MCP entry |
| Phase 21 (Higgsfield) | HIGGSFIELD_API_KEY + MCP entry |
| Phase 23.1/23.2 (marketplace + Stripe/Supabase) | STRIPE_API_KEY, SUPABASE_ACCESS_TOKEN, supabase CLI |
| Phase 24 (marketplace) | VERCEL_TOKEN |

---

## Appendix — quick-fix command sheet

```bash
# Test SearXNG live
curl -fsS 'http://127.0.0.1:8888/search?q=test&format=json' | jq '.results[0]'

# Restart all Nexus services after secret-file edits
sudo systemctl restart nexus-agent nexus-api nexus-task-worker nexus-telegram

# Check what's loaded in Ollama right now
ollama ps

# See which MCP tools loaded at last startup
journalctl -u nexus-agent --since '1 hour ago' | grep -i mcp

# Force a specific search backend (for testing)
NEXUS_WEB_SEARCH_FORCE=searxng python3 -c \
  "from tools.search_router import web_search; print(web_search.invoke({'query':'test'}))"
```

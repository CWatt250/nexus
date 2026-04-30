# Nexus Dependencies

_Last audited: 2026-04-29 (Fix #2 follow-up)_

Single source of truth for what Nexus needs at the system, Python, Node, and model layers. If you hit a "command not found" or `ImportError`, this table tells you what to install and why.

## How this is organized

- **Status** — `OK` / `MISS` / `OPT` (optional / not yet wired up).
- **Tier** — `CRITICAL` blocks current functionality; `FUTURE` only matters when the named phase starts.
- **Required for** — names the feature, tool file, or roadmap phase that pulls this in.

To install:

- pip: `~/AI_Agent/venv/bin/pip install <pkg>` (no sudo)
- ollama: `ollama pull <model>` (no sudo)
- Playwright browsers: `~/AI_Agent/venv/bin/playwright install chromium` (no sudo)
- apt + npm-global: run `~/AI_Agent/SUDO_DEPENDENCIES.sh` (sudo)

---

## A. System packages (apt)

| Package | Required for | Status | Tier | Version |
|---|---|---|---|---|
| sqlite3 | checkpoints.db, tasks.db, scheduled_tasks.db | OK | — | 3.45.1 |
| ffmpeg | tts/whisper audio I/O, video tools | OK | — | (installed) |
| curl | external API installs, gh tooling | OK | — | 8.5.0 |
| wget | model downloads, doc fetch | OK | — | 1.21.4 |
| git | repo operations everywhere | OK | — | 2.43.0 |
| build-essential | building ctranslate2 / native wheels | OK | — | 12.10 |
| portaudio19-dev | sounddevice / wakeword listener (workers/wakeword_listener.py) | OK | — | (installed) |
| libnss3 | Playwright / Chromium | OK | — | 3.98 |
| libxss1 | Playwright / Chromium | OK | — | 1.2.3 |
| libasound2t64 | Playwright / Chromium (24.04 renamed from libasound2) | OK | — | 1.2.11 |
| libatk-bridge2.0-0 | Playwright / Chromium | **MISS** | CRITICAL | — |
| libatk1.0-0 | Playwright / Chromium | **MISS** | CRITICAL | — |
| libatspi2.0-0 | Playwright / Chromium | **MISS** | CRITICAL | — |
| libcups2 | Playwright / Chromium | **MISS** | CRITICAL | — |
| libgtk-3-0 | Playwright / Chromium | **MISS** | CRITICAL | — |
| libnspr4 | Playwright / Chromium | OK | — | 4.35 |
| libxcomposite1 | Playwright / Chromium | OK | — | 0.4.5 |
| libxdamage1 | Playwright / Chromium | OK | — | 1.1.6 |
| libxrandr2 | Playwright / Chromium | OK | — | 1.5.2 |
| libgbm1 | Playwright / Chromium | OK | — | 25.2.8 |
| libxkbcommon0 | Playwright / Chromium | OK | — | 1.6.0 |
| libpango-1.0-0 | Playwright / Chromium | OK | — | 1.52.1 |
| libcairo2 | Playwright / Chromium | OK | — | 1.18.0 |
| libxshmfence1 | Playwright / Chromium | OK | — | 1.3 |
| tesseract-ocr | Chronicle OCR (tools/chronicle.py) | OK | — | 5.3.4 |
| xclip | clipboard tool (tools/clipboard_watcher.py) | OK | — | 0.13 |
| xsel | clipboard tool (fallback) | OK | — | (installed) |
| poppler-utils | PDF rendering, markitdown PDF backend | OK | — | 24.02 |
| jq | shell scripts, run-log inspection | OK | — | 1.7.1 |
| ripgrep | grep_tool / codebase indexer | OK | — | (installed) |
| fd-find | find replacement (binary is `fdfind`, symlink to `fd` if you want) | OK | — | (installed) |
| scrot | Chronicle screenshots | OK | — | 1.10 |
| imagemagick | image_gen_tool / game_pipeline image ops | **MISS** | CRITICAL\* | — |
| gitleaks | secrets scan (Phase 12.2) | **MISS** | FUTURE | — |
| trufflehog | secrets scan alternative (no apt pkg, install via curl) | **MISS** | FUTURE | — |
| restic | backups (general hygiene) | **MISS** | FUTURE | — |
| tailscale | VPN to phone / dashboard (mobile UI) | OK | — | (running) |

\*imagemagick is "CRITICAL" only when an image-generation task is queued.

> Note on libasound2: Ubuntu 24.04 uses 64-bit time_t variants — the package is named `libasound2t64`. Chromium picks it up via the same symlinks. Don't `apt install libasound2` (it doesn't exist on noble).

---

## B. Python packages (in `~/AI_Agent/venv`)

### Core agent
| Package | Required for | Status | Tier | Version |
|---|---|---|---|---|
| langgraph | agent graph runtime | OK | — | 1.1.8 |
| langgraph-checkpoint-sqlite | checkpoints.db | OK | — | 3.0.3 |
| aiosqlite | AsyncSqliteSaver (Phase 15.1) | OK | — | 0.22.1 |
| ollama | local LLM client | OK | — | 0.6.1 |
| fastapi | nexus_api.py + state bridge | OK | — | 0.136.0 |
| uvicorn | ASGI server | OK | — | 0.44.0 |
| pydantic | tool schemas, intent models | OK | — | 2.13.2 |
| pyyaml | config/secrets.yaml loader | OK | — | 6.0.3 |
| python-dotenv | .env loader | OK | — | 1.2.2 |
| requests | sync HTTP (legacy paths) | OK | — | 2.33.1 |
| httpx | async HTTP (preferred) | OK | — | 0.28.1 |
| aiohttp | streaming HTTP, websockets | OK | — | 3.13.5 |
| psutil | perf-guardian, system stats | OK | — | 7.2.2 |

### Voice / audio
| Package | Required for | Status | Tier | Version |
|---|---|---|---|---|
| faster-whisper | Whisper STT | OK | — | 1.2.1 |
| kokoro-onnx | Kokoro TTS | OK | — | 0.5.0 |
| sounddevice | mic input | OK | — | 0.5.5 |
| soundfile | wav I/O | OK | — | 0.13.1 |
| pydub | audio segment ops | OK | — | 0.25.1 |
| openwakeword | wake-word listener | OK | — | 0.4.0 |
| numpy | DSP / general | OK | — | 2.4.4 |
| scipy | DSP / FFT for VAD | OK | — | 1.17.1 |

### Web / browser
| Package | Required for | Status | Tier | Version |
|---|---|---|---|---|
| playwright | browser_tool, browser_render | OK | — | 1.58.0 |
| beautifulsoup4 | HTML parsing | OK | — | 4.14.3 |
| lxml | XML/HTML parsing | OK | — | 6.1.0 |

### Documents
| Package | Required for | Status | Tier | Version |
|---|---|---|---|---|
| markitdown | unified doc → markdown | OK | — | 0.1.5 |
| pdfminer.six | PDF text extraction | OK | — | 20251230 |
| pdfplumber | PDF table extraction | OK | — | 0.11.9 |
| pypdf | extra PDF backend (newly installed) | OK | — | 6.10.2 |
| python-docx | Word doc write/read (newly installed) | OK | — | 1.2.0 |
| openpyxl | Excel I/O | OK | — | 3.1.5 |
| python-pptx | PowerPoint I/O | OK | — | 1.0.2 |
| mammoth | docx → markdown | OK | — | 1.11.0 |

### Vision / images
| Package | Required for | Status | Tier | Version |
|---|---|---|---|---|
| pillow | image read/write/resize | OK | — | 12.2.0 |
| pytesseract | OCR (Chronicle) | OK | — | 0.3.13 |
| opencv-python | image ops (none yet) | OPT | FUTURE | — |

### Memory / RAG
| Package | Required for | Status | Tier | Version |
|---|---|---|---|---|
| chromadb | RAG store | OK | — | 1.5.8 |
| sentence-transformers | embeddings | OK | — | 5.4.1 |
| mem0ai | durable facts | OK | — | 2.0.0 |
| qdrant-client | mem0 vector backend | OK | — | 1.17.1 |
| sqlite-vec | sqlite vector index (mem0) | OK | — | 0.1.9 |

### LangChain stack
| Package | Required for | Status | Tier | Version |
|---|---|---|---|---|
| langchain | core LC | OK | — | 1.2.15 |
| langchain-core | core LC | OK | — | 1.3.0 |
| langchain-ollama | Ollama bridge | OK | — | 1.1.0 |
| langchain-community | community tools | OK | — | 0.4.1 |

### MCP / external integrations
| Package | Required for | Status | Tier | Version |
|---|---|---|---|---|
| mcp | MCP server + clients | OK | — | 1.8.1 |
| markitdown-mcp | MCP wrapper around markitdown | OK | — | 0.0.1a4 |
| python-telegram-bot | Telegram listener | OK | — | 22.7 |
| PyGithub | GitHub tools | OK | — | 2.9.1 |
| youtube-transcript-api | YouTube transcripts | OK | — | 1.0.3 |

### Computer use / vision-LLM
| Package | Required for | Status | Tier | Version |
|---|---|---|---|---|
| PyAutoGUI | mouse/keyboard control | OK | — | 0.9.54 |
| pyperclip | clipboard | OK | — | 1.11.0 |
| transformers | tokenizers, vision processors | OK | — | 5.5.4 |
| safetensors | model weights | OK | — | 0.7.0 |

### Newly installed in this audit
| Package | Required for | Status | Tier | Version |
|---|---|---|---|---|
| aiofiles | Phase 13.6 (async file I/O conversion) | OK | — | (installed) |
| pytest-asyncio | async test support (came up in Fix #2) | OK | — | 1.3.0 |
| structlog | structured logging upgrade (optional but listed) | OK | — | 25.5.0 |

### Listed but intentionally skipped
| Package | Why skipped |
|---|---|
| openai-whisper | duplicates `faster-whisper`, slower; faster-whisper is the active backend |
| pyaudio | duplicates `sounddevice` (we standardised on sounddevice) |
| redis | not used; mem0 uses qdrant + sqlite-vec |
| supabase-py | bidwatt_tool talks to Supabase REST via httpx — no SDK needed |
| matplotlib / plotly / gradio / streamlit | dashboard is React + websocket, no Python plotting layer |

---

## C. Node packages (global)

| Package | Required for | Status | Tier | Version |
|---|---|---|---|---|
| node | runtime | OK | — | (installed) |
| npm | package manager | OK | — | (installed) |
| @anthropic-ai/claude-code | this CLI | OK | — | 2.1.114 |
| electron | Sparky overlay (sparky/overlay/) | OK | — | 41.2.2 |
| vercel | Vercel deploy tool | OK | — | 52.0.0 |
| yarn | optional alternate to npm | MISS | FUTURE | — |
| pnpm | optional alternate to npm | MISS | FUTURE | — |

---

## D. Other tooling

| Tool | Required for | Status | Tier | Version |
|---|---|---|---|---|
| ollama | local LLM runtime | OK | — | (port 11434) |
| tailscale | VPN, mobile dashboard access | OK | — | running |
| jq | JSON in shell | OK | — | 1.7.1 |
| rg (ripgrep) | code search | OK | — | (installed) |
| fdfind | fast find (binary is `fdfind` not `fd`) | OK | — | (installed) |
| gitleaks | secrets scan (Phase 12.2) | MISS | FUTURE | — |
| trufflehog | secrets scan (no apt pkg) | MISS | FUTURE | — |
| restic | backups | MISS | FUTURE | — |

---

## E. Ollama models

| Model | Required for | Status | Size |
|---|---|---|---|
| qwen3:4b | router / classifier (`router.py`, `quick_chat`) | OK | 2.5 GB |
| qwen3:8b | mid-tier route | OK | 5.2 GB |
| qwen3.6 | heavy / code / design route, EOD summary, classifier | OK | 23 GB |
| qwen3:14b | reserve heavy fallback | OK | 9.3 GB |
| qwen2.5vl:7b | vision (computer_use_tool find_on_screen_vision) | OK | 6.0 GB |
| nomic-embed-text | embeddings (RAG, mem0) | OK | 274 MB |

All required models are pulled. ~46 GB total — disk: 113 GB used / 1.7 TB free, no pressure.

---

## F. Playwright browsers (`~/.cache/ms-playwright/`)

| Browser | Status |
|---|---|
| chromium-1208 | OK |
| chromium_headless_shell-1208 | OK |
| ffmpeg-1011 | OK |
| webkit | not installed (not used) |
| firefox | not installed (not used) |

`browser_tool` and `browser_render` only target Chromium. Webkit/Firefox would only matter if you needed cross-browser testing — currently irrelevant.

---

## G. Phase-mapped dependency forecast

What the unfinished roadmap items will need:

| Phase | New deps | Already covered? |
|---|---|---|
| Phase 13.6 (async tool conversion) | aiofiles | YES (just installed) |
| Phase 14.5 (regression tests) | pytest, pytest-asyncio | YES (just installed) |
| Phase 14.6 (GLM-5.1 escalation) | httpx (already in) | YES |
| Phase 15.x (Telegram async) | aiosqlite | YES |
| Phase 16.6 (wake word) | openwakeword + sounddevice | YES |
| Phase 16.7 (perf guardian) | psutil | YES |
| Phase 17 (dashboard) | nothing new on Python side; React-side deps live in dashboard repo | YES |
| Phase 18.5 (model watcher) | uses existing `ollama` SDK | YES |
| Phase 19.6 (click-to-chat Sparky) | electron + tts integration | YES |
| Phase 21 (Higgsfield) | likely external API via httpx | YES |
| Phase 23.1 (project scaffolding) | `cookiecutter` if you want templated scaffolds; otherwise stdlib | install on demand |
| Phase 24 (marketplace) | nothing identified | n/a |
| Phase 25 (knowledge garden) | already have markitdown / chroma | YES |
| MCP filesystem (16.10), Obsidian (18.7), Excel (18.8) | mcp + openpyxl + obsidian_sync | YES |

---

## H. Quick install crib sheet

| Need | Command |
|---|---|
| pip install (no sudo) | `~/AI_Agent/venv/bin/pip install <pkg>` |
| Ollama model | `ollama pull <model>` |
| Playwright browser | `~/AI_Agent/venv/bin/playwright install chromium` |
| Playwright system libs | `sudo ~/AI_Agent/venv/bin/playwright install-deps chromium` |
| All apt deps from this audit | `bash ~/AI_Agent/SUDO_DEPENDENCIES.sh` |

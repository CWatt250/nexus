# Wiki Journal

Append-only chronological log of significant wiki changes. One line per change. Newest at top.

Format: `YYYY-MM-DD HH:MM TZ — <page>: <what changed>`

---

2026-06-16 14:54 UTC — Phase 28 dispatch | tier=max | "Code this - " | 31.2s | $0.0000 | done

2026-06-12 03:30 UTC — Phase 39 shipped: Brain + Guardrails Overhaul. gpt-oss:120b is the local brain (35.3 t/s, TTFT 1.68s, $0) for quick_chat + routing + lite_agent; qwen3:4b degraded fallback only; qwen3.6 retired as resident; DeepSeek demoted to disabled-by-default. workers/llm_router.py replaces the regex intent ladder with one structured-output call {route, tier, recon_mode} — prompts flow downstream byte-identical (HTML augmentation REMOVED), recon_mode kills visual_verify auto-fire, safe_label() ends mid-token truncation (gemma4:26b survives). Think suppression: gpt-oss think:'low' + discarded thinking field; scrubber backstop WARNs on every catch. qwen2.5vl pinned to CPU (num_gpu=0) so it co-resides with the 60GB brain inside the 64GB VRAM carve. tests/evals/ harness: 34/34 PASS, exit 0 — now a mandatory ship gate for every future phase (CLAUDE.md rule). pytest 413/413. Services restarted clean; hermes-gateway untouched.

2026-05-28 04:03 UTC — Phase 28 dispatch | tier=api | "connect_to_deepseek_flash" | 2.9s | $0.0042 | failed

2026-05-07 18:08 UTC — Phase 28 dispatch | tier=max | "BidWatt remote reconnaissance for NIMO migration. " | 306.9s | $0.0000 | done

2026-05-07 16:57 UTC — Phase 28 dispatch | tier=max | "take a screenshot of the desktop and send it to me" | 32.8s | $0.0000 | done

2026-05-07 — phase 35: nexus-xvfb.service unit created; chronicle + parallel_tools gain :99 fallback; 8 tests green; sudo-commands.sh written — display :99 active after `sudo /tmp/sudo-commands.sh`
2026-05-07 15:30 UTC — Phase 28 dispatch | tier=max | "audit all 27 services in the credentials registry " | 125.0s | $0.0000 | done

2026-05-07 — Phase 32.2 shipped: result reporter multi-message chunking. workers/cc_result_reporter.py gains _chunk_text (splits at newline/table boundaries, preserves ``` fences), _telegram_chunked ([N/M] markers, max_total_chunks overflow pointer), _read_log_body (ANSI-stripped log tail), _is_investigation (files_changed==0, commits==[], duration>60s). Investigation dispatches now ship full log content instead of one_line_summary. Build dispatches get top-5 changed files + first 3 commit lines. config/cost_limits.yaml gains result_reporter: section. 30/30 tests pass in tests/test_result_reporter_chunking.py.

2026-05-07 — Phase 33 shipped: credentials bootstrap helper. tools/credentials_helper.py + core/credentials_registry.py + tools/credentials_registry.py. 27 services registered (8 Tier-1: vercel/supabase/stripe/github/cloudflare/resend/deepseek/anthropic; 8 Tier-2; 7 Tier-3 stubs; 4 Tier-4 stubs). Interactive flow: show instructions → prompt for token → real API validation → save to config/secrets.yaml (chmod 600 + auto-backup). Validation methods: HTTP_GET (bearer+basic), HTTP_POST, CLI_EXEC. save_token bug fixed (previous attempt was redacting values before write). Telegram /creds command shape in place. 138/138 tests pass. SOUL.md + CLAUDE.md + wiki/concepts/credentials-management.md updated.

2026-05-07 14:04 UTC — Phase 28 dispatch | tier=max | "PHASE 33 — Credentials Bootstrap Helper CONTEXT --" | 601.3s | $0.0000 | failed

2026-05-07 13:23 UTC — Phase 28 dispatch | tier=max | "I have a multi-step Supabase + Vercel setup task f" | 31.0s | $0.0000 | done

2026-05-07 01:01 UTC — Phase 28 dispatch | tier=max | "Phase 30b: reboot-hardening for Nexus services. Th" | 124.8s | $0.0000 | done

2026-05-06 — Phase 30b: reboot-hardening staged in /tmp/. nexus-telegram + nexus-cc-dispatcher gain `After=`/`Wants=` on nexus-prewarm.service so they don't race the prewarm on boot. Ollama gets a drop-in (`/etc/systemd/system/ollama.service.d/keep-alive.conf`) setting `OLLAMA_KEEP_ALIVE=24h` to keep models hot. Apply via /tmp/sudo-commands.sh; verify per /tmp/phase30b-verify.md.

2026-05-07 00:48 UTC — Phase 28 dispatch | tier=max | "Phase 32.1: implement the scrubber fix that Sonnet" | 250.9s | $0.0000 | done

2026-05-06 17:50 UTC — Phase 28 dispatch | tier=max | "List every file in ~/AI_Agent/cc_archive/ and ~/AI" | 18.6s | $0.0000 | done

2026-05-06 17:49 UTC — Phase 28 dispatch | tier=max | "read ~/AI_Agent/cc_archive/cc_2e01e270.md and post" | 20.6s | $0.0000 | done

2026-05-06 17:46 UTC — Phase 28 dispatch | tier=max | "Audit the chain-of-thought scrubber in workers/con" | 172.9s | $0.0000 | done


2026-05-06 — entities/colton.md: Location updated to Kennewick, WA (works in Pasco)
2026-05-06 03:29 UTC — Phase 28 dispatch | tier=flash | "echo "evening live test" and exit" | 11.1s | $0.0003 | done

2026-05-05 11:36 UTC — Phase 28 dispatch | tier=flash | "echo "phase 31 live verification" and exit" | 17.1s | $0.0004 | done

2026-05-05 11:27 UTC — Phase 28 dispatch | tier=flash | "echo "ollama pull gemma4:26b" (do not actually pul" | 6.8s | $0.0002 | done

2026-05-05 11:27 UTC — Phase 28 dispatch | tier=flash | "build a simple analog clock html page" | 30.8s | $0.0007 | done

2026-05-05 11:26 UTC — Phase 28 dispatch | tier=flash | "create ~/AI_Agent/test_phase31.txt with the text "" | 14.8s | $0.0004 | done

2026-05-04 14:49 UTC — Phase 28 dispatch | tier=api | "extend(60m): Pull the Ollama model gemma4:26b. Run" | 0.0s | $0.0000 | failed

2026-05-04 14:48 UTC — Phase 28 dispatch | tier=flash | "Pull the Ollama model gemma4:26b. Run "ollama pull" | 601.0s | $0.0146 | timeout

2026-05-04 13:48 UTC — Phase 28 dispatch | tier=api | "create a file at ~/AI_Agent/test_pipeline_v2.txt w" | 0.0s | $0.0000 | failed

2026-05-04 13:40 UTC — Phase 28 dispatch | tier=flash | "create a file at ~/AI_Agent/test_pipeline.txt with" | 106.8s | $0.0026 | done

2026-05-04 03:28 UTC — Phase 28 dispatch | tier=api | "SOUL.md tone fix — "lfg" is input vocabulary, not " | 0.0s | $0.0000 | failed

2026-05-04 02:13 UTC — Phase 28 dispatch | tier=max | "a working analog clock with all 12 numbers, smooth" | 67.1s | $0.0000 | done

2026-05-03 — Phase 29 shipped: coding router fixed to default to /max (Claude Sonnet via Max plan, $0 marginal) instead of /code (paid DeepSeek Flash). Renamed /real → /api with /real kept as a deprecation-logging alias (writes to cc_logs/_deprecation.log on every use). Tier-specific cost ceilings replace the Phase 28 uniform per_dispatch_usd: max/local/quick uncapped, flash $0.10, pro $0.50, api $2.00; daily $15 ceiling now applies only to paid tiers (flash+pro+api). Dispatcher's _spawn_claude(tier="max") skips the env-file source step so claude reads ~/.claude/ Max session auth directly; _build_dispatch_env scrubs every ANTHROPIC_* var for tier=max so a stray parent-env key can't shadow the Max session. Smart build-intent routing ("build me X", "create X", etc.) now upgrades to /max instead of /code. core.cc_dispatch.normalize_tier() merges legacy tier="real" rows into "api" bucket so cumulative stats stay consistent. CLAUDE.md gains a coding-router section documenting the new ladder. 7/7 gates passed; /max test build (analog clock) verified CLEAN by qwen2.5vl. Cook cost: $0 (all test routing went through /max). 15/15 Phase 22 dispatch tests still green.

2026-05-03 19:02 UTC — Phase 28 dispatch | tier=real | "say hello in one sentence" | 10.8s | $0.0075 | done

2026-05-03 18:32 UTC — Phase 28 dispatch | tier=real | "say hello in one sentence" | 0.0s | $0.0000 | failed

2026-05-03 — Phase 28 shipped: tier-aware Claude Code router (flash/pro/real/local) folded into existing cc_dispatch / cc_dispatcher / cc_result_reporter (no new claude_code_dispatch.py per Colton). Five slash commands live: /code (DeepSeek V4-Flash, default cloud), /pro (DeepSeek V4-Pro), /real (Anthropic Sonnet 4.6), /local (qwen3-coder:30b via Ollama), /quick (qwen3:4b). Smart-routing regex (SIMPLE_BUILD vs general build intent) auto-upgrades plain "build me X" → tier=flash dispatch. Visual-verify pipeline (Playwright headless screenshot + qwen2.5vl CLEAN/BROKEN verdict, with description-override for unambiguous blank/garbled pages) flags broken HTML before posting. Phase 27 auto-attach bug fixed: reporter + listener now send HTML + screenshot via Telegram sendDocument. New entity wiki/entities/coding-router.md auto-rewritten by reporter on every dispatch. Cost guardrails in config/cost_limits.yaml (per-dispatch $0.50, per-day $5.00). 10/10 test gates passed; total cook cost $0.012 across 5 cloud dispatches (4 flash + 1 pro). 15/15 Phase 22 dispatch tests still green. Limit: ANTHROPIC_API_KEY missing from secrets.yaml — /real tier routes but subprocess fails until key added.

2026-05-03 18:07 UTC — Phase 28 dispatch | tier=flash | "reply with the word ACK only" | 22.6s | $0.0005 | done

2026-05-03 18:07 UTC — Phase 28 dispatch | tier=flash | "output a single line: PHASE_28_PING" | 10.8s | $0.0003 | done

2026-05-03 18:06 UTC — Phase 28 dispatch | tier=pro | "say hi in one short sentence" | 20.8s | $0.0010 | done

2026-05-03 18:04 UTC — Phase 28 dispatch | tier=flash | "a working analog clock with all 12 numbers, smooth" | 58.8s | $0.0014 | done

2026-05-03 18:02 UTC — Phase 28 dispatch | tier=flash | "build me an analog clock with smooth-moving hands " | 145.1s | $0.0035 | done

2026-05-03 17:56 UTC — Phase 28 dispatch | tier=flash | "analog clock" | 42.5s | $0.0021 | done

2026-05-01 — Pushed Nexus to private GitHub repo CWatt250/nexus. Pre-flight surfaced leaked GITHUB_TOKEN/PAT + TELEGRAM_BOT_TOKEN+CHAT_ID in projects/nexus-core/run-log.jsonl committed history; scrubbed via git-filter-repo --replace-text (173 commits, 0 deletions, all hashes rewritten). Patched tools/run_log.py to redact every string field via core.secrets.redact() at write time; .gitignore'd projects/*/run-log.jsonl + cc_* dirs + wiki/sources/ + memory runtime files. ADR: decisions/2026-05-01_nexus-github-backup.md.

2026-05-01 — May 1 polish pass: 12 production-testing bugs fixed in 9 commits (c639f85 wiki-grounded entity queries, e54e0cc uncertainty rule, 8b7da82 think-leak scrubber, f930f72 multi-step compliance, 5b7b932 synthesis on summary requests, 567e070 /chat through router, 55e87ec casual routing, 5fe4c17 task_id prefix on CHAT, 6bdff73 slang glossary). SOUL.md gained Following-instructions, Uncertainty, and Slang sections. conversation_handler gained _entity_lookup, _strip_think_final, _wants_synthesis. nexus_api /chat now goes through route_message.

2026-05-01 — Phase 25 bootstrap: created entities/{colton,nexus,bidwatt,subwatt,argus}.md, concepts/{llm-wiki-pattern,dispatch-system,intent-routing,scaffolding-recipes}.md, decisions/2026-04-30_*.md and 2026-05-01_phase-25-knowledge-garden.md, SCHEMA.md, index.md.

2026-05-02 — Phase 21 Part 1 shipped: tools/script_writer.py + visual_generator.py + content_create.py + Telegram commands. Script backend Anthropic Sonnet 4.5 → local qwen3.6 fallback; visuals image_gen_tool/ERNIE → PIL solid-gradient fallback; TTS Kokoro; assembly ffmpeg libx264+AAC 1080x1920@30fps. Smoke test produced content/final/bidwatt-bid-management-for-mechanical-contractors.mp4 (30.5s, 9/9 scenes, 24.9s wall, $0, free local backends). ADR: decisions/2026-05-02_phase-21-content-stack.md. Concept: concepts/content-pipeline.md.

2026-05-02 — Phase 21 Part 2 polish shipped: scripts/generate_music.py procedurally generates 5x60s tone-keyed mp3s (sine+saw additive, royalty-free by construction); tools/music_picker.py routes tones → buckets. content_create.py now does ffmpeg xfade+acrossfade transitions, zoompan Ken Burns alternating direction, drawtext per-sentence captions at h*0.78 with 4px outline, amix music ducking at 0.15 with 1.0s/1.5s fades, multi-aspect output via scale+pad. visual_generator.py gained tone-keyed palettes, keyword extraction (drops stopwords + ranks by length/position/caps), film-grain noise overlay. Smoke test BidWatt v2: 9/9 scenes, 68s actual (script overran 30s target — script_writer followup), $0 cost, 84s wall, 13MB each for 9x16 and 16x9. ADRs: decisions/2026-05-02_phase-21-part2-polish.md + decisions/2026-05-02_image-gen-status.md.

2026-05-07 — BUG: tools/cu_agent_safety.py (or wherever the denial logic lives) evaluated stale conversation reasoning instead of current file content for VNC service install. Phase 31 v2 / safety-context-grounding candidate. Denial was correct architecturally but referenced wrong file state — flagged my earlier Tailscale-IP binding plan even after I had rewritten /tmp/nexus-vnc.service to bind 127.0.0.1 via -localhost. User had to override explicitly. Followup: ground denial reasoning in current artifact contents (file diff at decision time), not session transcript. Phase 36 nexus-vnc.service ultimately installed clean (active, ss shows 127.0.0.1:5900 + [::1]:5900 only).

2026-05-07 — BUG (Phase 36 install): Computer Use safety policy denied a 
sudo install command based on stale conversation reasoning, not current 
file content. The on-disk service file was localhost-only (-localhost flag), 
but denial cited an earlier turn's contemplated Tailscale-IP binding. Phase 
31 v2 / safety-context-grounding candidate. Workaround: explicit 
human override after verifying file content. Real fix: safety check should 
re-read file from disk at decision time, not use cached session context.


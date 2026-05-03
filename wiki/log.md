# Wiki Journal

Append-only chronological log of significant wiki changes. One line per change. Newest at top.

Format: `YYYY-MM-DD HH:MM TZ — <page>: <what changed>`

---

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

# Wiki Journal

Append-only chronological log of significant wiki changes. One line per change. Newest at top.

Format: `YYYY-MM-DD HH:MM TZ — <page>: <what changed>`

---

2026-05-01 — Pushed Nexus to private GitHub repo CWatt250/nexus. Pre-flight surfaced leaked GITHUB_TOKEN/PAT + TELEGRAM_BOT_TOKEN+CHAT_ID in projects/nexus-core/run-log.jsonl committed history; scrubbed via git-filter-repo --replace-text (173 commits, 0 deletions, all hashes rewritten). Patched tools/run_log.py to redact every string field via core.secrets.redact() at write time; .gitignore'd projects/*/run-log.jsonl + cc_* dirs + wiki/sources/ + memory runtime files. ADR: decisions/2026-05-01_nexus-github-backup.md.

2026-05-01 — May 1 polish pass: 12 production-testing bugs fixed in 9 commits (c639f85 wiki-grounded entity queries, e54e0cc uncertainty rule, 8b7da82 think-leak scrubber, f930f72 multi-step compliance, 5b7b932 synthesis on summary requests, 567e070 /chat through router, 55e87ec casual routing, 5fe4c17 task_id prefix on CHAT, 6bdff73 slang glossary). SOUL.md gained Following-instructions, Uncertainty, and Slang sections. conversation_handler gained _entity_lookup, _strip_think_final, _wants_synthesis. nexus_api /chat now goes through route_message.

2026-05-01 — Phase 25 bootstrap: created entities/{colton,nexus,bidwatt,subwatt,argus}.md, concepts/{llm-wiki-pattern,dispatch-system,intent-routing,scaffolding-recipes}.md, decisions/2026-04-30_*.md and 2026-05-01_phase-25-knowledge-garden.md, SCHEMA.md, index.md.

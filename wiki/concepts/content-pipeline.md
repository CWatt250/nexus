---
title: Content Pipeline
date: 2026-05-02
tags: [content, video, ffmpeg, kokoro, anthropic, ollama]
status: active
---

# Concept

End-to-end original short-form video production from a single topic
prompt. Topic in → 1080x1920 vertical mp4 out. Phase 21 Part 1.

# Pipeline

```
topic ─► script_writer.script_write_core
            (anthropic:claude-sonnet-4-5 if ANTHROPIC_API_KEY set,
             else local qwen3.6 via Ollama, free)
         │
         ▼
       content/scripts/<date>_<slug>.md
       parse_scenes() → list of {visual, voiceover}
         │
         ▼
       For each scene N:
         ├─► tts_tool.save_audio (Kokoro, ~0.5x realtime)
         │       └─► content/voiceovers/<slug>_scene_NN.wav
         ├─► visual_generator.visual_generate
         │       (image_gen_tool/ERNIE if ERNIE_API_KEY set,
         │        else PIL solid-gradient + text overlay)
         │       └─► content/stills/<slug>_scene_NN.png (1080x1920)
         └─► ffmpeg (libx264 + tune stillimage + aac 192k + yuv420p + 30fps)
                 └─► content/stills/<slug>_scene_NN.mp4
         │
         ▼
       ffmpeg concat demuxer (-c copy, re-encode fallback)
         └─► content/final/<slug>.mp4
```

# Tools

| Tool | LangGraph | Purpose |
|------|-----------|---------|
| `script_write(topic, duration, tone)` | yes | Generate scene-by-scene script. SLOW tier. |
| `content_create(topic, duration, tone)` | yes | Full topic → mp4. SLOW tier. |
| `visual_generate(scene_description, output_path)` | no (helper) | One scene image, real or PIL fallback. |
| `script_writer.script_write_core(...)` | no (helper) | Same as `script_write` but returns a dataclass. |
| `script_writer.parse_scenes(text)` | no (helper) | Pull scene blocks out of script markdown. |
| `content_create.content_create_core(...)` | no (helper) | Same as `content_create` but returns the full info dict. |

# Telegram surface

| Command | Latency | Effect |
|---------|---------|--------|
| `script <topic>` | ~10-30s | Inline reply with script markdown (truncated at 3500 chars). |
| `create video <topic>` | ~25s-5min | Acks immediately, sends final mp4 file when done. Runs in background thread so the listener stays responsive. |
| `video: <topic>` | same | Alias. |

# Backends + cost model

- **Script** — Anthropic Claude Sonnet 4.5 (~$0.005-0.02 per script) preferred. Local qwen3.6 (free) fallback when key missing. Detected at module load via `core.secrets.get("ANTHROPIC_API_KEY")`.
- **TTS** — Kokoro-82M local. Voice resolution: env `SPARKY_VOICE` → preferred Kokoro list (`af_sky`, `af_nova`, `bf_emma`, `af_heart`). Bark installed but slow on this hardware; not used in the default pipeline.
- **Visuals** — `image_gen_tool` (ERNIE) preferred. PIL solid-gradient + text overlay fallback when no key. Real video clips (Higgsfield/Seedance) deferred to Part 2.
- **Assembly** — `ffmpeg` (system binary). libx264 + AAC 192k @ 30fps + yuv420p, padded to 1080x1920.

# Filesystem layout

```
~/AI_Agent/content/
├── scripts/        # generated markdown (Anthropic or qwen3.6 source)
├── voiceovers/     # per-scene WAV from Kokoro
├── stills/         # per-scene PNG + per-scene mp4 clips
├── final/          # concatenated mp4s — the deliverable
└── publish_queue/  # videos pending upload (multi-platform publish — Part 2)
```

All five dirs exist locally; runtime contents are gitignored under the broader `output/` / `content/` patterns from `.gitignore`.

# Limitations + followups

- **Visuals are static cards by default.** The PIL fallback renders the scene description as overlaid text on a gradient. Looks legible on a phone but not what a TikTok creator would ship. Phase 21 Part 2 plans Higgsfield video clips.
- **No Ken Burns / motion** on the stills. The ffmpeg `-loop 1 -i image.png` path produces a frozen frame. Pan / zoom would lift the production value at low cost.
- **No background music.** The voiceover is the only audio. Phase 21 Part 2 should add an `audio_gen_tool` invocation per script for a music bed.
- **Script tone leans markdown-heavy** — the current SYSTEM_PROMPT enforces `## Scene N` + `[VISUAL]:` + `[VOICEOVER]:` blocks. Local qwen3.6 occasionally adds extra commentary that the parser drops. Tighter post-validation would help.
- **Hardcoded Sonnet 4.5 model id** in `script_writer.ANTHROPIC_MODEL`. Move to `core.secrets` config when more models land.

# See also
- [[entities/bidwatt]] — first subject of the smoke test (May 2 2026).
- [[concepts/dispatch-system]] — Phase 22 dispatch is orthogonal but shares the SLOW-tier pattern.
- `tools/voiceover_pipeline.py` — sibling tool that re-voices existing YouTube videos (yt-dlp pull → TTS → SRT → mp4 mix). Different entry point; same Kokoro + ffmpeg dependencies.

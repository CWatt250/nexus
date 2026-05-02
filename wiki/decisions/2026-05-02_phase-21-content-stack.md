---
title: Phase 21 Part 1 — Content Production Stack
date: 2026-05-02
status: accepted
tags: [phase-21, content, video, kokoro, ffmpeg, anthropic]
---

# Context

Yesterday's voiceover infrastructure (commit `e927f15`) gave Nexus
re-voicing capability — yank a YouTube video, swap the audio with a
Kokoro voiceover, re-mux. Useful but reactive. The roadmap goal for
Phase 21 was original short-form content: topic prompt → vertical
9:16 mp4 ready for TikTok / Reels / Shorts. Part 1 had to ship the
foundation (script + visuals + assembly) without blocking on
external API keys.

# Decision

Built four new tools (`script_writer`, `visual_generator`,
`content_create`, plus Telegram commands) that compose the existing
`tts_tool` + `ffmpeg_tool` into an end-to-end pipeline. Architecture
documented in [[concepts/content-pipeline]].

## Backend strategy

Every external API has a free local fallback so the pipeline runs
end-to-end on any machine that has Kokoro + ffmpeg, no API keys
required:

- **Script** — Anthropic Claude Sonnet 4.5 preferred → local qwen3.6
  via Ollama (free) when `ANTHROPIC_API_KEY` is missing.
- **Visuals** — `image_gen_tool` (ERNIE) preferred → PIL
  solid-gradient + text-overlay card when `ERNIE_API_KEY` is missing.
- **TTS** — Kokoro-82M local always (no fallback needed; Bark
  installed but too slow on this hardware to use by default).

Same pattern as `tools/web_search` (Tavily → Brave → SearXNG). User
sees a clear note in the result about which backend was used and
what the cost was.

## Pipeline-as-a-tool

`content_create_core(topic, duration, tone)` returns a dict with
`{final_video_path, scene_count, duration_actual_seconds,
script_backend, cost_usd, wall_seconds, visuals_fallback_count}`
so downstream callers (Telegram listener, dashboard, future
publish tool) get structured info without parsing prose.

The `@tool` wrapper formats this for the LangGraph agent. Tagged
SLOW tier in TOOLS.md (next refresh) — wall clock 25s-5min depending
on backend mix and scene count.

## Concat strategy

ffmpeg concat demuxer with `-c copy` is fast (no re-encode) but
fragile to encoder-setting drift. Since every scene clip is built by
the same `_build_scene_clip` call with identical args, `-c copy`
should always work. Fallback path re-encodes via `libx264 + aac` so
a single bad timestamp doesn't fail the whole pipeline.

## Telegram surface

Two commands wired into `_handle_content_command` BEFORE the LLM
router (same pattern as `dispatch:` and `queue:`):

- `script <topic>` — fast (~10-30s), awaited inline.
- `create video <topic>` (alias `video: <topic>`) — slow (~2-5 min),
  acks immediately, runs the orchestrator on a worker thread via
  `asyncio.to_thread`, sends the final mp4 file via `Bot.send_video`
  when done. Listener stays responsive throughout.

# Smoke test results (2026-05-02)

`content_create_core` invoked from this dispatch with the BidWatt
promo prompt:

```
topic    : BidWatt — bid management for mechanical contractors.
           Disorganized estimator drowning in spreadsheets,
           lightning bolt moment, organized BidWatt user shipping
           bids in half the time.
duration : 30s target → 30.53s actual (within ±5s tolerance)
scenes   : 9/9 built
backend  : ollama (no ANTHROPIC_API_KEY set)
visuals  : 9/9 PIL fallback (no ERNIE_API_KEY set)
wall     : 24.89 seconds
cost     : $0.00
output   : content/final/bidwatt-bid-management-for-mechanical-contractors.mp4
           1080x1920 H.264 + AAC 192k @ 30fps, 1.3 MB
```

Verified via `ffprobe` (codec/dim/duration), played without error,
sent to Telegram for live review.

# Consequences

**Won:**
- Pipeline runs free out of the box; no API keys required to make
  videos.
- Single-source-of-truth for content (`content/` tree).
- Reused 100% of the existing `tts_tool` + `ffmpeg_tool` — no
  duplicate code.
- Telegram surface lets Colton produce videos from his phone in 25s
  on local backends.

**Tradeoffs accepted:**
- PIL fallback visuals are static text-cards. Looks acceptable but
  isn't TikTok-ready. Higgsfield clips deferred to Part 2.
- Local qwen3.6 scripts have more boilerplate than Sonnet 4.5
  output. Adding the Anthropic key is the cheap upgrade.
- No background music yet (audio_gen_tool integration deferred).
- No motion on stills (Ken Burns deferred).

# Followups

- Add `ANTHROPIC_API_KEY` to `config/secrets.yaml` to upgrade script
  quality. Cost: ~$0.01/script.
- Add `ERNIE_API_KEY` (or wire Stable Diffusion via Ollama) to
  upgrade visuals.
- Phase 21 Part 2:
  - Higgsfield / Seedance video clips per scene (replaces stills)
  - Background music via existing `audio_gen_tool`
  - Multi-platform publishing (TikTok, Reels, Shorts)
  - Brand asset injection (logo, color palette)
- Long-term: caching of the script-writer's system prompt to cut
  Anthropic input-token costs by ~60%.

# See also
- [[concepts/content-pipeline]] — full architecture.
- [[entities/bidwatt]] — smoke-test subject; `content/final/bidwatt-...mp4` is in the wiki sources for reference.
- `tools/voiceover_pipeline.py` — sibling pipeline (re-voicing).

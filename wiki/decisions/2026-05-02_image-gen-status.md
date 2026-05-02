---
title: Image generator status — Part 2 polish pass
date: 2026-05-02
status: accepted
tags: [phase-21, content, image-gen, followup]
---

# Context

Phase 21 Part 2 (content polish pass) tasked us with trying real image
generation in the content pipeline before settling for the PIL gradient
fallback shipped in Part 1. The existing entry point is
`tools/image_gen_tool.py`, originally wired for Baidu's ERNIE.

# Findings

`image_gen_tool.py` is wired but has no working credential on this host.
Direct invocation returns:

```
Error: ERNIE_API_KEY not configured.
Add ERNIE_API_KEY=your_key to ~/AI_Agent/.env
```

`.env` carries six keys (`GITHUB_TOKEN`, `Z_AI_API_KEY`,
`BRAVE_SEARCH_API_KEY`, `TAILSCALE_API_KEY`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`) — none usable for image generation.

Local-fallback options were probed:

- **Stable Diffusion via Ollama** — no diffusion-capable models pulled
  (`ollama list` returns no diffusion entries; Ollama doesn't ship a
  first-party image-gen model).
- **AUTOMATIC1111 / ComfyUI** — neither service is running on the usual
  ports (7860 / 8188).
- **Cloud free tiers (Stability, Replicate, OpenAI)** — would require a
  new API key + a budget decision; out of scope for a polish pass.

# Decision

1. Keep `image_gen_tool.py` as the primary path inside
   `visual_generator.visual_generate(prefer_real=True)`. When a key
   appears later, the pipeline picks it up automatically.
2. Improve the PIL fallback so it isn't ugly: tone-keyed palettes,
   keyword-extracted typography, subtle film-grain noise (Phase 21
   Part 2.5).
3. Document the gap as a Part 3 follow-up — when we want to invest in
   real visuals, the path is to spin up a local SDXL or Flux setup
   (ComfyUI is the typical play) and wire a thin client into
   `image_gen_tool.py` (or add a parallel `tools/sd_tool.py` and have
   `visual_generator` try both).

# Follow-ups

- [ ] Stand up a local SD/Flux server (ComfyUI on port 8188 is the
      default suggestion).
- [ ] Add a `tools/sd_tool.py` that calls the local server and returns
      a path-or-error string in the same shape as `image_gen_tool`.
- [ ] Update `visual_generator.visual_generate` to try `sd_tool` →
      `image_gen_tool` → PIL fallback, in that order.
- [ ] Consider gating "real visuals" behind an env flag so unit tests
      stay deterministic.

# Status

PIL fallback is the de facto generator for now. It produces
tone-matched gradient cards with extracted keywords and grain — clearly
not production-quality artwork but a reasonable visual scaffold for a
30s talking-head reel. All 9 scenes in the Part 2 smoke test rendered
via PIL fallback (`visuals_fallback_count: 9`).

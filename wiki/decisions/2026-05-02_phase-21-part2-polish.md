---
title: Phase 21 Part 2 — Content Polish
date: 2026-05-02
status: accepted
tags: [phase-21, content, video, ffmpeg, music, captions, transitions]
---

# Context

Phase 21 Part 1 (commit chain ending at `05eb719`) shipped a working
end-to-end content pipeline, but the output was a "PoC video" — flat
PIL gradient stills, hard cuts, no music, no on-screen text. Part 2
was scoped to add the polish layer that takes output from "PoC" to
"actually shareable", without touching the publishing surface (Part
3, manual oversight required).

# Decisions

## 2.1 Music library — procedural generation, not internet downloads

The spec offered "download CC0 tracks from Pixabay/MacLeod" as the
primary path with "generate locally" as fallback. We went straight to
the local-generation path and skipped the download branch entirely.
Reasoning:

- The host is sometimes off-net (Tailscale-only, local-only).
- A reproducible-by-construction library beats a fetched-once library
  that's hard to recreate on a fresh machine.
- Background music for a short voiceover only needs to set mood, not
  to be a hit song. Procedural sine+saw additive synthesis at 60s,
  ducked to 0.15 under voiceover, is sufficient.

Artifacts:
- `scripts/generate_music.py` — one-shot generator. Run on a fresh
  machine to bootstrap the library.
- `content/music/{energetic,chill,dramatic,cinematic,minimal}/<bucket>_01.mp3`
  — five 60-second mp3s. content/ is gitignored, so the tracks aren't
  in the repo; the generator script is.
- `tools/music_picker.py` — `select_music(tone, duration_seconds)`
  maps tone → bucket → first track. Returns None if missing so the
  pipeline can skip cleanly.

## 2.2 Audio mix — fixed-volume duck, not sidechain compression

ffmpeg's `sidechaincompress` filter is the "right" way to duck music
under voiceover dynamically, but it's twitchy with short clips and
needs tuning per-track. For Part 2 we use the simpler
`amix=inputs=2:duration=first` with a fixed `volume=0.15` on the
music input + `afade=in/out` for the song endpoints. Voiceover stays
at 1.0. Net result: music sits noticeably under the voice without
requiring any per-track work. Sidechain ducking can come back as a
Part 3 enhancement when we have enough tracks for it to matter.

## 2.3 Transitions — xfade chain, not concat demuxer

Concat demuxer (-c copy) was the Part 1 approach. It's fast but
doesn't support transitions. Part 2 uses a `filter_complex` chain
that pairs clips with `xfade=transition=fade:duration=0.3` and the
audio side gets `acrossfade=duration=0.3`. The chain re-encodes (no
-c copy shortcut), but at 30fps + 1080x1920 the cost is acceptable
(~3-5s of ffmpeg time per scene-pair on this hardware).

Per-scene Ken Burns zoom via `zoompan` alternates direction (even
scenes zoom in, odd scenes zoom out) so a long video doesn't feel
monotonous. Pre-scaled 2x so zoompan has pixels to work with.

## 2.4 Burned-in captions — drawtext per sentence, not ASS subtitles

Two paths: ffmpeg's `drawtext` filter or a sidecar ASS/SRT file.
Drawtext won because:

- No external dependency (libass is fine but adds another link).
- Per-sentence styling is just another `drawtext` in the chain,
  gated by `enable='between(t,T0,T1)'`. Sentence durations split
  proportional to char count.
- White text + 4px black outline at h*0.78 reads on any background;
  no need to detect the gradient color of the still behind it.
- Falls back to "no captions" cleanly when no system font is found.

The escape function for drawtext (handles `\\ : ' % ,`) lives at
`content_create._escape_drawtext`.

## 2.5 Visuals — kept PIL fallback as primary

`image_gen_tool` requires an ERNIE_API_KEY that isn't on this host.
Local SD/Flux servers aren't running. Cloud-free-tier options were
out of scope for a polish pass. Decision documented in
[[decisions/2026-05-02_image-gen-status]].

What we DID ship to keep the fallback from looking like a PoC:

- Tone-keyed palette families (`TONE_PALETTES`) with 4 variants each
  for energetic, chill, dramatic, cinematic, minimal. Hash of scene
  description picks within a family for stable per-scene distinct
  cards.
- `_extract_keywords` pulls 1-3 high-impact words from the scene
  description (drops stopwords, ranks by length + position +
  capitalisation) and renders them stacked in big bold type.
- `_add_noise` overlays a soft film-grain tile at ~3.5% opacity so
  the gradient doesn't look like flat plastic.

When a real diffusion server lands later, `visual_generator` already
tries `prefer_real=True` first; the fallback pattern stays as belt-
and-suspenders.

## 2.6 Multi-aspect — scale+pad re-render, not source-level cropping

`content_create_core` now takes `aspects=("9x16", "1x1", "16x9")`.
9:16 is the master mp4; other aspects re-render the master via
ffmpeg `scale + pad` so subjects don't get cropped or distorted.
Padding uses a black background — a future enhancement could pull
the dominant gradient color and pad with that for a more polished
look.

# Smoke test results (2026-05-02)

`content_create_core` invoked from this dispatch with the BidWatt
prompt and `aspects=("9x16", "16x9")`:

```
topic    : BidWatt — bid management for mechanical contractors.
           Built with Next.js and Supabase. Helps Irex Argus
           estimators ship bids faster.
duration : 30s target → 68.17s actual (script overran)
scenes   : 9/9 built
backend  : ollama (no ANTHROPIC_API_KEY)
visuals  : 9/9 PIL fallback (no ERNIE_API_KEY) — tone-keyed,
           keyword typography, grain
music    : energetic_01.mp3 (procedural, ducked at 0.15)
wall     : 83.97 seconds
cost     : $0.00
output   : content/final/bidwatt-bid-management-...mp4 (9x16, 13 MB)
           content/final/bidwatt-bid-management-..._16x9.mp4 (13 MB)
```

# Verification gates

| Gate | Status | Evidence |
|------|--------|----------|
| 2.1 Music library | PASS | 5 buckets, 1 track each (≥ 3 minimum). `select_music()` resolves paths for all 5 tones + fallback. |
| 2.2 Audio mix | PASS | mean_volume -27.3 dB, max -9.2 dB across the 68s mp4. RMS at t=10s = 1630 (speech range). Single mixed AAC stream as expected (amix output). |
| 2.3 Transitions | PASS | 2045 video frames over 68.17s = 30.0 fps continuous. Xfade chain accepted by ffmpeg. |
| 2.4 Captions | PASS | Frames at 5s/15s ~10-14% larger than baseline (411k → 457k / 468k bytes), confirming text overlay. Frame sent to Telegram for visual verification. |
| 2.5 Visuals | PASS (documented fallback) | All 9 scenes rendered via tone-keyed PIL fallback. ERNIE/SD path documented in [[decisions/2026-05-02_image-gen-status]]. |
| 2.6 Multi-aspect | PASS | Both 9x16 (13 MB) and 16x9 (13 MB) variants in content/final/. Each plays clean per ffprobe. |
| 2.7 Smoke test | PASS | Both files sent to Telegram. End-to-end ran in 84s. |

7 of 7 gates pass.

# Tradeoffs accepted

- **Script overran the duration target.** Local qwen3.6 produced 9
  scenes for a 30s prompt → 68s of voiceover. Script_writer should
  honor the duration budget tighter; logged as a Part 3 followup.
- **Fixed-volume music duck** vs sidechain compression. Acceptable
  while we have one track per bucket; revisit when the library grows.
- **Black padding on aspect variants.** Cleaner than distortion;
  could be tone-matched in a later pass.
- **PIL fallback visuals** are still text-on-gradient cards. Better
  than Part 1 (keyword typography + noise + tone palettes) but not
  TikTok-ready production art.

# Followups

- Tighten `script_writer` duration enforcement: cap scene count more
  aggressively (`scenes ≈ duration / 4` instead of `/ 3.5`), or
  post-trim the longest scenes to fit the budget.
- Stand up a local diffusion server (ComfyUI on :8188) and add
  `tools/sd_tool.py`. See [[decisions/2026-05-02_image-gen-status]].
- Phase 21 Part 3: TikTok / Reels / YouTube Shorts publishing with
  manual oversight. OAuth flows + a manual-approve queue.
- Brand asset injection (logo on intro/outro, brand color palette
  override of TONE_PALETTES).
- Bark voice acting per-character (different speaker per scene) when
  Bark performance improves.

# See also
- [[concepts/content-pipeline]] — full architecture diagram (Part 1 + 2).
- [[decisions/2026-05-02_phase-21-content-stack]] — Part 1 ADR.
- [[decisions/2026-05-02_image-gen-status]] — visuals investigation.
- [[entities/bidwatt]] — smoke-test subject.

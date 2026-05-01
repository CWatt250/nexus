---
ingested_at: 2026-05-01T06:30:27-07:00
source_type: research
descriptor: hyperframes-integration
---

# HyperFrames Integration Research

**Source:** https://github.com/heygen-com/hyperframes
**Date:** 2026-04-30
**Status:** Research / Phase 21.2 candidate

---

## 1. What HyperFrames Does

HyperFrames is an **open-source video rendering framework** (13.3k stars) built by HeyGen. The core idea: **write HTML/CSS/JS, render to video.** You compose frames as HTML elements with special data attributes (`data-hyperframe`, `data-animate`, etc.), and HyperFrames renders them frame-by-frame into an MP4.

Key features:
- **HTML-native** — no React, no TypeScript compilation, no build step. Plain HTML, CSS, JS.
- **AI-first authoring** — designed to be prompted into existence by AI coding agents. Quick-start with `npx skills add heygen-com/hyperframes` for Claude/Cursor/Codex, or `npx hyperframes init` manually.
- **Frame Adapter pattern** — swap rendering backends without changing your HTML. Deterministic rendering (same HTML = same video every time).
- **Catalog of 50+ blocks** — prebuilt animated components (text reveals, transitions, lower thirds, counters, etc.).
- **Licensing:** Apache 2.0.

**Package:** `npm i hyperframes` (see https://www.npmjs.com/package/hyperframes)
**Docs:** https://hyperframes.heygen.com/guides/prompting

---

## 2. Why It's Relevant for Amazon Influencer Content

Amazon Influencer pages are **video-first**. Short-form product showcases, haul videos, comparison clips — all need to be produced at volume with consistent branding. HyperFrames solves three pain points:

| Pain Point | How HyperFrames Helps |
|---|---|
| **Speed of production** | HTML composition is instant to iterate. No rendering queues, no After Effects. |
| **Consistency** | Same HTML template = same branding every frame. Reusable across dozens of product videos. |
| **AI-automation** | Designed for AI prompt-to-video. Nexus could generate HTML compositions from product data (Amazon API → template → MP4) without human design involvement. |
| **Text + product overlay** | The catalog of 50+ blocks handles lower thirds, price tags, ratings, callouts natively. |
| **Reusability** | Frame components are composable. Build a "product card" once, reuse across every video. |

This aligns directly with the Amazon Influencer pipeline: receive product info → generate video → publish. HyperFrames is the rendering layer that turns data into polished video frames.

---

## 3. Integration Plan — Phase 21.2

### Phase 21.2.0: Research & Prototype
- [ ] Run `npx hyperframes init` to verify local setup
- [ ] Build a single test video: product card + price overlay + CTA
- [ ] Benchmark render time for a 15-second clip
- [ ] Evaluate catalog blocks for Amazon-specific needs (star ratings, pricing, comparison tables)

### Phase 21.2.1: Nexus Integration
- [ ] Create a Nexus tool: `hyperframes_render(html_content, output_path, duration)`
- [ ] Build a template library in `~/AI_Agent/research/hyperframes-templates/`
  - `product-showcase.html` — hero image, title, price, CTA
  - `comparison.html` — side-by-side product cards
  - `haul.html` — sequential product reveals
- [ ] Add prompt-to-HTML generator: Nexus takes a Telegram message or product URL → outputs HyperFrames HTML
- [ ] Pipe rendered MP4 into the publishing pipeline (upload to Amazon Influencer page)

### Phase 21.2.2: Automation Pipeline
- [ ] Template data injection: product image, price, title, ratings from Amazon API → fill HTML
- [ ] Batch rendering: queue multiple videos, render in parallel
- [ ] Quality check: validate output video dimensions, audio sync, branding

### Deliverables
- 1 working Nexus tool for HyperFrames rendering
- 3 reusable video templates (showcase, comparison, haul)
- End-to-end test: input data → published video
- Benchmarks in `research/hyperframes_benchmarks.md`

---

## 4. Prerequisites Check

| Requirement | Needed | WattBott Status | OK? |
|---|---|---|---|
| **Node.js** | 22 (recommended) | v18.19.1 | ⚠️ Upgrade needed |
| **npm** | any modern | v9.2.0 | ✅ |
| **FFmpeg** | any modern | v6.1.1-3ubuntu5 | ✅ |
| **Disk space** | ~500MB for deps | plenty on WattBott | ✅ |

**Node 18.19 is current.** HyperFrames recommends Node 22. Node 18 should work (it's LTS), but we'll need to verify. If there's a hard 22 requirement, we can do:
```bash
nvm install 22
nvm use 22
```
Cost: ~2 minutes, zero risk.

**FFmpeg** is installed and functional. **npm** is at 9.2 — fine.

**Verdict:** All three are present. Node is the only soft concern; likely works on 18 but worth testing before committing to the upgrade.

---

## 5. Sample Workflow: "Telegram Message → Published Video"

### Step-by-step

1. **Colton sends** a Telegram message: `"Showcase the Anker PowerCore 26800, price $49.99, 4.5 stars"`

2. **Nexus parses** the message, extracts product name, price, rating. Optionally fetches product image from Amazon API or searches for one.

3. **Nexus generates HTML** by filling the `product-showcase.html` template:
   ```html
   <div data-hyperframe="scene" data-duration="10">
     <div data-animate="fade-in" data-at="0s">
       <img src="powercore.jpg" class="product-image" />
       <h1 data-animate="slide-up" data-at="0.5s">Anker PowerCore 26800</h1>
       <p data-animate="pop-in" data-at="1s" class="price">$49.99</p>
       <div data-animate="star-reveal" data-at="1.5s">
         ★★★★☆ <span>4.5 stars</span>
       </div>
       <div data-animate="fade-in" data-at="3s" class="cta">
         Shop Now →
       </div>
     </div>
   </div>
   ```

4. **Nexus renders** to MP4:
   ```bash
   npx hyperframes render input.html -o output.mp4 -d 10
   ```
   Render time estimate: ~5-15 seconds for a 10-second clip (CPU dependent).

5. **Nexus publishes** the MP4:
   - Uploads to Amazon Influencer video library (via Amazon SP-API or manual workflow)
   - Logs to run log: `projects/amazon-influencer/run-log.jsonl`
   - Sends confirmation Telegram: `"Video published: Anker PowerCore 26800 showcase (10s)"`

### Flow diagram

```
Telegram message
    → Nexus parses product data
    → Nexus fills HTML template
    → HyperFrames renders MP4
    → Upload to Amazon
    → Telegram confirmation
```

### Time estimate
- Parse + template fill: ~3s
- Render: ~10s (parallelizable)
- Upload + confirm: ~5s
- **Total: ~18 seconds per video**

---

## References
- https://github.com/heygen-com/hyperframes
- https://www.npmjs.com/package/hyperframes
- https://hyperframes.heygen.com/guides/prompting

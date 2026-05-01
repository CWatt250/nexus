---
name: iOS liquid-glass aesthetic for dashboard_v2
description: Adopted iOS-style frosted-glass UI for the unified observability dashboard at port 11438.
type: decision
last_updated: 2026-05-01
sources: []
tags: [dashboard, ui, design, dashboard-v2]
---

# 2026-04-30 — iOS liquid-glass for dashboard_v2

## Decision
The Phase 17 unified observability dashboard (`dashboard_v2/index.html`, served at localhost:11438) uses an iOS-style frosted-glass / liquid-glass aesthetic — translucent panels, soft shadows, spring animations, the bottom tab bar pattern.

## Why
- Mobile-first. Colton primarily checks the dashboard from his phone via Tailscale. iOS-native look feels right on iOS Safari.
- Glanceable. Frosted-glass panels with strong typography read at a distance better than dense desktop dashboards.
- Differentiation. Most dev-ops dashboards look the same (Grafana / Datadog clones). This one doesn't.

## How to apply
- New tabs / cards inherit the `.glass` class and the `spring` Framer Motion config.
- Stick with the established palette: `#007AFF` (active accent), `#1A1F4F` (text primary), translucent whites for surfaces.
- Don't drop in stock Tailwind admin templates — they break the visual coherence.
- See `dashboard_v2/index.html` lines 167–330 for tab bar / header conventions.

## Related
- Phase 17 — Unified Observability Dashboard
- New addition (Phase 25): the Memory tab now surfaces wiki entries alongside retros.

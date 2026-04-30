# Phase 17.5 — Dashboard v2 (iOS Liquid Glass)

Phone-first PWA control surface for Nexus. Replaces the minimal Phase
17.3 vanilla-HTML dashboard at port 11438; the legacy view is still
served at `/legacy` as a fallback.

## Why single-file React, not Next.js

The original Phase 17 spec called for Next.js + Tailwind + shadcn/ui +
framer-motion. After scoping the actual dashboard surface (4 tabs,
WebSocket subscription, glass cards, mobile-first layout) and weighing
it against:

- npm install adding hundreds of MB to the repo
- Build pipeline + dev server config + deploy step
- Adding a Node runtime to a Python-only host

…a single-file React app delivers the same visual + functional spec.
Tailwind via CDN, React 18 UMD via CDN, Babel-standalone for inline
JSX. CSS handles the Liquid Glass aesthetic (backdrop-filter, gradient
animations) and a 30-line CSS keyframe replaces framer-motion's spring
transitions for the four card mounts that actually animate.

If a future tab needs heavy interactivity (drag-and-drop, complex
charts, big-data virtualization), promote to a real Next.js app at
that point. For now, one HTML file is the right scope.

## Files

| Path | Purpose |
|------|---------|
| `dashboard_v2/index.html` | App shell, all four tabs, WS subscription, chat, dispatch flow. |
| `dashboard_v2/manifest.json` | PWA install — name, theme, icons, scope. |
| `dashboard_v2/sw.js` | Cache-first service worker for the shell, network-first for `/api/*` and `/ws/*`. |
| `dashboard_v2/icon-192.svg` | Letterform-N on liquid-glass background. |
| `dashboard_v2/icon-512.svg` | Higher-res variant. |
| `dashboard/server.py` | FastAPI server for port 11438; serves v2 if present, legacy `/legacy` always. |
| `scripts/dashboard-qr.sh` | Print a QR for the Tailscale URL — scan to install on iPhone. |

## Tabs

### Home
- Persistent chat input → `POST /chat` (the same endpoint Telegram uses)
- "Active now" card if a CC dispatch is running
- Recent activity feed pulled live from `ws://nexus-api/ws/events`

### Dispatch
- Textarea + 4 quick templates (Build / Fix / Tests / Refactor)
- Per-dispatch label + budget controls
- Pending-approval section (Go / Cancel buttons)
- Currently-running card (live elapsed time)
- Queued list, ordered FIFO
- Recent results — expand each card for summary, commits, log tail
- Force button appears when over monthly budget

### Memory
- Recent retros (`memory/retros/`) — paginated, click to expand
- Hits `GET /api/memory/retros` and `GET /api/memory/retro/{id}`

### Settings
- Service health: `nexus-*` units, restart per row (uses `POST /api/restart`)
- Claude Code monthly budget bar
- Connection diagnostic (API URL, WS URL, Tailscale URL)

## Backend wiring

All endpoints live in `nexus_api.py` (port 11435) so the dashboard runs
side-by-side with Open WebUI / Telegram traffic without contention:

```
GET  /api/dispatches?limit=N        — queue snapshot + recent results + budget
POST /api/dispatch                  — new dispatch (dashboard mirror of the tool)
POST /api/dispatch/approve          — release a pending dispatch
POST /api/dispatch/cancel           — drop a pending or queued dispatch
GET  /api/dispatch/{id}/log?tail=N  — log tail for the expanded card
GET  /api/services                  — `systemctl is-active` for nexus-*
POST /api/restart                   — restart selected nexus-* services
GET  /api/memory/retros?limit=N     — recent retro index
GET  /api/memory/retro/{id}         — full retro markdown body
WS   /ws/events                     — live event bus (existing)
```

Polling cadence: queue snapshot every 4s, services every 10s. Plus
push: any `cc_dispatch_*` event over WS triggers an immediate refresh.

## PWA install

- iOS Safari → Share → Add to Home Screen
- Android Chrome → menu → Install app
- Theme color `#0A0E27` matches the app background; status bar blends
- `display: standalone` so the launched app is full-bleed without browser chrome

The QR script makes phone setup one-tap:

```
~/AI_Agent/scripts/dashboard-qr.sh
```

It prints a UTF-8 QR encoding `http://100.124.210.84:11438` (Tailscale
IP). Override with an arg:
```
~/AI_Agent/scripts/dashboard-qr.sh http://wattbott.local:11438
```

## Operating notes

- The dashboard expects nexus-api to be running on the same host. If
  the API URL needs to change, edit the `API` constant near the top of
  `dashboard_v2/index.html`.
- The legacy dashboard is still at `/legacy` — use it as a triage
  fallback if the v2 build breaks for any reason.
- Service worker caches `/`, `/manifest.json`, `/icon-*.svg` only. A
  full hard reload (Cmd-Shift-R / clear site data) drops the cache so
  HTML changes pick up immediately during dev.

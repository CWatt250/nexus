# Phase 36 — Computer Use Agent

_Built: 2026-05-07_

Fire-and-forget agent that drives a real browser on Xvfb `:99` to do GUI
work on cloud dashboards (Supabase, Vercel, Stripe, GitHub, etc.).
Uses Anthropic's native Computer Use API (`computer_20250124` tool +
`computer-use-2025-01-24` beta) on `claude-sonnet-4-6`. Triggered via
`/computer` from Telegram.

## Architecture

```
Telegram /computer <task>
        │
        ▼
telegram_listener.computer_command
        │
        ▼  (asyncio.create_task)
_run_computer_in_background
        │
        ├─► cu_browser.launch()        # Chromium/Firefox on :99 with persistent profile
        ├─► cu_recorder.Recorder()     # ffmpeg x11grab → session.mp4
        └─► computer_agent.run_task()
                │
                ▼  (loop)
            screenshot (scrot) → Anthropic API → tool_use blocks → execute via xdotool
                │
                ▼
            stop_reason=end_turn → AgentResult
```

## Files

| File | Purpose |
|------|---------|
| `tools/computer_agent.py` | Agent loop, safety stops, cost tracking |
| `tools/cu_browser.py` | Chromium/Firefox launcher, persistent profile |
| `tools/cu_recorder.py` | FFmpeg x11grab session recorder (optional) |
| `tools/telegram_listener.py` | `/computer` command + 30s screenshot updates |
| `config/cost_limits.yaml` | `computer_use:` ceilings ($5/task, $25/day, 30min, 200 iters) |
| `cu_logs/<task_id>/` | Per-task screenshots, transcript.json, session.mp4 |
| `cu_profile/{chromium,firefox}/` | Persistent browser profile (gitignored) |

## Dependencies (subprocess-only)

The agent intentionally avoids `pyautogui` — its `mouseinfo` dep calls
`sys.exit()` if `python3-tk` isn't installed, which breaks the import for
the entire tool module. Instead:

- Screenshots: `scrot -o`
- Mouse + keyboard: `xdotool` (key, type, click, mousemove, etc.)
- Display probe: `xdpyinfo`
- Session video: `ffmpeg -f x11grab`

This decouples the agent from any Python GUI library.

## First-time setup

1. Run sudo deps (installs chromium, ffmpeg, xdotool, scrot, x11-utils,
   plus python3-tk in case other tools want it):
   ```bash
   sudo bash /tmp/sudo-phase-36.sh
   ```
   **Without this step, `xdotool` is missing and every action will fail
   with "xdotool not installed".** The recon for Phase 35 found xdotool
   referenced in code but never confirmed the binary — it isn't.
2. Confirm Xvfb is up: `systemctl status nexus-xvfb` (Phase 35).
3. Manual login per service (one-time):
   ```bash
   DISPLAY=:99 chromium --user-data-dir=$HOME/AI_Agent/cu_profile/chromium &
   # — VNC into :99 (or use x11vnc) and log into Supabase/Vercel/Stripe by hand.
   # The cookie jar persists in cu_profile/chromium/Default/Cookies.
   ```
4. Test from Telegram: `/computer take a screenshot of the supabase dashboard`.

## Safety stops

The agent halts and writes `cu_logs/<id>/HALT.txt` when it's about to:

- Type text matching destructive verbs (delete, drop, destroy, remove, terminate, erase, wipe)
- Operate on an active window whose title hints at billing/payment/api-keys/transfer/danger zones
- Type "send email", "publish", "transfer ownership"

Override by appending `--unsafe` to the `/computer` task string. Use sparingly —
this is the only seatbelt against a model misreading a confirmation modal.

## Cost model

- Pricing assumed: `$3/M input, $15/M output` (claude-sonnet-4-6).
- Each iteration = ~1 screenshot (≈2-5K tokens) + conversation history + ~200-1000 output tokens.
- Typical task: 10-30 iterations → $0.30-$2.00 actual.
- Hard cap: $5/task. Reaches the cap → loop aborts, status "timeout".

## Known limits (Phase 36.0)

- Single browser instance, single session — no parallel tasks.
- No OCR-based URL detection (uses window title only); a borderline-risky
  page that doesn't surface "/billing" in the title can slip the safety net.
- No inbound Telegram commands during a run — operator can't say "yes go
  ahead". Halts terminate; re-run with `--unsafe` if you want to proceed.
- No headless Chrome — Xvfb is the display, but the browser is rendered.

## Roadmap → Phase 36.1

- Inbound Telegram approval for halts ("/computer approve cu_xyz")
- Chromium DevTools Protocol channel for ground-truth URL inspection
- Parallel sessions on `:100`, `:101`, ...
- Voice control via Whisper input route

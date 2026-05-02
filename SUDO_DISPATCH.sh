#!/usr/bin/env bash
# SUDO_DISPATCH.sh — Phase 22 + 17.5 + polish-pass activation steps.
# Run manually after Nexus's autonomous build commits its dispatch system.
# Each block is annotated with the task it covers.
#
# Loud-fail mode: NO `set -e`. Every step runs even if the previous one
# fails. Successes and failures are tallied and printed at the end so
# silent partial failures (today's bug: EOD timer + Xvfb didn't install
# but no one noticed for hours) can't recur.

set -uo pipefail   # NB: no -e — we want every step to attempt

STEPS_OK=()
STEPS_FAIL=()

echo "Nexus Phase 22 + 17.5 sudo activation."
echo "Review each section before running. Loud-fail mode."

# ---------------------------------------------------------------------------
echo ""
echo "═══ STEP 1: install NOPASSWD sudoers entry for nexus-* restarts ═══"
# 22.4 — sudoers entry so nexus_restart_services can bounce its own units.
if (
  sudo install -m 0440 /dev/stdin /etc/sudoers.d/nexus-restart <<'EOF'
Cmnd_Alias NEXUS_RESTARTS = /bin/systemctl restart nexus-*.service
cwatt250 ALL=(ALL) NOPASSWD: NEXUS_RESTARTS
EOF
  sudo visudo -cf /etc/sudoers.d/nexus-restart
); then
  echo "✅ STEP 1: SUCCESS"
  STEPS_OK+=("Step 1: NOPASSWD sudoers entry installed")
else
  echo "❌ STEP 1: FAILED — visudo rejected the file or sudo is not configured"
  STEPS_FAIL+=("Step 1: NOPASSWD sudoers entry")
fi

# ---------------------------------------------------------------------------
echo ""
echo "═══ STEP 2: install + start nexus-cc-dispatcher + nexus-cc-reporter ═══"
# 22 — dispatcher daemon (one-at-a-time CC runner) + reporter daemon.
if (
  sudo cp /tmp/nexus-cc-dispatcher.service /etc/systemd/system/nexus-cc-dispatcher.service \
  && sudo cp /tmp/nexus-cc-reporter.service   /etc/systemd/system/nexus-cc-reporter.service \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable nexus-cc-dispatcher.service \
  && sudo systemctl enable nexus-cc-reporter.service \
  && sudo systemctl start  nexus-cc-dispatcher.service \
  && sudo systemctl start  nexus-cc-reporter.service
); then
  echo "✅ STEP 2: SUCCESS"
  STEPS_OK+=("Step 2: cc-dispatcher + cc-reporter installed and started")
else
  echo "❌ STEP 2: FAILED — check /tmp/nexus-cc-{dispatcher,reporter}.service exist"
  STEPS_FAIL+=("Step 2: cc-dispatcher + cc-reporter")
fi

# ---------------------------------------------------------------------------
echo ""
echo "═══ STEP 3: restart Phase 22 dependents (api, agent, task-worker, telegram) ═══"
if (
  sudo systemctl restart nexus-api.service \
  && sudo systemctl restart nexus-agent.service \
  && sudo systemctl restart nexus-task-worker.service \
  && sudo systemctl restart nexus-telegram.service
); then
  echo "✅ STEP 3: SUCCESS"
  STEPS_OK+=("Step 3: Phase 22 dependents restarted")
else
  echo "❌ STEP 3: FAILED — one of the nexus services is missing or unhealthy"
  STEPS_FAIL+=("Step 3: Phase 22 dependents restart")
fi

# ---------------------------------------------------------------------------
echo ""
echo "═══ STEP 4: install xvfb + start nexus-xvfb headless display :99 ═══"
# Polish #7 — Xvfb fallback so computer_use_tool.screenshot() works headless.
if (
  sudo apt-get install -y xvfb x11-utils \
  && sudo cp /tmp/nexus-xvfb.service /etc/systemd/system/nexus-xvfb.service \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable nexus-xvfb.service \
  && sudo systemctl start  nexus-xvfb.service
); then
  echo "✅ STEP 4: SUCCESS"
  STEPS_OK+=("Step 4: Xvfb installed + nexus-xvfb running on :99")
  # Probe :99 (best-effort — failure here doesn't fail the step itself
  # since the systemd unit may still be initialising).
  if DISPLAY=:99 xdpyinfo >/dev/null 2>&1; then
    echo "       :99 answered xdpyinfo probe."
  else
    echo "       warn: :99 not yet answering — give it a few seconds and retry"
  fi
else
  echo "❌ STEP 4: FAILED — apt-get blocked, /tmp/nexus-xvfb.service missing, or Xvfb won't start"
  STEPS_FAIL+=("Step 4: Xvfb install + service")
fi

# ---------------------------------------------------------------------------
echo ""
echo "═══ STEP 5: move EOD summary timer 17:00 local → 20:00 America/Los_Angeles ═══"
# Polish #6 — DST-aware Pacific 20:00 trigger.
if (
  sudo cp /tmp/nexus-eod-summary.timer /etc/systemd/system/nexus-eod-summary.timer \
  && sudo systemctl daemon-reload \
  && sudo systemctl restart nexus-eod-summary.timer
); then
  echo "✅ STEP 5: SUCCESS"
  STEPS_OK+=("Step 5: EOD timer moved to 20:00 America/Los_Angeles")
  systemctl list-timers nexus-eod-summary.timer --no-pager 2>/dev/null | head -5
else
  echo "❌ STEP 5: FAILED — /tmp/nexus-eod-summary.timer missing or systemd rejected the file"
  STEPS_FAIL+=("Step 5: EOD timer move")
fi

# ---------------------------------------------------------------------------
echo ""
echo "═══ STEP 6: verify journals for cc-dispatcher + cc-reporter ═══"
# Best-effort. Always reports SUCCESS so it doesn't poison the summary —
# this is a read step, not an install step.
echo "--- nexus-cc-dispatcher (last 20 lines) ---"
journalctl -u nexus-cc-dispatcher.service -n 20 --no-pager 2>&1 | tail -20
echo "--- nexus-cc-reporter (last 20 lines) ---"
journalctl -u nexus-cc-reporter.service   -n 20 --no-pager 2>&1 | tail -20
echo "✅ STEP 6: journal tail printed (read-only, always succeeds)"
STEPS_OK+=("Step 6: journal tail")

# ---------------------------------------------------------------------------
echo ""
echo "═══════════════════════════════════════"
echo "ACTIVATION SUMMARY"
echo "═══════════════════════════════════════"
echo "Successful steps: ${#STEPS_OK[@]}"
for s in "${STEPS_OK[@]}"; do echo "  ✅ $s"; done
echo "Failed steps: ${#STEPS_FAIL[@]}"
for s in "${STEPS_FAIL[@]}"; do echo "  ❌ $s"; done
echo "═══════════════════════════════════════"

# Exit non-zero if any step failed so a CI / wrapper script can detect it.
if [ "${#STEPS_FAIL[@]}" -gt 0 ]; then
  exit 1
fi
exit 0

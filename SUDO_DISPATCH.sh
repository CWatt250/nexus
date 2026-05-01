#!/usr/bin/env bash
# SUDO_DISPATCH.sh — Phase 22 + 17.5 activation steps.
# Run manually after Nexus's autonomous build commits its dispatch system.
# Each block is annotated with the task it covers.
set -euo pipefail
echo "Nexus Phase 22 + 17.5 sudo activation."
echo "Review each section before running."

# ---------------------------------------------------------------------------
# 22.4 — NOPASSWD sudoers entry so nexus_restart_services can bounce its own
# systemd units without prompting for a password. Scoped tightly to
# `systemctl restart nexus-*`.
# ---------------------------------------------------------------------------
sudo install -m 0440 /dev/stdin /etc/sudoers.d/nexus-restart <<'EOF'
Cmnd_Alias NEXUS_RESTARTS = /bin/systemctl restart nexus-*.service
cwatt250 ALL=(ALL) NOPASSWD: NEXUS_RESTARTS
EOF
sudo visudo -cf /etc/sudoers.d/nexus-restart

# ---------------------------------------------------------------------------
# 22 — install + enable the dispatcher daemon (one-at-a-time CC runner).
# ---------------------------------------------------------------------------
sudo cp /tmp/nexus-cc-dispatcher.service /etc/systemd/system/nexus-cc-dispatcher.service
sudo cp /tmp/nexus-cc-reporter.service   /etc/systemd/system/nexus-cc-reporter.service
sudo systemctl daemon-reload
sudo systemctl enable nexus-cc-dispatcher.service
sudo systemctl enable nexus-cc-reporter.service
sudo systemctl start  nexus-cc-dispatcher.service
sudo systemctl start  nexus-cc-reporter.service

# ---------------------------------------------------------------------------
# 22 — restart services that import the new tools so they're hot.
# ---------------------------------------------------------------------------
sudo systemctl restart nexus-api.service
sudo systemctl restart nexus-agent.service
sudo systemctl restart nexus-task-worker.service
sudo systemctl restart nexus-telegram.service

# ---------------------------------------------------------------------------
# 17.5 — restart dashboard once the v2 build is on disk.
# (Skip until dashboard_v2 is built and ready to serve port 11438.)
# ---------------------------------------------------------------------------
# sudo systemctl restart nexus-dashboard.service

# ---------------------------------------------------------------------------
# Polish #6 — move EOD summary trigger from 17:00 local → 20:00 Pacific.
# OnCalendar with America/Los_Angeles suffix handles DST automatically.
# ---------------------------------------------------------------------------
sudo cp /tmp/nexus-eod-summary.timer /etc/systemd/system/nexus-eod-summary.timer
sudo systemctl daemon-reload
sudo systemctl restart nexus-eod-summary.timer
systemctl list-timers nexus-eod-summary.timer --no-pager

# ---------------------------------------------------------------------------
# 22 — verify
# ---------------------------------------------------------------------------
journalctl -u nexus-cc-dispatcher.service -n 20 --no-pager
journalctl -u nexus-cc-reporter.service   -n 20 --no-pager
echo "✅ Phase 22 sudo activation complete."

#!/usr/bin/env bash
# Phase 3 — Nexus headless desktop on :99 + tailnet VNC.
# Run with:  sudo bash ~/AI_Agent/scripts/sudo_phase3.sh
#
# Non-sudo prerequisites (already done by the Phase 3 build, re-run if missing):
#   x11vnc -storepasswd <pw> ~/.vnc/passwd     -> /home/cwatt250/.vnc/passwd
#   plaintext copy (0600):                       /home/cwatt250/.vnc/nexus-vnc-password.txt
set -euo pipefail
ROOT=/home/cwatt250/AI_Agent
OWNER=cwatt250

if [ ! -f /home/$OWNER/.vnc/passwd ]; then
  echo "ERROR: /home/$OWNER/.vnc/passwd missing. As $OWNER run:"
  echo "  x11vnc -storepasswd '<password>' ~/.vnc/passwd && chmod 600 ~/.vnc/passwd"
  exit 1
fi

echo "[1/5] apt: window manager, panel, AT-SPI python bindings, fonts, xterm"
export DEBIAN_FRONTEND=noninteractive
apt-get install -y openbox tint2 python3-pyatspi at-spi2-core fonts-dejavu-core xterm

echo "[2/5] install nexus-desktop.service (openbox + tint2 + Chrome on :99)"
install -m 0755 -o $OWNER -g $OWNER "$ROOT/scripts/desktop_session.sh" "$ROOT/scripts/desktop_session.sh"
install -m 0644 "$ROOT/scripts/nexus-desktop.service" /etc/systemd/system/nexus-desktop.service

echo "[3/5] nexus-vnc drop-in: listen on tailnet 100.124.210.84 with password"
mkdir -p /etc/systemd/system/nexus-vnc.service.d
install -m 0644 "$ROOT/scripts/nexus-vnc-tailnet.conf" /etc/systemd/system/nexus-vnc.service.d/10-tailnet.conf

echo "[4/5] reload + enable"
systemctl daemon-reload
systemctl enable --now nexus-desktop.service
systemctl restart nexus-vnc.service
sleep 2
systemctl --no-pager --lines=0 status nexus-desktop.service nexus-vnc.service || true

echo "[5/5] verify"
sudo -u $OWNER DISPLAY=:99 wmctrl -l || echo "(wmctrl: no windows yet — Chrome may still be starting)"
ss -ltnp | grep 5900 || echo "WARN: x11vnc not listening on 5900 yet (is tailscale up?)"

cat <<EOF

done.
  VNC connect string:  100.124.210.84:5900
  password:            cat /home/$OWNER/.vnc/nexus-vnc-password.txt
  Telegram:            /watch  /screenshot  /open <url>  /desktop <task>
EOF

# --- Phase 1+2 code needs a service restart to take effect ---
echo "[phase1+2] restarting Nexus services"
systemctl restart nexus-telegram nexus-task-worker nexus-agent nexus-api nexus-cc-dispatcher
systemctl start nexus-prewarm
echo "all done. Telegram: /help, /screenshot, /watch, /desktop <task>, /think on"

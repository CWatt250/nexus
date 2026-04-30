#!/usr/bin/env bash
# SUDO_DEPENDENCIES_R5.sh — Docker + SearXNG bring-up (R5 / 2026-04-29).
#
# Replaces Brave Search with a self-hosted SearXNG container. Run this
# once. After it finishes, Nexus's `searxng_search` and `web_search`
# tools start returning real results without restarting any Nexus
# service (the tools talk HTTP, the container is a separate process).
#
# All commands are sudo because Docker requires root to install and
# enable the daemon. Once the daemon is running, the rest of the bring-up
# (compose up) does NOT need sudo if your user is in the docker group.

set -euo pipefail

# ============================================================================
# 1. Install Docker Engine + compose plugin (Ubuntu 24.04 noble)
# ============================================================================
# Official Docker repo install. The Ubuntu-shipped docker.io is older
# and missing the compose plugin we use in docker-compose.yml.

if ! command -v docker >/dev/null 2>&1; then
  echo "[1/5] Installing Docker Engine..."
  sudo apt update
  sudo apt install -y ca-certificates curl gnupg

  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
    https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

  sudo apt update
  sudo apt install -y \
    docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin

  # Daemon should auto-start, but be explicit.
  sudo systemctl enable --now docker
else
  echo "[1/5] Docker already installed: $(docker --version)"
fi

# ============================================================================
# 2. Add cwatt250 to the docker group (skip sudo for `docker` commands)
# ============================================================================
if ! id -nG cwatt250 | grep -qw docker; then
  echo "[2/5] Adding cwatt250 to docker group (LOG OUT + BACK IN to take effect)..."
  sudo usermod -aG docker cwatt250
  GROUP_NEEDS_RELOG=1
else
  echo "[2/5] cwatt250 already in docker group"
  GROUP_NEEDS_RELOG=0
fi

# ============================================================================
# 3. Bring up the SearXNG container
# ============================================================================
# We `sudo` this once even with group membership so it works in a fresh
# shell that hasn't picked up the new group yet. After your next login
# you can run plain `docker compose up -d` from ~/AI_Agent/searxng/.
echo "[3/5] Pulling and starting SearXNG..."
cd /home/cwatt250/AI_Agent/searxng
sudo docker compose pull
sudo docker compose up -d

# ============================================================================
# 4. Install the systemd unit so the container restarts on boot via
#    docker compose (idempotent — restart=unless-stopped on the
#    container itself already covers reboot, but the unit makes the
#    state visible via `systemctl status nexus-searxng`).
# ============================================================================
echo "[4/5] Installing nexus-searxng.service..."
sudo cp /home/cwatt250/AI_Agent/searxng/nexus-searxng.service \
        /etc/systemd/system/nexus-searxng.service
sudo systemctl daemon-reload
sudo systemctl enable nexus-searxng.service

# ============================================================================
# 5. Smoke test the API
# ============================================================================
echo "[5/5] Smoke testing http://localhost:8888/search?q=hello&format=json ..."
sleep 4   # give uwsgi a beat to bind
if curl -fsS "http://localhost:8888/search?q=hello&format=json" \
     | head -c 400 ; then
  echo
  echo
  echo "✅ SearXNG up. Container status:"
  sudo docker ps --filter name=nexus-searxng --format \
    'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
else
  echo
  echo "❌ Smoke failed. Check: sudo docker logs nexus-searxng"
fi

if [ "$GROUP_NEEDS_RELOG" = "1" ]; then
  echo
  echo "NOTE: Log out and back in (or run 'newgrp docker') so plain"
  echo "      'docker' commands work without sudo."
fi

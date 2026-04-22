#!/bin/bash
# ============================================================================
# NEXUS MASTER SUDO COMMANDS
# ============================================================================
# Run this script as root or with sudo to install all Nexus dependencies
# and services created during the build.
#
# Usage: sudo bash ~/AI_Agent/SUDO_COMMANDS.sh
#
# This script:
# 1. Installs system dependencies (apt packages)
# 2. Installs global npm packages
# 3. Installs systemd services
# 4. Enables services for auto-start
# ============================================================================

set -e

echo "=============================================="
echo "  NEXUS BUILD - SYSTEM INSTALLATION"
echo "=============================================="
echo ""

# ----------------------------------------------------------------------------
# SECTION 1: APT PACKAGES
# ----------------------------------------------------------------------------
echo "[1/5] Installing system packages..."

# Phase 5.3 - Chronicle (screenshot + OCR)
apt install -y scrot tesseract-ocr

# Phase 10 - Godot Engine (try snap first, fall back to apt)
echo "Installing Godot..."
snap install godot-4 --classic 2>/dev/null || apt install -y godot 2>/dev/null || echo "Godot: manual install needed from godotengine.org"

echo "[1/5] APT packages complete."
echo ""

# ----------------------------------------------------------------------------
# SECTION 2: NPM GLOBAL PACKAGES
# ----------------------------------------------------------------------------
echo "[2/5] Installing global npm packages..."

# Phase 7.4 - Vercel CLI
npm install -g vercel

# Phase 8 - Electron for Sparky overlay
npm install -g electron

echo "[2/5] NPM packages complete."
echo ""

# ----------------------------------------------------------------------------
# SECTION 3: COPY SERVICE FILES
# ----------------------------------------------------------------------------
echo "[3/5] Installing systemd service files..."

# Service files are in /tmp/ - copy to /etc/systemd/system/
SERVICE_FILES=(
    "nexus-chronicle.service"
    "nexus-telegram.service"
    "nexus-watchdog.service"
    "nexus-git-watcher.service"
    "nexus-file-watcher.service"
    "nexus-clipboard-watcher.service"
    "nexus-patterns.service"
)

for svc in "${SERVICE_FILES[@]}"; do
    if [ -f "/tmp/$svc" ]; then
        cp "/tmp/$svc" "/etc/systemd/system/"
        echo "  Installed: $svc"
    else
        echo "  Skipped (not found): $svc"
    fi
done

echo "[3/5] Service files installed."
echo ""

# ----------------------------------------------------------------------------
# SECTION 4: RELOAD SYSTEMD AND ENABLE SERVICES
# ----------------------------------------------------------------------------
echo "[4/5] Configuring systemd..."

systemctl daemon-reload

# Enable services for auto-start
SERVICES_TO_ENABLE=(
    "nexus-chronicle"
    "nexus-telegram"
    "nexus-watchdog"
    "nexus-git-watcher"
)

for svc in "${SERVICES_TO_ENABLE[@]}"; do
    systemctl enable "$svc" 2>/dev/null && echo "  Enabled: $svc" || echo "  Skipped: $svc"
done

echo "[4/5] Systemd configured."
echo ""

# ----------------------------------------------------------------------------
# SECTION 5: SUMMARY
# ----------------------------------------------------------------------------
echo "[5/5] Installation complete!"
echo ""
echo "=============================================="
echo "  NEXT STEPS"
echo "=============================================="
echo ""
echo "1. Start all Nexus services:"
echo "   sudo systemctl start nexus-chronicle nexus-telegram nexus-git-watcher"
echo ""
echo "2. Check service status:"
echo "   sudo systemctl status nexus-*"
echo ""
echo "3. View logs:"
echo "   journalctl -u nexus-telegram -f"
echo ""
echo "4. Configure API keys in ~/AI_Agent/.env:"
echo "   - TELEGRAM_BOT_TOKEN (see docs/telegram-setup.md)"
echo "   - TELEGRAM_CHAT_ID"
echo "   - BRAVE_SEARCH_API_KEY (optional)"
echo "   - VERCEL_TOKEN (optional)"
echo "   - ERNIE_API_KEY (optional)"
echo ""
echo "5. Test Nexus API:"
echo "   curl http://localhost:11435/health"
echo ""
echo "6. Start Sparky overlay:"
echo "   cd ~/AI_Agent/sparky/overlay && npm install && ./start.sh"
echo ""
echo "=============================================="
echo "  BUILD COMPLETE!"
echo "=============================================="

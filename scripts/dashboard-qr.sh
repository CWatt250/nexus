#!/usr/bin/env bash
# Phase 17.5 — print a QR code for the Tailscale dashboard URL so Colton
# can scan from his phone and add the Liquid Glass dashboard as a PWA.
#
# Requires `qrencode`. Install with:
#     sudo apt-get install -y qrencode
set -euo pipefail

URL="${1:-http://100.124.210.84:11438}"

if ! command -v qrencode >/dev/null 2>&1; then
  echo "qrencode is not installed. Run:"
  echo "    sudo apt-get install -y qrencode"
  echo
  echo "URL to add to home screen manually: $URL"
  exit 1
fi

echo
echo "Scan to install Nexus PWA (URL: $URL)"
echo
qrencode -t UTF8 "$URL"
echo
echo "1. Open the link in Safari (iOS) or Chrome (Android)"
echo "2. Tap Share → Add to Home Screen"
echo "3. Tap the new icon to launch in fullscreen mode"

#!/usr/bin/env bash
# SUDO_COMMANDS_R4.sh — manual sudo steps from the R4 work.
#
# Run these by hand, in order. Nexus does NOT run sudo — these are
# collected for Colton.

set -euo pipefail

# --- Fix #2 (X.com / JS-heavy URL render) ---------------------------------
# Playwright's Python wheel + Chromium binary are already installed in
# the Nexus venv. Chromium needs a handful of system shared libraries
# (font, audio, video stacks) to launch reliably — the Python install
# can't pull these via pip. If `browser_render` ever fails with
# "missing libnss3.so" or similar, run:
#
#   sudo /home/cwatt250/AI_Agent/venv/bin/playwright install-deps chromium
#
# This is the official Playwright helper. It maps to:
#   sudo apt install -y \
#     libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
#     libxcomposite1 libxdamage1 libxrandr2 libgbm1 libxkbcommon0 \
#     libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 \
#     libxshmfence1 libgtk-3-0
#
# Verify after install:
#   /home/cwatt250/AI_Agent/venv/bin/python3 -c \
#     "from playwright.sync_api import sync_playwright; \
#      pw=sync_playwright().start(); b=pw.chromium.launch(); \
#      print(b.version); b.close(); pw.stop()"

echo "R4 sudo commands documented above. None auto-execute — read first."

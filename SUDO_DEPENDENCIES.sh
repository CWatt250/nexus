#!/usr/bin/env bash
# SUDO_DEPENDENCIES.sh — manual sudo installs from the dep audit (2026-04-29).
#
# Nexus does NOT run sudo. Run this by hand, top to bottom.
# Everything below is system-wide (apt, npm -g) — pip stuff already
# happened in the venv, no sudo needed.
#
# Two tiers: CRITICAL is needed now; FUTURE unblocks queued roadmap work.
# Run CRITICAL first, FUTURE when the matching phase comes up.

set -euo pipefail

# ============================================================================
# CRITICAL — fixes/safeguards for things already in use
# ============================================================================

# --- A. Playwright/Chromium safety net -------------------------------------
# Chromium currently launches fine without these (24.04 ships most of the
# stack via its 64-bit-time-t variants). Install them anyway so the next
# Playwright update can't brick browser_render. The five packages dpkg
# flagged missing on this box:
#   libatk-bridge2.0-0  libatk1.0-0  libatspi2.0-0  libcups2  libgtk-3-0
# The other "missing" name (libasound2) was renamed to libasound2t64 in
# 24.04 and is already installed — no action needed.
PLAYWRIGHT_DEPS=(
  libatk-bridge2.0-0
  libatk1.0-0
  libatspi2.0-0
  libcups2
  libgtk-3-0
)

# Same effect via Playwright's official helper. Either pick the explicit
# list above OR uncomment this line — don't run both.
#   sudo /home/cwatt250/AI_Agent/venv/bin/playwright install-deps chromium

# --- B. Image ops (imagemagick) --------------------------------------------
# Image generation pipeline (image_gen_tool, game_pipeline) shells out to
# `convert` for resizing/composing. Not blocking until you queue an
# image-pipeline task.
IMAGE_OPS=(
  imagemagick
)

# ============================================================================
# FUTURE — queued roadmap, install when the phase starts
# ============================================================================

# --- C. Secrets scanning (Phase 12.2 / 14.x) -------------------------------
# gitleaks is the lighter of the two; trufflehog is the noisier one with
# more detectors. Pick gitleaks for CI hooks, trufflehog for one-off
# audits. trufflehog is NOT in the Ubuntu repos — install via the upstream
# script or Go.
SECRETS_SCAN=(
  gitleaks
)
# trufflehog (no apt package on 24.04):
#   curl -sSfL https://raw.githubusercontent.com/trufflesecurity/trufflehog/main/scripts/install.sh \
#     | sudo sh -s -- -b /usr/local/bin

# --- D. Backups (Final phase / general hygiene) ----------------------------
BACKUP_TOOLS=(
  restic
)

# --- E. Node global package managers (optional alternatives to npm) -------
# npm is already installed and handles every roadmap install. yarn and
# pnpm are nice-to-haves for repos that pin them in packageManager.
NODE_GLOBAL=(
  # yarn   # uncomment if you ever clone a yarn-locked repo
  # pnpm   # uncomment if you ever clone a pnpm-locked repo
)

# ============================================================================
# Run
# ============================================================================

ALL_APT=(
  "${PLAYWRIGHT_DEPS[@]}"
  "${IMAGE_OPS[@]}"
  "${SECRETS_SCAN[@]}"
  "${BACKUP_TOOLS[@]}"
)

echo "About to apt install: ${ALL_APT[*]}"
echo "Press Ctrl-C in 5 seconds to abort..."
sleep 5

sudo apt update
sudo apt install -y "${ALL_APT[@]}"

# Node globals — only if any are uncommented above.
if [ ${#NODE_GLOBAL[@]} -gt 0 ]; then
  sudo npm install -g "${NODE_GLOBAL[@]}"
fi

# ============================================================================
# Verify
# ============================================================================
echo
echo "=== Verification ==="
for pkg in "${ALL_APT[@]}"; do
  if dpkg -l "$pkg" 2>/dev/null | awk 'NR==6 {exit ($1=="ii" ? 0 : 1)}'; then
    printf '  %-30s OK\n' "$pkg"
  else
    printf '  %-30s FAILED\n' "$pkg"
  fi
done

# Smoke-test Chromium launch from the venv.
echo
echo "=== Chromium launch smoke ==="
/home/cwatt250/AI_Agent/venv/bin/python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    b = pw.chromium.launch()
    print('chromium ok, version', b.version)
    b.close()
" || echo "Chromium failed — investigate before relying on browser_render."

# Optional verifications for the FUTURE-tier installs:
command -v gitleaks >/dev/null && echo "  gitleaks: $(gitleaks version)"
command -v restic   >/dev/null && echo "  restic:   $(restic version | head -1)"

echo
echo "Done. If anything failed, paste the error and I (Nexus) will dig in."

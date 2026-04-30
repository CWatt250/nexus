#!/usr/bin/env bash
# SUDO_INTEGRATIONS.sh — gaps surfaced by EXTERNAL_INTEGRATIONS.md (R5+).
#
# Two earlier R-files (SUDO_DEPENDENCIES.sh, SUDO_DEPENDENCIES_R5.sh)
# already covered Playwright libs, Docker, gitleaks, restic. This file
# fills the integration-audit gaps that didn't fit those buckets:
#
#   - ffmpeg          (CRITICAL — was a false-positive in the dep audit)
#   - fd-find         (alias `fdfind`; was also a false positive)
#   - gh              (optional GitHub CLI for shell convenience)
#   - npm globals: supabase, pnpm, yarn, uv (yarn/pnpm commented out)
#
# Run this end-to-end. It's idempotent.

set -euo pipefail

# ============================================================================
# 1. Apt packages: ffmpeg + fd-find + gh
# ============================================================================
APT_PACKAGES=(
  ffmpeg          # CRITICAL — pydub / faster-whisper / future audio gen
  fd-find         # nice-to-have — binary lands as `fdfind`
)

# gh is in the upstream Docker repo for Ubuntu, not in default repos.
# Install via the official keyring + source list.
if ! command -v gh >/dev/null 2>&1; then
  echo "[1a] Adding GitHub CLI repo..."
  sudo install -dm 755 /etc/apt/keyrings
  out=$(mktemp)
  wget -nv -O- https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | sudo dd of=/etc/apt/keyrings/githubcli-archive-keyring.gpg
  sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  APT_PACKAGES+=(gh)
fi

echo "[1b] Installing apt packages: ${APT_PACKAGES[*]}"
sudo apt update
sudo apt install -y "${APT_PACKAGES[@]}"

# ============================================================================
# 2. Symlink fdfind → fd so codebase tooling works either way
# ============================================================================
if command -v fdfind >/dev/null 2>&1 && ! command -v fd >/dev/null 2>&1; then
  echo "[2] Symlinking fdfind → fd"
  sudo ln -sf "$(command -v fdfind)" /usr/local/bin/fd
fi

# ============================================================================
# 3. npm globals
# ============================================================================
NPM_GLOBALS=(
  supabase    # Phase 16.4 + 23.2
  # pnpm      # uncomment when a cloned repo pins it
  # yarn      # uncomment when a cloned repo pins it
)
echo "[3] Installing npm globals: ${NPM_GLOBALS[*]}"
sudo npm install -g "${NPM_GLOBALS[@]}"

# ============================================================================
# 4. uv (faster Python pkg installer) — optional, no sudo needed.
#    Uncomment to install for the cwatt250 user.
# ============================================================================
# echo "[4] Installing uv for cwatt250..."
# sudo -u cwatt250 sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

# ============================================================================
# 5. Verify
# ============================================================================
echo
echo "=== Verification ==="
for cmd in ffmpeg fd fdfind gh supabase; do
  if command -v "$cmd" >/dev/null 2>&1; then
    printf '  %-12s OK   %s\n' "$cmd" "$("$cmd" --version 2>&1 | head -1 | cut -c1-80)"
  else
    printf '  %-12s MISSING\n' "$cmd"
  fi
done

# Sanity-check pydub now finds ffmpeg (the warning that prompted this file).
echo
echo "=== pydub ffmpeg detection ==="
/home/cwatt250/AI_Agent/venv/bin/python3 -c "
import warnings
with warnings.catch_warnings():
    warnings.simplefilter('error')
    try:
        from pydub import AudioSegment   # noqa
        print('pydub OK — ffmpeg found')
    except RuntimeWarning as exc:
        print('pydub still warning:', exc)
" || echo "(pydub import failed — investigate)"

echo
echo "=== gh auth ==="
echo "Run 'gh auth login' to authenticate with the GitHub CLI."
echo "(Use the same fine-grained PAT or pick 'Login with a web browser'.)"
echo
echo "Done."

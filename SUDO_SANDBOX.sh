#!/usr/bin/env bash
# G4 — one-time enablement for Nexus's bubblewrap sandbox (sandbox_exec tool).
#
# Ubuntu 24.04 blocks unprivileged user namespaces via AppArmor
# (kernel.apparmor_restrict_unprivileged_userns=1), which stops bwrap. This
# installs a small AppArmor profile that lets ONLY /usr/bin/bwrap use
# namespaces — the targeted, recommended fix (keeps the restriction on for
# everything else). Run once; survives reboot.
set -euo pipefail

sudo tee /etc/apparmor.d/bwrap >/dev/null <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>
profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
PROFILE

sudo apparmor_parser -r /etc/apparmor.d/bwrap
echo "✅ bwrap AppArmor profile installed. Nexus sandbox_exec is now active."
echo "   Verify:  bwrap --ro-bind / / --tmpfs /tmp --dev /dev --die-with-parent true && echo OK"

# Alternative (broader, less targeted) if the profile approach is unwanted:
#   sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
#   echo 'kernel.apparmor_restrict_unprivileged_userns=0' | sudo tee /etc/sysctl.d/60-userns.conf

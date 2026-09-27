#!/usr/bin/env bash
# 2026-09-27 rollout — run with: sudo bash ~/AI_Agent/scripts/sudo_training_rollout.sh
#
# 1. Ollama: MAX_LOADED_MODELS 1 → 2. With 1, every message swaps the router
#    in and evicts the 22 GB brain, which then reloads to write the reply:
#    live_drive chat averaged 10.1 s (target ~2 s). Brain (22 GB) + router
#    (2.5 GB) is ~25 GB — nowhere near the Sep-13 swap problem (that was
#    KEEP_ALIVE=-1 pinning Ornith 1.0 + 1.5 + qwen3:4b at once).
# 2. Restart Nexus services so today's code is live: nexus-router, skills,
#    👍/👎 capture, rebuilt memory store, the 8 tool fixes.
set -euo pipefail
CONF=/etc/systemd/system/ollama.service.d/20-nexus-perf.conf
echo "[1/3] Ollama: MAX_LOADED_MODELS=2"
sed -i 's/^Environment="OLLAMA_MAX_LOADED_MODELS=1"/Environment="OLLAMA_MAX_LOADED_MODELS=2"/' "$CONF"
sed -i 's/^# Was 4. One local model at a time.*/# brain + trained router (nexus-router) resident together — 2026-09-27/' "$CONF"
grep MAX_LOADED "$CONF"
systemctl daemon-reload
echo "[2/3] restart ollama"
systemctl restart ollama
sleep 5
echo "[3/3] restart Nexus services + prewarm"
systemctl restart nexus-telegram nexus-task-worker nexus-agent nexus-api \
    nexus-file-watcher nexus-git-watcher   # drop stale handles to the rebuilt memory store
systemctl start nexus-prewarm || true
echo "done. verify: ollama ps  (after one message: brain + nexus-router both listed)"

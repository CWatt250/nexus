#!/usr/bin/env bash
# Phase 0 — run with: sudo bash ~/AI_Agent/scripts/sudo_phase0.sh
set -euo pipefail
echo "[1/3] Ollama drop-in: one default ctx, pinned models, 2 parallel slots"
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/20-nexus-perf.conf <<'CONF'
[Service]
# Safety cap when a caller forgets num_ctx (unset = GGUF max 262144 → 37 GB KV)
Environment="OLLAMA_CONTEXT_LENGTH=32768"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_MAX_LOADED_MODELS=4"
# 2 slots: the router/quick-chat never queues behind a running build
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_FLASH_ATTENTION=1"
CONF
systemctl daemon-reload
echo "[2/3] restart ollama (models reload, ~30s)"
systemctl restart ollama
sleep 5
echo "[3/3] restart Nexus services + prewarm"
systemctl restart nexus-telegram nexus-task-worker nexus-agent nexus-api nexus-cc-dispatcher
systemctl start nexus-prewarm
echo "done. verify with: ollama ps"

#!/usr/bin/env bash
# switch-brain.sh — swap the Nexus brain model in one command.
#
#   ./switch-brain.sh                 # show current brain + available models
#   ./switch-brain.sh <model-name>    # switch brain to <model-name> and restart
#
# Only edits models.json (the live config — router.py reads it at runtime).
# Validates the model is pulled in Ollama before touching anything.
set -euo pipefail
cd "$(dirname "$0")"

PY=venv/bin/python
MODELS_JSON=models.json
BRAIN_SERVICES=(nexus-agent nexus-api nexus-cc-dispatcher nexus-sparky-brain nexus-telegram)

current() { "$PY" -c "import json;print(json.load(open('$MODELS_JSON'))['brain'])"; }

if [[ $# -eq 0 ]]; then
  echo "Current brain: $(current)"
  echo
  echo "Available models (ollama list):"
  ollama list
  echo
  echo "Usage: ./switch-brain.sh <model-name>"
  exit 0
fi

NEW_MODEL="$1"

# 1. Validate the model is actually pulled.
if ! ollama list | awk '{print $1}' | grep -qxF "$NEW_MODEL"; then
  echo "✗ '$NEW_MODEL' is not in 'ollama list'. Pull it first:"
  echo "    ollama pull $NEW_MODEL"
  exit 1
fi

# 2. Rewrite the brain-owned keys in models.json (leave fast/mid alone).
"$PY" - "$NEW_MODEL" <<'PYEOF'
import json, sys
m = sys.argv[1]
path = "models.json"
cfg = json.load(open(path))
for k in ("brain", "router", "heavy", "code", "design"):
    cfg[k] = m
json.dump(cfg, open(path, "w"), indent=2)
open(path, "a").write("\n")
print(f"✓ models.json updated → {m}")
PYEOF

# 3. Drop the old model from VRAM so the new one loads clean.
echo "Restarting brain services (needs sudo)..."
sudo systemctl restart "${BRAIN_SERVICES[@]}"
systemctl is-active "${BRAIN_SERVICES[@]}"

echo
echo "✓ Brain switched to: $(current)"
echo "  Send a test message. If you see 'exceed_context_size_error', the new"
echo "  model's tokenizer needs more room — bump num_ctx in"
echo "  workers/conversation_handler.py (and context_window_tokens in"
echo "  config/cost_limits.yaml) to match, then restart again."

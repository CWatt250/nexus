#!/bin/bash
# Start Sparky overlay

cd /home/cwatt250/AI_Agent/sparky/overlay

# Start state bridge in background
/home/cwatt250/AI_Agent/venv/bin/python /home/cwatt250/AI_Agent/sparky/state_bridge.py &
STATE_PID=$!

# Wait a moment for state bridge to start
sleep 1

# Start Electron overlay
npx electron .

# Cleanup state bridge when overlay closes
kill $STATE_PID 2>/dev/null

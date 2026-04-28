#!/bin/bash
# Start Sparky overlay, stop when done
echo "Starting Sparky..."
sudo systemctl start sparky
sleep 2
echo ""
echo "Sparky is running. Look for him on your desktop."
echo ""
read -p "Press Enter to STOP Sparky when you are done..."
echo "Stopping Sparky..."
sudo systemctl stop sparky
pkill -f sparky_brain.py 2>/dev/null
pkill -f state_bridge.py 2>/dev/null
echo "Done."

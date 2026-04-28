#!/bin/bash
# Start Chronicle screenshot/OCR loop, stop when done
echo "Starting Nexus Chronicle..."
sudo systemctl start nexus-chronicle
echo ""
echo "Chronicle is running. Capturing screen context to RAG."
echo ""
read -p "Press Enter to STOP Chronicle when you are done..."
echo "Stopping Chronicle..."
sudo systemctl stop nexus-chronicle
echo "Done."

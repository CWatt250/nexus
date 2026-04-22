#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Test computer use tool imports."""
import sys
sys.path.insert(0, "/home/cwatt250/AI_Agent")

from tools.computer_use_tool import COMPUTER_USE_TOOLS

print("Computer use tool loaded successfully")
print(f"Tools: {[t.name for t in COMPUTER_USE_TOOLS]}")

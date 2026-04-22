#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Test nexus.py imports."""
import sys
sys.path.insert(0, "/home/cwatt250/AI_Agent")

# Import the TOOLS list from nexus
from nexus import TOOLS

print(f"Nexus loaded with {len(TOOLS)} tools")
for t in TOOLS:
    print(f"  - {t.name}")

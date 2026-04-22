#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Test Telegram tool imports."""
import sys
sys.path.insert(0, "/home/cwatt250/AI_Agent")

from tools.telegram_tool import telegram_notify, telegram_send_file, TELEGRAM_TOOLS

print("Telegram tool loaded successfully")
print(f"TELEGRAM_TOOLS: {[t.name for t in TELEGRAM_TOOLS]}")

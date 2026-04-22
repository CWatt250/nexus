# Telegram Bot Setup for Nexus

This guide walks you through setting up Telegram notifications and remote commands for Nexus.

## Step 1: Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send the command `/newbot`
3. Follow the prompts:
   - Enter a name for your bot (e.g., "Nexus Bot")
   - Enter a username for your bot (must end in `bot`, e.g., "nexus_wattbott_bot")
4. BotFather will give you a **bot token** like: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`
5. Copy this token

## Step 2: Get Your Chat ID

1. Open Telegram and search for **@userinfobot**
2. Send any message to it
3. It will reply with your user info including your **chat ID** (a number like `123456789`)
4. Copy this number

## Step 3: Configure Nexus

Add the credentials to your `.env` file:

```bash
# Edit the .env file
nano ~/AI_Agent/.env
```

Add these lines:
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

## Step 4: Start the Telegram Listener

### Manual start (for testing):
```bash
~/AI_Agent/venv/bin/python ~/AI_Agent/tools/telegram_listener.py
```

### As a systemd service:
```bash
# Copy the service file
sudo cp /tmp/nexus-telegram.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable and start
sudo systemctl enable nexus-telegram
sudo systemctl start nexus-telegram

# Check status
sudo systemctl status nexus-telegram
```

## Step 5: Test the Bot

1. Open Telegram and find your bot (search for its username)
2. Send `/start` to see available commands
3. Send any message to route it to Nexus
4. Try `/status` to check Nexus health

## Available Commands

| Command | Description |
|---------|-------------|
| `/start` | Show welcome message and available commands |
| `/status` | Check Nexus API health |
| `/tasks` | List current active tasks |
| `/stop` | Stop current task (not yet implemented) |

Any other text message will be routed to Nexus for processing.

## Troubleshooting

### Bot not responding?
1. Check if the service is running: `sudo systemctl status nexus-telegram`
2. Check logs: `sudo journalctl -u nexus-telegram -f`
3. Verify your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are correct

### Can't reach Nexus?
1. Make sure Nexus API is running on port 11435
2. Check: `curl http://localhost:11435/health`

### Messages being ignored?
- The bot only responds to messages from the configured TELEGRAM_CHAT_ID
- Make sure you're messaging from the correct Telegram account

## Security Notes

- The bot only responds to messages from the authorized chat ID
- Never share your bot token publicly
- The `.env` file is gitignored to prevent accidental commits

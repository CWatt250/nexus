# Claude Code Routines Setup

Claude Code routines allow you to schedule automated tasks that run on a cron schedule.

## Setting Up Routines

### Using the /schedule command

In Claude Code, use the `/schedule` command to create recurring tasks:

```
/schedule create "daily-health" "0 6 * * *" "Check Nexus health: run all tests, verify services are running, report status to Telegram"
```

### Recommended Routines

#### Daily Health Check (6 AM)
```
/schedule create "daily-health" "0 6 * * *" "Run Nexus health check:
1. Check all systemd services are running
2. Run tool import test
3. Verify Ollama is responsive
4. Check disk space
5. Report summary to Telegram"
```

#### Weekly Pattern Digest (Monday 6 AM)
```
/schedule create "weekly-digest" "0 6 * * 1" "Generate weekly pattern digest:
1. Run memory/patterns.py analysis
2. Generate insights from the week
3. Update memory/weekly-digest.md
4. Send summary to Telegram"
```

#### Nightly Git Sync (Midnight)
```
/schedule create "git-sync" "0 0 * * *" "Git sync and backup:
1. Stage changes in projects/ and memory/
2. Commit with auto-generated message
3. Push to remote if configured"
```

#### Service Monitor (Every 30 min)
```
/schedule create "service-monitor" "*/30 * * * *" "Check service health:
1. Verify nexus-api is responding
2. Check nexus-telegram if configured
3. Check Ollama model availability
4. Alert on any issues"
```

## GitHub Webhooks Integration

You can trigger Nexus routines from GitHub events:

### Setup

1. Create a webhook endpoint:
```python
# Add to nexus_api.py
@app.post("/webhook/github")
async def github_webhook(request: Request):
    payload = await request.json()
    event = request.headers.get("X-GitHub-Event")
    
    if event == "push":
        # Handle push event
        pass
    elif event == "pull_request":
        # Handle PR event
        pass
    
    return {"received": True}
```

2. Configure in GitHub:
   - Go to repo Settings > Webhooks
   - Add webhook URL: `https://your-tailscale-ip:11435/webhook/github`
   - Select events: Push, Pull Request, Issues

### Automatic PR Review

When a new PR is opened:
```
/schedule create "pr-review" "@webhook:pull_request" "Review PR:
1. Read the diff
2. Check for issues
3. Comment with feedback
4. Notify via Telegram"
```

## Managing Routines

```bash
# List all scheduled routines
/schedule list

# View routine details
/schedule show daily-health

# Delete a routine
/schedule delete daily-health

# Run immediately (test)
/schedule run daily-health

# Pause a routine
/schedule pause daily-health

# Resume a routine
/schedule resume daily-health
```

## Viewing Logs

Routine logs are stored in `~/.claude/routines/logs/`:

```bash
# View recent runs
ls -la ~/.claude/routines/logs/

# View specific routine log
cat ~/.claude/routines/logs/daily-health-latest.log
```

## Troubleshooting

### Routine not running?
1. Check if Claude Code is running
2. Verify cron syntax
3. Check routine status: `/schedule show <name>`

### Not receiving notifications?
1. Verify TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
2. Test manually: `/schedule run <name>`

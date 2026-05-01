# Runbook

## Starting Services
```bash
sudo systemctl start nexus-api nexus-agent nexus-telegram nexus-task-worker
```

## Checking Status
```bash
sudo systemctl status nexus-api nexus-agent nexus-telegram nexus-task-worker
```

## Restarting All
```bash
nexus_restart_services
```

## Logs
```bash
journalctl -u nexus-api -f
journalctl -u nexus-agent -f
journalctl -u nexus-telegram -f
journalctl -u nexus-task-worker -f
```

## API Health
```bash
curl http://localhost:11435/healthz
```

## Dashboard
Open http://localhost:11438 in browser.

## Memory
```bash
# Check stats
memory_stats

# Search
memory_search "query"

# Add
memory_add "text to remember"
```

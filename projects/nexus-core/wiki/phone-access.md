# Phone Access via Open WebUI
_Last updated: 2026-05-01_

## Goal
Expose Nexus API (port 11435) so it's reachable from devices on the home network
(e.g., phone via Open WebUI or any HTTP client).

## Current State
- Nexus API binds to `127.0.0.1:11435` (localhost only).
- Dashboard binds to `127.0.0.1:11438`.
- No authentication on the API.

## Options

### Option A: Bind to all interfaces (simplest)
Change the API to listen on `0.0.0.0:11435` instead of `127.0.0.1`.

**Files to edit:**
- `nexus_api.py` — find the `uvicorn.run()` call, change `host="127.0.0.1"` → `host="0.0.0.0"`.

**Pros:** Quick, no extra infrastructure.
**Cons:** No auth — anyone on the network can call the API. Firewall must be configured.

### Option B: API key auth (recommended for phone access)
Add an X-API-Key header check to the API.

**Steps:**
1. Add `NEXUS_API_KEY` to `~/.env` (generate with `openssl rand -hex 32`).
2. In `nexus_api.py`, add a middleware that checks `X-API-Key` header against the env var.
3. In Open WebUI, set the API URL to `http://<wattbott-ip>:11435` and API key to the value from .env.
4. Ensure firewall allows port 11435 from home network subnet.

**Pros:** Basic auth protection.
**Cons:** Need to configure Open WebUI on each device.

### Option C: SSH tunnel (most secure, zero config)
From phone/laptop: `ssh -L 11435:localhost:11435 cwatt250@wattbott`

**Pros:** No changes to nexus, encrypted, firewall-friendly.
**Cons:** Manual setup, SSH must be running.

## Firewall Config
```bash
# Allow only from home network (192.168.x.x), block everything else
sudo ufw allow from 192.168.0.0/16 to any port 11435 proto tcp
sudo ufw allow from 10.0.0.0/8 to any port 11435 proto tcp
# Or the specific phone IP:
# sudo ufw allow from 192.168.1.X to any port 11435 proto tcp
```

## Open WebUI Setup
1. Install Open WebUI on phone or laptop on the same network.
2. In settings, set:
   - **Ollama URL:** `http://<wattbott-ip>:11434`
   - **Custom API URL:** `http://<wattbott-ip>:11435` (for Nexus API)
   - **API Key:** (if using Option B)
3. Test with `curl http://<wattbott-ip>:11435/healthz` from phone.

## WattBott's IP
```bash
hostname -I
# Typically: 192.168.1.XX
```

## Security Notes
- Never expose port 11435 to the internet without auth + firewall.
- Use Option C (SSH tunnel) if you need external access.
- Consider rate-limiting if opening to the network.

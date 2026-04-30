# SearXNG — Self-Hosted Search

Replaces Brave-only web search with a free, unlimited, locally-hosted SearXNG container that runs on `http://127.0.0.1:8888`. The Nexus tool layer (`web_search`, `searxng_search`, `searxng_search_news`, `searxng_health`) talks to it over plain HTTP.

## Why

- **No API key, no rate limits.** Brave's free tier is fine until you start running daily research jobs; SearXNG aggregates Google + Bing + DuckDuckGo + Wikipedia + GitHub + Stack Overflow + Reddit + YouTube + News for free.
- **Pluggable.** The `web_search()` router picks the best backend in priority order: **Tavily → Brave → SearXNG**. Drop a `TAVILY_API_KEY` or `BRAVE_SEARCH_API_KEY` into `~/AI_Agent/config/secrets.yaml` later and the router upgrades automatically — zero code change.
- **Loopback only.** The container binds to `127.0.0.1:8888`; nothing leaks to the LAN.

## One-time install

Docker isn't installed yet on this box. Run the bring-up script:

```bash
bash ~/AI_Agent/SUDO_DEPENDENCIES_R5.sh
```

That script:

1. Installs Docker Engine + the compose plugin from Docker's official repo (the Ubuntu-shipped `docker.io` is too old).
2. Adds `cwatt250` to the `docker` group (log out + back in to take effect).
3. Pulls and starts the SearXNG container via `~/AI_Agent/searxng/docker-compose.yml`.
4. Installs `nexus-searxng.service` so `systemctl status nexus-searxng` works for at-a-glance health.
5. Smokes `http://localhost:8888/search?q=hello&format=json`.

## File layout

```
~/AI_Agent/searxng/
├── config/
│   └── settings.yml            ← bind-mounted to /etc/searxng/settings.yml
├── docker-compose.yml          ← service definition
└── nexus-searxng.service       ← optional systemd wrapper
```

## settings.yml — important bits

- `formats: [html, json]` — JSON is **required** for `searxng_search()`. Without it the API returns 403.
- `secret_key` — random 256-bit hex. Regenerate with `openssl rand -hex 32` and update if it ever leaks.
- `engines:` — curated list (google / bing / duckduckgo / github / stackoverflow / wikipedia / reddit / youtube + four news engines). Add/remove engines here, then `docker compose restart`.

## Day-to-day operations

```bash
# Status
systemctl status nexus-searxng       # systemd-level
docker ps --filter name=nexus-searxng  # container-level

# Logs
docker logs -f nexus-searxng

# Restart after editing settings.yml
cd ~/AI_Agent/searxng && docker compose restart

# Stop / start (no sudo once you're in the docker group)
cd ~/AI_Agent/searxng && docker compose down
cd ~/AI_Agent/searxng && docker compose up -d

# Update to the latest SearXNG image
cd ~/AI_Agent/searxng && docker compose pull && docker compose up -d
```

## Smoke checks

From any shell:

```bash
curl -fsS 'http://127.0.0.1:8888/search?q=python&format=json' | jq '.results[0].title'
```

From Nexus:

```python
from tools.searxng_tool import searxng_health, searxng_search
print(searxng_health.invoke({}))                           # 'ok'
print(searxng_search.invoke({"query": "weather Pasco WA"}))
```

From the agent (router-level):

```python
from tools.search_router import web_search
print(web_search.invoke({"query": "nori l1 robot price"}))  # picks searxng
```

## Adding Tavily later (optional upgrade)

When you're ready to bolt on Tavily:

1. Add `TAVILY_API_KEY: <key>` to `~/AI_Agent/config/secrets.yaml`.
2. Wire a real client in `tools/search_router.py::_call_tavily`.
3. The router picks Tavily first automatically; SearXNG drops to fallback.

No other code changes needed — the `web_search()` tool is the stable surface.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `searxng_health()` → `down: container not reachable` | Container isn't running. | `cd ~/AI_Agent/searxng && docker compose up -d` |
| `searxng_search` → `ERROR: ... 403 — JSON format probably not enabled` | `settings.yml` missing `json` in `formats`. | Add it, then `docker compose restart`. |
| Search results all empty across engines | Engines flaky / rate-limited externally. | Wait a minute, or look at `docker logs nexus-searxng` for which engines errored. |
| Permission denied talking to docker | User not in `docker` group yet. | Log out, log back in. Or `newgrp docker`. |

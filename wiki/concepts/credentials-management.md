---
title: Credentials Management
description: How Nexus stores, validates, and uses service API tokens
created: 2026-05-07
phase: 33
---

# Credentials Management

## Problem it solves

Autonomous workflows (deploy BidWatt → Vercel, seed Supabase, send emails via Resend) 
require pre-provisioned tokens. Without them, Nexus stalls and Colton has to act as hands.
Phase 33 builds a one-time bootstrap helper so this only happens once per service.

## Files

| File | Purpose |
|------|---------|
| `tools/credentials_helper.py` | CLI entry point — status, interactive flow, Telegram helpers |
| `core/credentials_registry.py` | Registry of all services with validation definitions |
| `tools/credentials_registry.py` | Re-export of the registry for tools/ consumers |
| `config/secrets.yaml` | The actual secrets store (gitignored, chmod 600) |
| `config/secrets.yaml.bak` | Auto-backup created before every write |

## CLI usage

```bash
# Show status of all services
python tools/credentials_helper.py --status
python tools/credentials_helper.py          # same, no args = status

# Add a credential interactively
python tools/credentials_helper.py vercel
python tools/credentials_helper.py github

# Get Telegram-formatted instructions for a service
python tools/credentials_helper.py --telegram vercel

# Force-overwrite an existing credential
python tools/credentials_helper.py vercel --force
```

## Service tiers

| Tier | Meaning | Examples |
|------|---------|---------|
| 1 | Needed soon — active deploys depend on these | vercel, supabase, stripe, github, cloudflare, resend |
| 2 | Likely needed — secondary workflows | aws_iam, sentry, anthropic_admin, twilio |
| 3 | Situational — used occasionally | discord_webhook, slack_webhook, tailscale, linear |
| 4 | Specialized stubs | youtube_data, amazon_seller, docusign, quickbooks |

## Validation methods

Each service registration includes a `validation` method:

- **HTTP_GET** — curl GET with Bearer/Basic auth, check expected status code
- **HTTP_POST** — curl POST with JSON body, check response code
- **CLI_EXEC** — shell command with token substituted, check exit code

The validation is a real API call — not a regex check on the token format.

## secrets.yaml format

Plain `key: value` lines. No YAML nesting. The loader (`core/secrets.py`) handles
`KEY: value`, `KEY:value` (no space), and `KEY=value` (env-style) so editing by hand is forgiving.

```yaml
GITHUB_PAT: ghp_...
ANTHROPIC_API_KEY: sk-ant-...
VERCEL_TOKEN: vercel_...
SUPABASE_ACCESS_TOKEN: sbp_...
STRIPE_SECRET_KEY: sk_live_...
```

## Security guarantees

1. `secrets.yaml` is always `chmod 600` — only the current user can read it
2. Before every write, a `secrets.yaml.bak` backup is created (also `chmod 600`)
3. If the write corrupts the file, the backup is restored automatically
4. Tokens are never logged in full — only first 4 + last 4 chars in output
5. `core/secrets.redact()` masks known token values in any log text

## Telegram /creds command

The Telegram bot supports `/creds` for on-the-go credential management:

- `/creds` → posts the status table to Telegram
- `/creds vercel` → posts setup instructions with a clear security warning

⚠️ Tokens sent via Telegram remain in chat history. The helper warns about this
and recommends using the terminal helper for higher security. Always delete the
token message after validation.

## Adding a new service

Add a `ServiceDef` entry to `core/credentials_registry.py`:

```python
"myservice": ServiceDef(
    name="myservice",
    display_name="My Service",
    tier=2, order=9,
    secret_key="MYSERVICE_API_KEY",
    description="What this service does.",
    instructions=(
        "1. Go to https://myservice.example.com/settings/api\n"
        "2. Click 'Create API Key'\n"
        "3. Copy the key"
    ),
    validation=ValidationMethod.HTTP_GET,
    http_url="https://api.myservice.example.com/v1/me",
    http_auth_header="Authorization: Bearer {token}",
    http_expected_status=200,
    http_error_patterns=["unauthorized", "401"],
),
```

The helper automatically picks it up — no changes needed to `credentials_helper.py`.

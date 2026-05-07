"""
credentials_registry.py — Service credential registry for Nexus.

Defines every supported service with its validation method, display info,
and schema. Tier 1 = needed soon, Tier 2 = likely, Tier 3 = situational,
Tier 4 = specialized stubs.

Usage:
    from core.credentials_registry import registry
    service = registry["vercel"]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ValidationMethod(Enum):
    """How to prove a token is live."""
    HTTP_GET = "http_get"       # GET /something, expect expected_status
    HTTP_POST = "http_post"     # POST to token endpoint
    CLI_EXEC = "cli_exec"       # shell command, expect rc==0


@dataclass
class ServiceDef:
    """Schema for one registered service."""
    name: str
    display_name: str
    tier: int = 1                    # 1–4
    order: int = 0
    description: str = ""
    instructions: str = ""
    secret_key: str = ""             # key name in secrets.yaml; defaults to NAME.upper()
    validation: ValidationMethod = ValidationMethod.HTTP_GET

    # HTTP_GET / HTTP_POST shared
    http_url: str = ""
    http_auth_header: str = "Authorization: Bearer {token}"
    http_auth_type: str = "bearer"   # "bearer" | "basic" (base64 key: for Stripe)
    http_expected_status: int = 200
    http_error_patterns: list[str] = field(
        default_factory=lambda: ["unauthorized", "401", "forbidden", "403"]
    )

    # HTTP_POST only
    post_url: str = ""
    post_body: dict[str, Any] = field(default_factory=dict)
    post_auth_header: str = "Authorization: Bearer {token}"

    # CLI_EXEC only
    cli_command: str = ""
    cli_expected_rc: int = 0

    @property
    def effective_key(self) -> str:
        """The secrets.yaml key for this service."""
        return self.secret_key or self.name.upper()


def _build_registry() -> dict[str, ServiceDef]:

    return {

        # ======================================================================
        # TIER 1 — needed soon; BidWatt / GreenOps deploys depend on these
        # ======================================================================

        "vercel": ServiceDef(
            name="vercel",
            display_name="Vercel",
            tier=1, order=1,
            secret_key="VERCEL_TOKEN",
            description="Deploy Next.js + frontend apps to Vercel edge network.",
            instructions=(
                "1. Go to https://vercel.com/account/tokens\n"
                "2. Click 'Create' — name it 'Nexus', scope 'Full Account', expiry 1 year\n"
                "3. Copy the token (starts with vercel_...)\n"
                "⚠️ Scope note: 'Full Account' grants project create/delete. "
                "Use a read-only token if you only need Nexus to read deployments."
            ),
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.vercel.com/v2/user",
            http_auth_header="Authorization: Bearer {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401", "forbidden", "403"],
        ),

        "supabase": ServiceDef(
            name="supabase",
            display_name="Supabase",
            tier=1, order=2,
            secret_key="SUPABASE_ACCESS_TOKEN",
            description="Postgres DB + Auth + Storage for BidWatt and other apps.",
            instructions=(
                "1. Go to https://supabase.com/dashboard/account/tokens\n"
                "2. Click 'Generate new token' — name it 'Nexus'\n"
                "3. Copy the token (starts with sbp_...)\n"
                "Note: This is a Personal Access Token for the Management API.\n"
                "Project-specific service-role keys go in secrets.yaml as SUPABASE_SERVICE_KEY_<project>."
            ),
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.supabase.com/v1/projects",
            http_auth_header="Authorization: Bearer {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401", "forbidden", "403", "invalid token"],
        ),

        "stripe": ServiceDef(
            name="stripe",
            display_name="Stripe",
            tier=1, order=3,
            secret_key="STRIPE_SECRET_KEY",
            description="Payment processing for SaaS apps.",
            instructions=(
                "1. Go to https://dashboard.stripe.com/apikeys\n"
                "2. Under 'Standard keys', copy the Secret key (starts with sk_live_ or sk_test_)\n"
                "3. Use sk_test_ for dev/staging, sk_live_ for production\n"
                "⚠️ Scope note: secret key has full account access. "
                "Consider a Restricted Key scoped to only the resources Nexus needs."
            ),
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.stripe.com/v1/balance",
            http_auth_type="basic",  # Stripe uses Basic auth: base64(key:)
            http_expected_status=200,
            http_error_patterns=["no such api_key", "invalid api key", "401", "authentication"],
        ),

        "github": ServiceDef(
            name="github",
            display_name="GitHub",
            tier=1, order=4,
            secret_key="GITHUB_PAT",
            description="Git hosting, PRs, issues, CI, and package registry.",
            instructions=(
                "1. Go to https://github.com/settings/tokens?type=beta (fine-grained) "
                "OR https://github.com/settings/tokens (classic)\n"
                "2. Generate new token — name it 'Nexus-WattBott'\n"
                "3. Scopes needed: repo (full control), workflow, read:org\n"
                "Note (scope upgrade): if you have an older PAT with only 'repo' scope, "
                "generate a new one that also includes 'workflow' so Nexus can trigger Actions."
            ),
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.github.com/user",
            http_auth_header="Authorization: token {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401", "Bad credentials"],
        ),

        "cloudflare": ServiceDef(
            name="cloudflare",
            display_name="Cloudflare",
            tier=1, order=5,
            secret_key="CLOUDFLARE_API_TOKEN",
            description="DNS management, CDN, and Workers for domain routing.",
            instructions=(
                "1. Go to https://dash.cloudflare.com/profile/api-tokens\n"
                "2. Click 'Create Token'\n"
                "3. Use template 'Edit zone DNS' or 'Custom token' — "
                "add permissions: Zone:DNS:Edit, Zone:Zone:Read\n"
                "4. Copy the token (starts with a random string)\n"
                "⚠️ Scope note: choose a zone-scoped token, not a Global API Key."
            ),
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.cloudflare.com/client/v4/user/tokens/verify",
            http_auth_header="Authorization: Bearer {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401", "invalid token"],
        ),

        "resend": ServiceDef(
            name="resend",
            display_name="Resend",
            tier=1, order=6,
            secret_key="RESEND_API_KEY",
            description="Transactional email API (welcome emails, notifications).",
            instructions=(
                "1. Go to https://resend.com/api-keys\n"
                "2. Click 'Create API Key' — name it 'Nexus'\n"
                "3. Choose 'Full access' or scope to specific domains\n"
                "4. Copy the key (starts with re_...)"
            ),
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.resend.com/domains",
            http_auth_header="Authorization: Bearer {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401", "invalid_api_key"],
        ),

        # ======================================================================
        # TIER 1 continued — Nexus AI stack credentials
        # ======================================================================

        "deepseek": ServiceDef(
            name="deepseek",
            display_name="DeepSeek",
            tier=1, order=7,
            secret_key="Z_AI_API_KEY",
            description="AI API for coding tasks (Flash/Pro tiers) via Nexus dispatcher.",
            instructions=(
                "1. Go to https://platform.deepseek.com/api_keys\n"
                "2. Click 'Create new API key' — name it 'Nexus'\n"
                "3. Copy the key (starts with sk-...)"
            ),
            validation=ValidationMethod.HTTP_POST,
            post_url="https://api.deepseek.com/chat/completions",
            post_body={"model": "deepseek-chat", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 5},
            post_auth_header="Authorization: Bearer {token}",
        ),

        "anthropic": ServiceDef(
            name="anthropic",
            display_name="Anthropic",
            tier=1, order=8,
            secret_key="ANTHROPIC_API_KEY",
            description="Claude Sonnet/Opus/Haiku via API key (fallback when Max plan hits limits).",
            instructions=(
                "1. Go to https://console.anthropic.com/settings/keys\n"
                "2. Click 'Create Key' — name it 'Nexus-API'\n"
                "3. Copy the key (starts with sk-ant-...)"
            ),
            validation=ValidationMethod.HTTP_POST,
            post_url="https://api.anthropic.com/v1/messages",
            post_body={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "Hi"}],
            },
            post_auth_header="x-api-key: {token}",
        ),

        # ======================================================================
        # TIER 2 — likely needed; secondary workflows depend on these
        # ======================================================================

        "aws_iam": ServiceDef(
            name="aws_iam",
            display_name="AWS IAM",
            tier=2, order=1,
            secret_key="AWS_ACCESS_KEY_ID",
            description="AWS storage, Lambda, S3 for media/document storage.",
            instructions=(
                "1. Go to https://console.aws.amazon.com/iam/home#/security_credentials\n"
                "2. Under 'Access keys', click 'Create access key'\n"
                "3. Select 'Application running outside AWS'\n"
                "4. Copy the Access Key ID and Secret Access Key\n"
                "5. Also save AWS_SECRET_ACCESS_KEY to secrets.yaml\n"
                "⚠️ Scope note: create an IAM user with only the S3/Lambda permissions needed, "
                "not root account credentials."
            ),
            validation=ValidationMethod.CLI_EXEC,
            cli_command="aws sts get-caller-identity --query Account --output text",
            cli_expected_rc=0,
        ),

        "sentry": ServiceDef(
            name="sentry",
            display_name="Sentry",
            tier=2, order=2,
            secret_key="SENTRY_AUTH_TOKEN",
            description="Error tracking and performance monitoring for BidWatt.",
            instructions=(
                "1. Go to https://sentry.io/settings/account/api/auth-tokens/\n"
                "2. Click 'Create New Token' — name it 'Nexus'\n"
                "3. Scopes needed: project:read, org:read, event:read\n"
                "4. Copy the token"
            ),
            validation=ValidationMethod.HTTP_GET,
            http_url="https://sentry.io/api/0/user/",
            http_auth_header="Authorization: Bearer {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401", "forbidden"],
        ),

        "plausible": ServiceDef(
            name="plausible",
            display_name="Plausible",
            tier=2, order=3,
            secret_key="PLAUSIBLE_API_KEY",
            description="Privacy-first web analytics for BidWatt and SubWatt.",
            instructions=(
                "1. Go to https://plausible.io/settings (or your self-hosted instance)\n"
                "2. Under 'API Keys', click 'New API Key' — name it 'Nexus'\n"
                "3. Copy the key"
            ),
            validation=ValidationMethod.HTTP_GET,
            http_url="https://plausible.io/api/v1/sites",
            http_auth_header="Authorization: Bearer {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401", "forbidden"],
        ),

        "twilio": ServiceDef(
            name="twilio",
            display_name="Twilio",
            tier=2, order=4,
            secret_key="TWILIO_AUTH_TOKEN",
            description="SMS/voice notifications for project alerts.",
            instructions=(
                "1. Go to https://console.twilio.com/us1/account/keys-credentials/api-keys\n"
                "2. Click 'Create API key' — type 'Standard', name it 'Nexus'\n"
                "3. Copy the SID and Secret\n"
                "4. Also save TWILIO_ACCOUNT_SID to secrets.yaml (your main account SID)\n"
                "Note: validating the auth token alone requires your account SID too."
            ),
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.twilio.com/2010-04-01.json",
            http_auth_type="basic",
            http_expected_status=200,
            http_error_patterns=["not authorized", "401", "authenticate"],
        ),

        "anthropic_admin": ServiceDef(
            name="anthropic_admin",
            display_name="Anthropic Admin",
            tier=2, order=5,
            secret_key="ANTHROPIC_ADMIN_KEY",
            description="Anthropic admin API for billing usage and workspace management.",
            instructions=(
                "1. Go to https://console.anthropic.com/settings/admin-keys\n"
                "2. Click 'Create Admin Key' — name it 'Nexus-Admin'\n"
                "3. Copy the key (starts with sk-ant-admin...)\n"
                "⚠️ Admin keys have billing/usage access. Treat with extra care."
            ),
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.anthropic.com/v1/organizations",
            http_auth_header="x-api-key: {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401", "forbidden"],
        ),

        "brave": ServiceDef(
            name="brave",
            display_name="Brave Search",
            tier=2, order=6,
            secret_key="BRAVE_SEARCH_API_KEY",
            description="Web search API for real-time results in Nexus research tasks.",
            instructions=(
                "1. Go to https://brave.com/search/api/\n"
                "2. Sign up / log in and create an API key\n"
                "3. Copy the key"
            ),
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.search.brave.com/res/v1/web/search?q=test",
            http_auth_header="X-Subscription-Token: {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401", "invalid subscription token"],
        ),

        "tavily": ServiceDef(
            name="tavily",
            display_name="Tavily",
            tier=2, order=7,
            secret_key="TAVILY_API_KEY",
            description="Search API used as web_search backend in LangGraph tools.",
            instructions=(
                "1. Go to https://app.tavily.com/home\n"
                "2. Sign up / log in and copy your API key from the dashboard\n"
                "3. Key starts with tvly-..."
            ),
            validation=ValidationMethod.HTTP_POST,
            post_url="https://api.tavily.com/search",
            post_body={"api_key": "{token}", "query": "test", "max_results": 1},
        ),

        "openai": ServiceDef(
            name="openai",
            display_name="OpenAI",
            tier=2, order=8,
            secret_key="OPENAI_API_KEY",
            description="GPT-4o, DALL-E, Whisper — fallback when local models aren't enough.",
            instructions=(
                "1. Go to https://platform.openai.com/api-keys\n"
                "2. Click 'Create new secret key' — name it 'Nexus'\n"
                "3. Copy the key (starts with sk-...)"
            ),
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.openai.com/v1/models",
            http_auth_header="Authorization: Bearer {token}",
            http_expected_status=200,
            http_error_patterns=["invalid_api_key", "unauthorized", "401"],
        ),

        # ======================================================================
        # TIER 3 — situational; stubs only
        # ======================================================================

        "discord_webhook": ServiceDef(
            name="discord_webhook",
            display_name="Discord Webhook",
            tier=3, order=1,
            secret_key="DISCORD_WEBHOOK_URL",
            description="Post Nexus notifications to a Discord channel.",
            instructions="Discord Webhook — stub, full instructions TBD.",
            validation=ValidationMethod.HTTP_POST,
            post_url="{token}",
            post_body={"content": "Nexus ping — webhook test"},
        ),

        "slack_webhook": ServiceDef(
            name="slack_webhook",
            display_name="Slack Webhook",
            tier=3, order=2,
            secret_key="SLACK_WEBHOOK_URL",
            description="Post Nexus notifications to a Slack channel.",
            instructions="Slack Webhook — stub, full instructions TBD.",
            validation=ValidationMethod.HTTP_POST,
            post_url="{token}",
            post_body={"text": "Nexus ping — webhook test"},
        ),

        "mapbox_mgmt": ServiceDef(
            name="mapbox_mgmt",
            display_name="Mapbox",
            tier=3, order=3,
            secret_key="MAPBOX_ACCESS_TOKEN",
            description="Map tiles and geocoding for SubWatt/BidWatt location features.",
            instructions="Mapbox Management Token — stub, full instructions TBD.",
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.mapbox.com/tokens/v2",
            http_auth_header="Authorization: Bearer {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401"],
        ),

        "tailscale": ServiceDef(
            name="tailscale",
            display_name="Tailscale",
            tier=3, order=4,
            secret_key="TAILSCALE_API_KEY",
            description="Zero-config VPN for secure WattBott remote access.",
            instructions="Tailscale API Key — stub, full instructions TBD.",
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.tailscale.com/api/v2/tailnet/-/devices",
            http_auth_header="Authorization: Bearer {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401"],
        ),

        "linear": ServiceDef(
            name="linear",
            display_name="Linear",
            tier=3, order=5,
            secret_key="LINEAR_API_KEY",
            description="Issue tracking for Nexus project management.",
            instructions="Linear API Key — stub, full instructions TBD.",
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.linear.app/graphql",
            http_auth_header="Authorization: {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401"],
        ),

        "fal": ServiceDef(
            name="fal",
            display_name="Fal.ai",
            tier=3, order=6,
            secret_key="FAL_API_KEY",
            description="Image / audio / video generation API.",
            instructions="Fal.ai API Key — stub, full instructions TBD.",
            validation=ValidationMethod.HTTP_GET,
            http_url="https://fal.run/v1/models",
            http_auth_header="Authorization: Key {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401"],
        ),

        "elevenlabs": ServiceDef(
            name="elevenlabs",
            display_name="ElevenLabs",
            tier=3, order=7,
            secret_key="ELEVENLABS_API_KEY",
            description="Voice synthesis API for high-quality TTS.",
            instructions="ElevenLabs API Key — stub, full instructions TBD.",
            validation=ValidationMethod.HTTP_GET,
            http_url="https://api.elevenlabs.io/v1/voices",
            http_auth_header="xi-api-key: {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401"],
        ),

        # ======================================================================
        # TIER 4 — specialized; stubs only, full instructions deferred
        # ======================================================================

        "youtube_data": ServiceDef(
            name="youtube_data",
            display_name="YouTube Data API",
            tier=4, order=1,
            secret_key="YOUTUBE_API_KEY",
            description="YouTube Data API v3 for video metadata and transcripts.",
            instructions="YouTube Data API — stub, full instructions TBD.",
            validation=ValidationMethod.HTTP_GET,
            http_url="https://www.googleapis.com/youtube/v3/channels?part=id&mine=true",
            http_auth_header="Authorization: Bearer {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401"],
        ),

        "amazon_seller": ServiceDef(
            name="amazon_seller",
            display_name="Amazon Seller",
            tier=4, order=2,
            secret_key="AMAZON_SELLER_CLIENT_ID",
            description="Amazon Selling Partner API for product/order data.",
            instructions="Amazon Seller API — stub, full instructions TBD.",
            validation=ValidationMethod.HTTP_GET,
            http_url="https://sellingpartnerapi-na.amazon.com/sellers/v1/marketplaceParticipations",
            http_auth_header="x-amz-access-token: {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401"],
        ),

        "docusign": ServiceDef(
            name="docusign",
            display_name="DocuSign",
            tier=4, order=3,
            secret_key="DOCUSIGN_ACCESS_TOKEN",
            description="E-signature API for contract automation.",
            instructions="DocuSign Access Token — stub, full instructions TBD.",
            validation=ValidationMethod.HTTP_GET,
            http_url="https://account.docusign.com/oauth/userinfo",
            http_auth_header="Authorization: Bearer {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401"],
        ),

        "quickbooks": ServiceDef(
            name="quickbooks",
            display_name="QuickBooks",
            tier=4, order=4,
            secret_key="QUICKBOOKS_ACCESS_TOKEN",
            description="Accounting API for invoice and expense automation.",
            instructions="QuickBooks Access Token — stub, full instructions TBD.",
            validation=ValidationMethod.HTTP_GET,
            http_url="https://accounts.platform.intuit.com/v1/openid_connect/userinfo",
            http_auth_header="Authorization: Bearer {token}",
            http_expected_status=200,
            http_error_patterns=["unauthorized", "401"],
        ),
    }


registry: dict[str, ServiceDef] = _build_registry()

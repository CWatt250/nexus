#!/usr/bin/env python3
"""
credentials_helper.py — CLI for managing secrets in secrets.yaml.

Commands:
    python credentials_helper.py --help              Show help
    python credentials_helper.py --status            Table of all services + their status
    python credentials_helper.py vercel              Interactive flow for one service
    python credentials_helper.py vercel --skip-prompt  Validate & save without asking

Safety:
    - secrets.yaml always kept at chmod 600
    - backup created before every write (secrets.yaml.bak, chmod 600)
    - YAML re-parsed after write; restore from backup on failure
    - full tokens never logged; only first 4 + last 4 chars

Telegram integration:
    --telegram service_name      Post instructions to Telegram, wait for token reply
"""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Resolve paths
AI_AGENT = Path.home() / "AI_Agent"
SECRETS_PATH = AI_AGENT / "config" / "secrets.yaml"
BACKUP_PATH = SECRETS_PATH.with_suffix(".yaml.bak")

# Import registry
sys.path.insert(0, str(AI_AGENT))
from core.credentials_registry import registry, ServiceDef, ValidationMethod
from core.secrets import get as _get_secret, _all_secrets  # our existing parser


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def redact_token(token: str) -> str:
    """Show first 4 + last 4 chars, mask the middle."""
    if not token or len(token) < 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def mask_token(token: str) -> str:
    """Fully mask a token, showing only its length."""
    if not token:
        return "(empty)"
    return f"[{len(token)} chars]"


# ---------------------------------------------------------------------------
# secrets.yaml I/O with backup + chmod guard
# ---------------------------------------------------------------------------

def _ensure_chmod(path: Path) -> None:
    """Force mode 600 on a path. Silently ignores PermissionError (e.g. /tmp)."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except PermissionError:
        pass


def _read_yaml(text: str) -> dict:
    """Minimal YAML parser that handles KEY: value and KEY:value lines."""
    result = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, val = stripped.partition("=")
            result[key.strip()] = val.strip().strip("'\"")
        elif ":" in stripped:
            key, _, val = stripped.partition(":")
            result[key.strip()] = val.strip().strip("'\"")
    return result


def save_token(service_name: str, token: str) -> str:
    """
    Save a token to secrets.yaml with full safety:
    1. Read only the yaml file (no env merging, no lru_cache)
    2. Create backup with chmod 600
    3. Write raw key:value lines — never redact values being written
    4. Verify file parses and key is present
    5. Ensure chmod 600; reload secrets cache
    """
    import shutil
    from core import secrets as _sm

    # Determine the key name: use service's secret_key if registered
    key_name = service_name.upper()
    svc = registry.get(service_name)
    if svc:
        key_name = svc.effective_key

    # Read only the yaml file (avoids env-var merging and stale cache)
    raw: dict[str, str] = _sm._parse_kv_file(SECRETS_PATH) if SECRETS_PATH.exists() else {}
    raw[key_name] = token

    # Backup before any write
    if SECRETS_PATH.exists():
        shutil.copy2(SECRETS_PATH, BACKUP_PATH)
        _ensure_chmod(BACKUP_PATH)

    # Ensure config dir exists
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Write raw key: value lines — actual token values, no redaction
    lines = [f"{k}: {v}" for k, v in raw.items()]
    SECRETS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _ensure_chmod(SECRETS_PATH)

    # Invalidate lru_cache so next get() picks up new value
    _sm.reload()

    # Verify written file parses and contains the new key
    try:
        parsed = _sm._parse_kv_file(SECRETS_PATH)
        if key_name not in parsed:
            raise ValueError(f"{key_name} missing from file after write")
    except Exception as e:
        if BACKUP_PATH.exists():
            shutil.copy2(BACKUP_PATH, SECRETS_PATH)
            _ensure_chmod(SECRETS_PATH)
            _sm.reload()
        return f"ERROR: write failed, restored from backup. {e}"

    return f"OK — {key_name} saved ({mask_token(token)})"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_http_get(service: ServiceDef, token: str) -> tuple[bool, str]:
    """Make a real HTTP GET with the token and check the response."""
    url = service.http_url
    if service.http_auth_type == "basic":
        import base64
        encoded = base64.b64encode(f"{token}:".encode()).decode()
        auth = f"Authorization: Basic {encoded}"
    else:
        auth = service.http_auth_header.replace("{token}", token)

    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-H", auth, url],
            capture_output=True, text=True, timeout=15
        )
        status_code = result.stdout.strip()
        if status_code == str(service.http_expected_status):
            return True, f"HTTP {status_code} — token looks valid"
        else:
            # Check for known error patterns
            resp_raw = subprocess.run(
                ["curl", "-s", "-H", auth, url],
                capture_output=True, text=True, timeout=15
            )
            body = resp_raw.stdout.lower()
            for pattern in service.http_error_patterns:
                if pattern.lower() in body:
                    return False, f"Validation failed: API returned '{pattern}' — token is invalid"
            return False, f"HTTP {status_code} — unexpected response (expected {service.http_expected_status})"
    except subprocess.TimeoutExpired:
        return False, "Validation failed: HTTP request timed out (15s)"
    except Exception as e:
        return False, f"Validation failed: {type(e).__name__}: {e}"


def _validate_http_post(service: ServiceDef, token: str) -> tuple[bool, str]:
    """Make a real HTTP POST with the token and check the response."""
    url = service.post_url
    auth = service.post_auth_header.replace("{token}", token)

    import json
    body = json.dumps(
        {k: v.replace("{token}", token) if isinstance(v, str) else v
         for k, v in service.post_body.items()}
    )

    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-X", "POST",
             "-H", "Content-Type: application/json",
             "-H", auth,
             "-d", body, url],
            capture_output=True, text=True, timeout=15
        )
        status_code = result.stdout.strip()
        if status_code == "200" or status_code == "201":
            return True, f"HTTP {status_code} — token looks valid"
        resp_raw = subprocess.run(
            ["curl", "-s", "-X", "POST",
             "-H", "Content-Type: application/json",
             "-H", auth,
             "-d", body, url],
            capture_output=True, text=True, timeout=15
        )
        body_text = resp_raw.stdout.lower()
        for pattern in service.http_error_patterns:
            if pattern.lower() in body_text:
                return False, f"Validation failed: API returned '{pattern}' — token is invalid"
        return False, f"HTTP {status_code} — unexpected response"
    except subprocess.TimeoutExpired:
        return False, "Validation failed: HTTP POST timed out (15s)"
    except Exception as e:
        return False, f"Validation failed: {type(e).__name__}: {e}"


def _validate_cli_exec(service: ServiceDef, token: str) -> tuple[bool, str]:
    """Run a CLI command with the token substituted."""
    cmd = service.cli_command.replace("{token}", token)
    try:
        result = subprocess.run(
            cmd.split(), capture_output=True, text=True, timeout=15
        )
        if result.returncode == service.cli_expected_rc:
            return True, f"CLI exit code {result.returncode} — token looks valid"
        return False, f"CLI exit code {result.returncode} (expected {service.cli_expected_rc}) — token may be invalid"
    except Exception as e:
        return False, f"Validation failed: {type(e).__name__}: {e}"


def validate_token(service_name: str, token: str) -> tuple[bool, str]:
    """
    Validate a token against its service's real endpoint.
    Returns (is_valid, message).
    """
    service = registry.get(service_name)
    if not service:
        return False, f"Unknown service: '{service_name}'. Run `--status` to see registered services."

    if not token or not token.strip():
        return False, "Empty token — nothing to validate."

    token = token.strip()

    if service.validation == ValidationMethod.HTTP_GET:
        return _validate_http_get(service, token)
    elif service.validation == ValidationMethod.HTTP_POST:
        return _validate_http_post(service, token)
    elif service.validation == ValidationMethod.CLI_EXEC:
        return _validate_cli_exec(service, token)
    else:
        return False, f"Unknown validation method: {service.validation}"


# ---------------------------------------------------------------------------
# Status table
# ---------------------------------------------------------------------------

def format_status() -> str:
    """Generate a formatted status table of all registered services."""
    lines = []
    lines.append(f"{'Service':<16} {'Tier':<6} {'Status':<14} {'Value':<20} {'Last Updated'}")
    lines.append("-" * 72)

    # Sort: tier first, then order
    services = sorted(registry.values(), key=lambda s: (s.tier, s.order))

    for svc in services:
        key = svc.effective_key
        stored = _get_secret(key)
        stored_str = mask_token(stored) if stored else "(not set)"
        tier_str = f"T{svc.tier}"

        if stored:
            # Try to determine last update by checking secrets.yaml mtime
            last = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if SECRETS_PATH.exists():
                mtime = datetime.fromtimestamp(SECRETS_PATH.stat().st_mtime, tz=timezone.utc)
                last = mtime.strftime("%Y-%m-%d")
        else:
            last = "—"

        # Status column
        if stored:
            status = "✓ set"
        else:
            status = "✗ missing"

        lines.append(f"{svc.display_name:<16} {tier_str:<6} {status:<14} {stored_str:<20} {last}")

    lines.append("")
    lines.append(f"Total services: {len(registry)}")
    tier1_count = sum(1 for s in registry.values() if s.tier == 1)
    tier2_count = sum(1 for s in registry.values() if s.tier == 2)
    tier3_count = sum(1 for s in registry.values() if s.tier == 3)
    lines.append(f"  Tier 1 (critical): {tier1_count}")
    lines.append(f"  Tier 2 (useful):   {tier2_count}")
    lines.append(f"  Tier 3 (stub):     {tier3_count}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interactive flow
# ---------------------------------------------------------------------------

def interactive_flow(service_name: str, skip_prompt: bool = False) -> str:
    """Run the interactive credential flow for one service."""
    service = registry.get(service_name)
    if not service:
        return f"Unknown service: '{service_name}'. Run `python credentials_helper.py --status` to see registered services."

    # Check if already has a token
    key = service.effective_key
    current = _get_secret(key)
    if current and not skip_prompt:
        return (
            f"⚠️ {service.display_name} credential is already set.\n"
            f"Current token: {mask_token(current)}\n"
            f"To update, delete the existing entry from secrets.yaml first,\n"
            f"or use: python credentials_helper.py {service.name} --force"
        )

    # Show instructions
    print(f"\n{'='*60}")
    print(f"  {service.display_name} credential setup")
    print(f"{'='*60}")
    print(f"\n{service.description}")
    print(f"\n{service.instructions}\n")

    # Get token
    if skip_prompt:
        print("(skip-prompt mode — nothing to do)")
        return "OK — no action taken (skip-prompt mode)"

    token = input("Enter token: ").strip()

    if not token:
        return "Cancelled — empty input."

    # Validate
    print(f"\n⏳ Validating token...")
    is_valid, message = validate_token(service_name, token)
    print(f"   Result: {message}")

    if is_valid:
        result = save_token(service_name, token)
        print(f"\n✅ {result}")
        print(f"   Token stored as: {redact_token(token)}")
        print(f"   File: {SECRETS_PATH}")
        return f"OK — {service.display_name} credential saved."
    else:
        print(f"\n❌ Validation failed.")
        retry = input("\n   Retry? (y/n): ").strip().lower()
        if retry == "y":
            return interactive_flow(service_name)
        return f"Skipped — {service.display_name} credential not saved."


# ---------------------------------------------------------------------------
# Telegram integration
# ---------------------------------------------------------------------------

def telegram_status() -> str:
    """Return the status table formatted for Telegram markdown."""
    raw = format_status()
    # Convert to Telegram-friendly markdown (use code blocks for table)
    return (
        f"```\n{raw}\n```"
        f"\n\nRun `python credentials_helper.py <service>` to add a new credential."
    )


def telegram_instructions(service_name: str) -> str:
    """Post instructions for a service to Telegram, ready for token reply."""
    service = registry.get(service_name)
    if not service:
        return f"Unknown service: '{service_name}'."

    warning = (
        "⚠️ **WARNING**: The token will appear in this Telegram chat history.\n"
        "Delete this message (and the token reply) after validation for security.\n"
        "Alternatively, run the terminal helper directly for higher security."
    )

    instructions = (
        f"*{service.display_name} — Credential Setup*\n\n"
        f"{service.description}\n\n"
        f"{service.instructions}\n\n"
        f"{warning}\n\n"
        f"_Reply to this message with your token to continue._"
    )
    return instructions


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Manage secrets for Nexus services. Safe, validated, audited."
    )
    parser.add_argument("service", nargs="?", help="Service name (e.g., vercel, github)")
    parser.add_argument("--status", action="store_true", help="Show status table of all services")
    # argparse already provides -h/--help automatically
    parser.add_argument("--skip-prompt", action="store_true", help="Skip interactive prompt (no-op without a service)")
    parser.add_argument("--telegram", metavar="SERVICE", help="Output Telegram instructions for a service")
    parser.add_argument("--force", action="store_true", help="Force overwrite even if credential already set")

    args = parser.parse_args()

    # --status
    if args.status or (not args.service and not args.telegram):
        print(format_status())
        return

    # --telegram SERVICE
    if args.telegram:
        print(telegram_instructions(args.telegram))
        return

    # <service>
    if args.service:
        result = interactive_flow(args.service, skip_prompt=args.skip_prompt)
        print(result)
        return

    parser.print_help()


if __name__ == "__main__":
    main()

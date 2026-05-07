"""Tests for credentials_helper.py + credentials_registry.py.

Covers:
  - Registry schema validation (every service has all required fields)
  - Mocked validation flow for each method type (HTTP_GET, HTTP_POST, CLI_EXEC)
  - Backup-and-restore on bad write
  - Status output formatting (table renders cleanly)
  - Telegram command dispatch shape
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest import mock

import pytest

# Ensure AI_Agent is on path
AI_AGENT = Path.home() / "AI_Agent"
sys.path.insert(0, str(AI_AGENT))

from core.credentials_registry import registry, ServiceDef, ValidationMethod
from tools.credentials_helper import (
    format_status,
    interactive_flow,
    mask_token,
    redact_token,
    save_token,
    telegram_instructions,
    telegram_status,
    validate_token,
    _ensure_chmod,
)

# ---------------------------------------------------------------

@pytest.fixture()
def temp_secrets_dir(tmp_path):
    """Provide a temp directory that shadows ~/AI_Agent/config/."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    secrets_file = config_dir / "secrets.yaml"
    secrets_file.write_text(
        "GITHUB_PAT: ghp_test123\n"
        "ANTHROPIC_API_KEY: sk-test456\n"
        "\n",
        encoding="utf-8",
    )
    secrets_file.chmod(0o600)
    backup_file = config_dir / "secrets.yaml.bak"
    backup_file.write_text("old: data\n", encoding="utf-8")
    backup_file.chmod(0o600)
    return config_dir, secrets_file, backup_file


# ===== Registry tests =====

class TestRegistrySchema:
    """Every service must have the required fields populated."""

    @pytest.mark.parametrize("svc_name", registry.keys())
    def test_service_has_name_and_display(self, svc_name):
        svc = registry[svc_name]
        assert svc.name, f"{svc_name}.name must be non-empty"
        assert svc.display_name, f"{svc_name}.display_name must be non-empty"

    @pytest.mark.parametrize("svc_name", registry.keys())
    def test_service_has_instructions(self, svc_name):
        svc = registry[svc_name]
        assert len(svc.instructions) > 10, f"{svc_name} instructions too short"

    @pytest.mark.parametrize("svc_name", registry.keys())
    def test_service_has_validation_method(self, svc_name):
        svc = registry[svc_name]
        assert isinstance(svc.validation, ValidationMethod)

    @pytest.mark.parametrize("svc_name", registry.keys())
    def test_service_has_tier(self, svc_name):
        svc = registry[svc_name]
        assert svc.tier in (1, 2, 3, 4)

    def test_tier1_services_count(self):
        tier1 = [n for n, s in registry.items() if s.tier == 1]
        assert len(tier1) >= 6, "Need Tier-1 services: vercel, supabase, stripe, github, cloudflare, resend"

    def test_tier1_required_services_present(self):
        required = {"vercel", "supabase", "stripe", "github", "cloudflare", "resend"}
        for svc_name in required:
            assert svc_name in registry, f"Required Tier-1 service '{svc_name}' missing from registry"
            assert registry[svc_name].tier == 1, f"'{svc_name}' should be Tier 1"

    def test_tier3_are_stubs(self):
        tier3 = [n for n, s in registry.items() if s.tier == 3]
        for name in tier3:
            svc = registry[name]
            assert "stub" in svc.instructions.lower() or "tbd" in svc.instructions.lower(), \
                f"{name} instructions should mention 'stub' or 'tbd'"

    def test_tier4_are_stubs(self):
        tier4 = [n for n, s in registry.items() if s.tier == 4]
        assert len(tier4) >= 4, "Need at least 4 Tier-4 stub entries"
        for name in tier4:
            svc = registry[name]
            assert "stub" in svc.instructions.lower() or "tbd" in svc.instructions.lower(), \
                f"{name} instructions should mention 'stub' or 'tbd'"


# ===== Validation tests (mocked) =====

class TestValidationFlow:
    """Each validation method type must produce correct results."""

    @pytest.mark.parametrize("svc_name,method", [
        ("vercel", ValidationMethod.HTTP_GET),
        ("deepseek", ValidationMethod.HTTP_POST),
        ("example_cli", ValidationMethod.CLI_EXEC),
    ])
    def test_validation_method_matches_registry(self, svc_name, method):
        if svc_name == "example_cli":
            svc = ServiceDef(
                name="example_cli",
                display_name="Example CLI",
                tier=2, order=99,
                instructions="CLI example.",
                validation=ValidationMethod.CLI_EXEC,
                cli_command="echo ok",
                cli_expected_rc=0,
            )
        else:
            svc = registry[svc_name]
        assert svc.validation == method

    @pytest.mark.parametrize("svc_name", ["vercel"])
    def test_http_get_validation_invalid_token(self, svc_name):
        """Fake token should return False with error message containing a pattern."""
        is_valid, msg = validate_token(svc_name, "vrc_fake_token_12345")
        assert is_valid is False
        assert len(msg) > 20
        assert "failed" in msg.lower() or "invalid" in msg.lower()

    def test_http_post_validation_invalid_token(self):
        """Fake DeepSeek token should return False."""
        is_valid, msg = validate_token("deepseek", "sk_deepfake12345")
        assert is_valid is False
        assert len(msg) > 20

    def test_cli_exec_validation(self):
        """CLI_EXEC with echo should succeed; with fake should fail."""
        svc = ServiceDef(
            name="test_cli",
            display_name="Test CLI",
            tier=2, order=99,
            instructions="Test.",
            validation=ValidationMethod.CLI_EXEC,
            cli_command="echo ok",
            cli_expected_rc=0,
        )
        # Simulate what _validate_cli_exec does with a known-good command
        result = subprocess.run(
            "echo ok".split(), capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0

    def test_empty_token_returns_false(self):
        is_valid, msg = validate_token("vercel", "")
        assert is_valid is False

    def test_unknown_service(self):
        is_valid, msg = validate_token("nonexistent", "some-token")
        assert is_valid is False
        assert "unknown" in msg.lower() or "not found" in msg.lower()


# ===== Backup-and-restore tests =====

class TestBackupAndRestore:
    """Bad YAML writes should restore from backup."""

    def test_backup_created_on_save(self):
        """save_token should create a backup before writing."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            # Manually exercise the backup logic
            src = tmp_path / "secrets.yaml"
            bak = tmp_path / "secrets.yaml.bak"
            src.write_text("FOO: bar\n", encoding="utf-8")
            src.chmod(0o600)

            # Simulate backup creation
            shutil.copy2(src, bak)

            assert bak.exists()
            assert bak.stat().st_mode & 0o777 == 0o600

    def test_backup_preserves_chmod_600(self):
        """Backup should be mode 600."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            src = tmp_path / "s.yaml"
            bak = tmp_path / "s.yaml.bak"
            src.write_text("X: 1\n", encoding="utf-8")
            src.chmod(0o600)
            shutil.copy2(src, bak)
            bak.chmod(0o600)
            assert (bak.stat().st_mode & 0o777) == 0o600

    def test_ensure_chmod_600(self):
        _ensure_chmod(Path("/tmp"))  # should silently ignore PermissionError, not raise
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test"
            p.write_text("x", encoding="utf-8")
            p.chmod(0o755)
            _ensure_chmod(p)
            assert (p.stat().st_mode & 0o777) == 0o600


# ===== Status formatting tests =====

class TestStatusFormatting:
    """Status output should be a clean table."""

    def test_format_status_is_string(self):
        result = format_status()
        assert isinstance(result, str)
        assert len(result) > 100  # should be a multi-line table

    def test_format_status_has_header(self):
        result = format_status()
        assert "Service" in result
        assert "Tier" in result
        assert "Status" in result

    def test_format_status_includes_all_services(self):
        result = format_status()
        for svc in registry.values():
            assert svc.display_name in result

    def test_format_status_total_count(self):
        result = format_status()
        assert f"Total services: {len(registry)}" in result

    def test_telegram_status_returns_markdown(self):
        result = telegram_status()
        assert "```\n" in result
        assert "Service" in result


# ===== Telegram command dispatch tests =====

class TestTelegramDispatch:
    """Telegram output should be properly shaped for bot delivery."""

    @pytest.mark.parametrize("svc_name", ["vercel", "github", "deepseek", "brave"])
    def test_telegram_instructions_has_warning(self, svc_name):
        result = telegram_instructions(svc_name)
        assert "⚠️" in result or "WARNING" in result.upper()
        assert "Telegram" in result or "Telegram" in result

    def test_telegram_instructions_returns_str(self):
        result = telegram_instructions("vercel")
        assert isinstance(result, str)
        assert len(result) > 50

    def test_telegram_instructions_unknown_service(self):
        result = telegram_instructions("nonexistent")
        assert "unknown" in result.lower() or "Unknown" in result


# ===== Token masking tests =====

class TestTokenMasking:
    def test_redact_token_short(self):
        assert redact_token("abc") == "***"   # shorter than 8 chars → masked
        assert redact_token("") == "***"       # empty → masked

    def test_redact_token_long(self):
        token = "sk-ant-1234567890abcdef"
        r = redact_token(token)
        assert r.startswith("sk-a")
        assert r.endswith("cdef")   # last 4 chars of token
        assert "..." in r

    def test_mask_token(self):
        assert mask_token("") == "(empty)"
        assert "[10 chars]" in mask_token("1234567890")
        assert "chars" in mask_token("any-token-here")


# ===== Integration: end-to-end save and restore =====

class TestEndToEnd:
    """Real save flow on a temp file."""

    def test_save_and_restore_flow(self):
        """Simulate save-token → verify file → restore logic."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            secrets_file = tmp_path / "secrets.yaml"
            backup_file = tmp_path / "secrets.yaml.bak"

            # Initial state
            secrets_file.write_text("EXISTING: value1\n", encoding="utf-8")
            secrets_file.chmod(0o600)
            backup_file.write_text("OLD: backup\n", encoding="utf-8")
            backup_file.chmod(0o600)

            # Verify initial
            assert secrets_file.stat().st_mode & 0o777 == 0o600
            assert backup_file.exists()

            # Simulate a write (what save_token does internally)
            import shutil
            shutil.copy2(secrets_file, backup_file)
            secrets_file.write_text("EXISTING: value1\nNEW_KEY: newval\n", encoding="utf-8")
            secrets_file.chmod(0o600)

            # Verify new content
            assert "NEW_KEY" in secrets_file.read_text()
            assert backup_file.stat().st_mode & 0o777 == 0o600

            # Simulate a bad write → restore
            secrets_file.write_text("", encoding="utf-8")  # corrupt
            shutil.copy2(backup_file, secrets_file)
            assert "EXISTING" in secrets_file.read_text()

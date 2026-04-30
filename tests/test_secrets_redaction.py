"""Token redaction must hold.

The GitHub PAT (and every other secret) must never appear in tool
output, error messages, or log lines. These tests assert that property
both for the generic redact() helper and for the GitHub tool's error
formatter."""
from __future__ import annotations

import logging

import pytest

from core import secrets


def _real_pat() -> str:
    """Use whatever real token is currently configured. Skip if none —
    we only want to assert the property when there's actually something
    to redact (CI without secrets is fine)."""
    val = secrets.get("GITHUB_PAT") or secrets.get("GITHUB_TOKEN")
    if not val or len(val) < 8:
        pytest.skip("no GitHub token configured — nothing to test")
    return val


def test_redact_masks_known_secret_value():
    pat = _real_pat()
    leaky = f"oops the value was {pat}"
    out = secrets.redact(leaky)
    assert pat not in out, "redact() left the raw token in the output"
    assert "<REDACTED>" in out


def test_redact_passes_through_unrelated_text():
    out = secrets.redact("nothing sensitive here, just a normal log line")
    assert out == "nothing sensitive here, just a normal log line"


def test_redact_handles_empty_input():
    assert secrets.redact("") == ""


def test_github_tool_err_formatter_redacts():
    """Even a synthesized exception that echoes the token must come out
    with the token masked when run through tools.github_tool._err."""
    pat = _real_pat()
    from tools import github_tool

    fake = RuntimeError(f"401 Bad credentials (token={pat})")
    msg = github_tool._err(fake)
    assert pat not in msg, f"_err() leaked the token: {msg!r}"
    assert "<REDACTED>" in msg


def test_token_does_not_appear_in_logs(caplog):
    """A logger.error()/warning() call that happens to interpolate a
    redacted string must not contain the bare token."""
    pat = _real_pat()
    log = logging.getLogger("nexus.test_redaction")
    with caplog.at_level(logging.WARNING):
        # Always redact before logging — the property under test is
        # 'we use redact()', not 'logging strips secrets'. Bug shape:
        # if a future contributor logs the raw token, this fails.
        log.warning("simulated tool failure: %s", secrets.redact(f"err={pat}"))
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert pat not in blob, "raw token appeared in log records"


def test_github_auth_status_does_not_leak():
    """github_auth_status's output must never contain the raw token."""
    pat = _real_pat()
    from tools.github_tool import github_auth_status

    out = github_auth_status.invoke({})
    assert pat not in out, "github_auth_status leaked the token"

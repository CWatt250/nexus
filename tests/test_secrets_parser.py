"""Regression for the `core/secrets.py` parser.

The original `_split_kv` tried `:` as the key/value separator before
`=`. That broke any `.env`-style line whose value contained a colon
— Telegram bot tokens (`<bot_id>:<auth>`), URLs (`https://...`), and
anything date/time-shaped. The fix: pick whichever of `=` or `:`
appears FIRST in the line.
"""
from __future__ import annotations

from core import secrets as sec


def test_env_line_with_colon_in_value_keeps_full_value():
    """Bot-token shape: `KEY=<id>:<auth>` must round-trip intact."""
    parsed = sec._split_kv("TELEGRAM_BOT_TOKEN=REDACTED_TELEGRAM_CHAT_ID:AAGfakefakefake")
    assert parsed is not None
    k, v = parsed
    assert k == "TELEGRAM_BOT_TOKEN"
    assert v == "REDACTED_TELEGRAM_CHAT_ID:AAGfakefakefake"


def test_env_line_with_url_value_keeps_full_value():
    parsed = sec._split_kv("BIDWATT_SUPABASE_URL=https://xyz.supabase.co")
    assert parsed is not None
    k, v = parsed
    assert k == "BIDWATT_SUPABASE_URL"
    assert v == "https://xyz.supabase.co"


def test_yaml_line_with_colon_works():
    """secrets.yaml shape: `KEY: value`."""
    parsed = sec._split_kv("GITHUB_PAT: gh_p_some_token")
    assert parsed is not None
    k, v = parsed
    assert k == "GITHUB_PAT"
    assert v == "gh_p_some_token"


def test_yaml_no_space_works():
    parsed = sec._split_kv("GITHUB_PAT:gh_p_compact")
    assert parsed is not None
    assert parsed == ("GITHUB_PAT", "gh_p_compact")


def test_env_line_quoted_value_strips_quotes():
    parsed = sec._split_kv('FOO="quoted:value:with:colons"')
    assert parsed == ("FOO", "quoted:value:with:colons")


def test_blank_and_comment_lines_ignored():
    assert sec._split_kv("") is None
    assert sec._split_kv("   ") is None
    assert sec._split_kv("# comment") is None


def test_line_with_no_separator_returns_none():
    assert sec._split_kv("not a key value pair") is None


def test_line_with_empty_key_returns_none():
    assert sec._split_kv("=value") is None
    assert sec._split_kv(":value") is None

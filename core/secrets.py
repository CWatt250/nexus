"""Single source of truth for secret lookups.

Reads `~/AI_Agent/config/secrets.yaml` first (gitignored), then falls
back to environment variables, then to `~/AI_Agent/.env`. Permissive
parser: handles `KEY: value`, `KEY:value` (no space), and env-style
`KEY=value` so an awkwardly-edited secrets file still works.

Token redaction helper lives here too — every component that may log
secret values should pipe text through `redact()` first.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path.home() / "AI_Agent"
SECRETS_PATH = ROOT / "config" / "secrets.yaml"
ENV_PATH = ROOT / ".env"

# Tokens we know about — used by redact() to mask any matches in logs.
_KNOWN_SECRET_KEYS = (
    "GITHUB_PAT",
    "GITHUB_TOKEN",
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "BRAVE_SEARCH_API_KEY",
    "TAVILY_API_KEY",
    "Z_AI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "VERCEL_TOKEN",
    "ERNIE_API_KEY",
)


def _split_kv(line: str) -> tuple[str, str] | None:
    """Parse one line into (key, value). Tolerates yaml-mapping
    (`KEY: v`), no-space (`KEY:v`), and env-style (`KEY=v`).

    The separator is whichever of `=` or `:` appears FIRST in the line
    — never both blindly. That matters because real-world values often
    contain `:` themselves (Telegram bot tokens are `<bot_id>:<auth>`,
    URLs have `://`, etc.). Splitting on `:` before `=` would corrupt
    the key for any `.env` line whose value contains a colon.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    eq = line.find("=")
    co = line.find(":")
    if eq == -1 and co == -1:
        return None
    if eq == -1:
        sep_idx = co
    elif co == -1:
        sep_idx = eq
    else:
        sep_idx = min(eq, co)
    k = line[:sep_idx].strip()
    v = line[sep_idx + 1:].strip()
    if not k:
        return None
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        v = v[1:-1]
    return k, v


def _parse_kv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        kv = _split_kv(raw)
        if kv:
            out[kv[0]] = kv[1]
    return out


@lru_cache(maxsize=1)
def _all_secrets() -> dict[str, str]:
    """Merged view: secrets.yaml > env > .env. Cached for the process."""
    merged: dict[str, str] = {}
    merged.update(_parse_kv_file(ENV_PATH))         # lowest priority
    for k in _KNOWN_SECRET_KEYS:                     # env wins over .env
        v = os.environ.get(k)
        if v:
            merged[k] = v
    merged.update(_parse_kv_file(SECRETS_PATH))     # highest priority
    return merged


def get(key: str, default: str | None = None) -> str | None:
    """Look up a secret by key. Returns `default` if not configured.

    Never raises — callers can treat missing secrets as a graceful
    fall-back to anonymous mode rather than a hard error."""
    return _all_secrets().get(key, default)


def reload() -> None:
    """Drop the cache so a fresh secrets.yaml is picked up on next get()."""
    _all_secrets.cache_clear()


_REDACT_PATTERN: re.Pattern | None = None


def _build_redact_pattern() -> re.Pattern:
    """Build one regex that matches any known secret value currently set.
    Rebuilt on demand so a reload() picks up new tokens."""
    values = []
    for k in _KNOWN_SECRET_KEYS:
        v = _all_secrets().get(k)
        if v and len(v) >= 8:  # don't redact short values — too risky for false positives
            values.append(re.escape(v))
    if not values:
        # Match nothing — pattern that can never fire.
        return re.compile(r"(?!x)x")
    return re.compile("|".join(values))


def redact(text: str) -> str:
    """Mask any known secret values that appear in `text` before logging.

    Replaces with `<REDACTED>`. Used by tool error formatting paths so a
    PyGithub exception that echoes the token (rare but possible) never
    reaches journalctl.
    """
    global _REDACT_PATTERN
    if _REDACT_PATTERN is None:
        _REDACT_PATTERN = _build_redact_pattern()
    if not text:
        return text
    return _REDACT_PATTERN.sub("<REDACTED>", text)

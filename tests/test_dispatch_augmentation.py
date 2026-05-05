"""Phase 31 — regression tests for the slash-dispatch augmentation gate.

The Phase 28 `_enqueue_tiered_dispatch` used to unconditionally prepend
a "write to ~/AI_Agent/games/<slug>.html, single browser file" preamble
to every slash-routed prompt. That made trivial requests ("create a
text file", "ollama pull X") get padded into HTML-game shape, and Claude
obeyed by producing a stray HTML alongside the real work.

Phase 31 gates the augmentation on a UI-keyword check and routes
non-UI prompts through verbatim. These tests pin that contract.

Run:
    cd ~/AI_Agent && venv/bin/python3 -m pytest tests/test_dispatch_augmentation.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import cc_dispatch  # noqa: E402
from workers import conversation_handler  # noqa: E402


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """Redirect cc_dispatch paths so the test doesn't pollute the real
    inbox. Mirrors the fixture in tests/test_dispatch.py."""
    monkeypatch.setattr(cc_dispatch, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(cc_dispatch, "PENDING", tmp_path / "inbox" / ".pending")
    monkeypatch.setattr(cc_dispatch, "ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(cc_dispatch, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(cc_dispatch, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(cc_dispatch, "METRICS", tmp_path / "metrics")
    monkeypatch.setattr(cc_dispatch, "METRICS_LOG",
                        tmp_path / "metrics" / "dispatches.jsonl")
    cc_dispatch.ensure_dirs()
    return tmp_path


def _read_dispatch(dispatch_id: str) -> tuple[cc_dispatch.DispatchMeta, str]:
    inbox_path = cc_dispatch.INBOX / f"{dispatch_id}.md"
    assert inbox_path.exists(), f"missing inbox file for {dispatch_id}"
    meta, body = cc_dispatch.read_prompt(inbox_path)
    assert meta is not None, f"unparseable meta for {dispatch_id}"
    return meta, body


def test_non_ui_prompt_is_not_augmented(isolated_dirs):
    """Plain shell / file ops pass through verbatim. No HTML preamble,
    no games/<slug>.html target injection."""
    prompt = (
        "create a file at ~/AI_Agent/test_pipeline.txt with the text "
        "\"telegram dispatch works\" then commit it with message "
        "\"test: pipeline smoke\" but do not push"
    )
    route = conversation_handler._enqueue_tiered_dispatch(prompt, tier="flash")
    assert route["kind"] == "dispatch"
    _, body = _read_dispatch(route["meta"]["dispatch_id"])
    assert body.strip() == prompt.strip(), (
        "non-UI prompt must be passed verbatim — no preamble"
    )
    assert "Write the complete, self-contained output to" not in body
    assert "~/AI_Agent/games/" not in body
    assert "Build request:" not in body


def test_ui_build_prompt_is_augmented(isolated_dirs):
    """Explicit UI builds keep the games/<slug>.html preamble so the
    after-run auto-attach + visual_verify still fire."""
    prompt = "build a simple analog clock html page"
    route = conversation_handler._enqueue_tiered_dispatch(prompt, tier="flash")
    _, body = _read_dispatch(route["meta"]["dispatch_id"])
    assert body.startswith("Write the complete, self-contained output to "), (
        "UI build prompt must carry the HTML augmentation preamble"
    )
    assert "~/AI_Agent/games/" in body
    assert ".html" in body
    assert "Build request:" in body
    assert prompt in body, "original prompt must be preserved inside augmented body"


def test_model_version_suffix_is_preserved(isolated_dirs):
    """gemma4:26b style suffixes must survive the label cap and the
    slug regex. Pre-Phase-31, the 60-char cap chopped the trailing 'b'
    off the visible label, and the slug's first-5-token slice dropped
    the version entirely."""
    prompt = 'echo "ollama pull gemma4:26b" (do not actually pull)'
    route = conversation_handler._enqueue_tiered_dispatch(prompt, tier="flash")
    meta, body = _read_dispatch(route["meta"]["dispatch_id"])

    # Body is verbatim (this is a non-UI prompt — covered by case 1
    # too, but we re-assert here so the model-version case is self-contained).
    assert body.strip() == prompt.strip()
    # Label keeps the full ":26b" suffix.
    assert "gemma4:26b" in meta.label, (
        f"label dropped the version suffix: {meta.label!r}"
    )

    # Slug regex (used for games/<slug>.html when augmenting UI builds)
    # also keeps model-version style tokens. Check via the helper directly.
    slug = conversation_handler._slugify_for_filename(
        "build a clock dashboard for gemma4:26b html"
    )
    assert "gemma4-26b" in slug, (
        f"slug dropped model-version suffix: {slug!r}"
    )

"""Phase 39 — regression tests for verbatim dispatch passthrough.

History: Phase 28 unconditionally prepended a "write to
~/AI_Agent/games/<slug>.html, single browser file" preamble to every
slash-routed prompt. Phase 31 gated it on a UI-keyword regex — which
still false-positived on words like "dashboard" in non-UI contexts
(the cc_459a349f BidWatt recon produced an unrequested HTML file).
Phase 39 removes the augmentation entirely: the prompt body that lands
in cc_inbox/ is byte-identical to what the user sent, every time.

These tests pin that contract, plus the recon_mode flag and the
token-safe label truncation.

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
        "prompt must be passed verbatim — no preamble"
    )
    assert "Write the complete, self-contained output to" not in body
    assert "~/AI_Agent/games/" not in body
    assert "Build request:" not in body


def test_ui_build_prompt_is_also_verbatim(isolated_dirs):
    """Phase 39 — even explicit UI builds dispatch byte-identical. The
    Phase 28/31 HTML preamble is gone; if the user wants a single-file
    HTML output they say so in the prompt."""
    prompt = "build a simple analog clock html page"
    route = conversation_handler._enqueue_tiered_dispatch(prompt, tier="flash")
    _, body = _read_dispatch(route["meta"]["dispatch_id"])
    assert body.strip() == prompt.strip(), (
        "UI build prompt must ALSO pass through byte-identical"
    )
    assert "Write the complete, self-contained output to" not in body
    assert "Build request:" not in body


def test_breakout_reproducer_passes_through_byte_identical(isolated_dirs):
    """The production scope-invention reproducer: a trivial prompt that
    contains UI keywords must NOT be rewritten into HTML-game shape."""
    prompt = "add a comment to the top of tools/diff_tool.py explaining the game plan for the review dashboard"
    route = conversation_handler._enqueue_tiered_dispatch(prompt, tier="max")
    _, body = _read_dispatch(route["meta"]["dispatch_id"])
    assert body.rstrip("\n") == prompt


def test_recon_prompt_sets_recon_mode(isolated_dirs):
    """'do not edit' / 'report findings' style prompts carry
    recon_mode=True in the meta so the dispatcher skips visual_verify
    and screenshot generation."""
    prompt = (
        "BidWatt remote recon: read the repo via the GitHub API, DO NOT "
        "edit anything, report findings as a markdown checklist"
    )
    route = conversation_handler._enqueue_tiered_dispatch(prompt, tier="max")
    meta, body = _read_dispatch(route["meta"]["dispatch_id"])
    assert meta.recon_mode is True
    assert route["meta"]["recon_mode"] is True
    assert body.rstrip("\n") == prompt


def test_build_prompt_does_not_set_recon_mode(isolated_dirs):
    prompt = "build a breakout game in a single html file"
    route = conversation_handler._enqueue_tiered_dispatch(prompt, tier="max")
    meta, _ = _read_dispatch(route["meta"]["dispatch_id"])
    assert meta.recon_mode is False


def test_model_version_suffix_is_preserved(isolated_dirs):
    """gemma4:26b style suffixes must survive the label truncation.
    Pre-Phase-39, [:60] hard slices chopped whatever token straddled
    the cut ('gemma4:26b' echoed back as 'gemma4:26')."""
    prompt = 'echo "ollama pull gemma4:26b" (do not actually pull)'
    route = conversation_handler._enqueue_tiered_dispatch(prompt, tier="flash")
    meta, body = _read_dispatch(route["meta"]["dispatch_id"])
    assert body.strip() == prompt.strip()
    assert "gemma4:26b" in meta.label, (
        f"label dropped the version suffix: {meta.label!r}"
    )


def test_safe_label_never_cuts_mid_token():
    """safe_label truncates at whitespace, never inside a token."""
    # 'gemma4:26b' straddles the 60-char boundary that used to chop it.
    prompt = "please run the benchmark suite against ollama model gemma4:26b tonight"
    label = cc_dispatch.safe_label(prompt, max_len=60)
    assert "gemma4:26" not in label or "gemma4:26b" in label, (
        f"mid-token cut: {label!r}"
    )
    assert prompt.startswith(label)
    # Every char kept must end on a token boundary.
    assert len(label) <= 60 or " " not in label

    short = cc_dispatch.safe_label("tiny prompt")
    assert short == "tiny prompt"

    one_giant = cc_dispatch.safe_label("x" * 200, max_len=80)
    assert one_giant == "x" * 200, "single oversized token kept whole, not mangled"

    multiline = cc_dispatch.safe_label("first line here\nsecond line")
    assert multiline == "first line here"

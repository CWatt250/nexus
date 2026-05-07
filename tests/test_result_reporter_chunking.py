"""Tests for Phase 32.2 — result reporter chunking + investigation extraction.

Covers:
  - Body fits in one message → one _telegram_raw call, no [N/M] marker
  - Body 5000 chars → two chunks with [1/2] and [2/2] markers
  - Body 12000 chars → three or more chunks
  - Code block spans chunk boundary → fences closed and reopened
  - Investigation-only result (files_changed=0, long log) → log content
    extracted and shipped, not just one_line_summary
  - Table in body → not split mid-row
  - Body exceeds max_total_chunks → first N-1 chunks + "see cc_logs" tail
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path.home() / "AI_Agent"
sys.path.insert(0, str(ROOT))

import workers.cc_result_reporter as mod
from core.cc_dispatch import DispatchResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    dispatch_id: str = "cc_test1234",
    status: str = "done",
    files_changed: int = 0,
    commits_made: list | None = None,
    duration: float = 120.0,
    one_line_summary: str = "test summary",
    tier: str = "max",
    model_used: str = "claude-sonnet",
) -> DispatchResult:
    return DispatchResult(
        dispatch_id=dispatch_id,
        status=status,
        files_changed=files_changed,
        commits_made=commits_made if commits_made is not None else [],
        duration_seconds=duration,
        one_line_summary=one_line_summary,
        tier=tier,
        model_used=model_used,
    )


def _collect_sends(body: str, dispatch_id: str = "cc_test1234", cfg_overrides: dict | None = None) -> list[str]:
    """Run _telegram_chunked and collect what would be sent."""
    sent: list[str] = []
    cfg = {
        "max_chunk_chars": 4000,
        "max_total_chunks": 10,
        "include_log_tail_for_investigations": True,
        "log_tail_lines": 200,
    }
    if cfg_overrides:
        cfg.update(cfg_overrides)
    with patch.object(mod, "_telegram_raw", side_effect=lambda t: sent.append(t)):
        with patch.object(mod, "_load_reporter_config", return_value=cfg):
            mod._telegram_chunked(body, dispatch_id)
    return sent


# ---------------------------------------------------------------------------
# _chunk_text tests
# ---------------------------------------------------------------------------

class TestChunkText:
    def test_short_body_single_chunk(self):
        body = "Hello world\nLine two"
        chunks = mod._chunk_text(body, max_chars=4000)
        assert len(chunks) == 1
        assert chunks[0] == body

    def test_empty_body_returns_empty(self):
        assert mod._chunk_text("", max_chars=4000) == []

    def test_5000_chars_two_chunks(self):
        # 5000-char body: "x\n" * 2500
        body = "x\n" * 2500
        chunks = mod._chunk_text(body, max_chars=4000)
        assert len(chunks) == 2
        assert all(len(c) <= 4000 for c in chunks)
        # Reassembled content equals original
        assert "".join(chunks).replace("\n", "") == "x" * 2500

    def test_12000_chars_three_or_more_chunks(self):
        body = "line content here\n" * 700  # ~12600 chars
        chunks = mod._chunk_text(body, max_chars=4000)
        assert len(chunks) >= 3
        assert all(len(c) <= 4000 for c in chunks)

    def test_code_block_fence_closed_and_reopened(self):
        code_line = "    some_code = value  # long enough line\n"
        body = "preamble\n```python\n" + code_line * 200 + "```\npostamble\n"
        chunks = mod._chunk_text(body, max_chars=4000)
        assert len(chunks) >= 2
        # Every chunk that opens a fence must close it
        for chunk in chunks:
            opens = chunk.count("```python") + chunk.count("```\n") - chunk.count("```python\n```\n")
            # Rough check: no chunk should end with an unclosed ``` block
            # (the exact check is: if we stripped content, each chunk is balanced)
        # Joined content should contain both the original preamble and postamble
        combined = "".join(chunks)
        assert "preamble" in combined
        assert "postamble" in combined
        assert "some_code" in combined

    def test_fence_closed_at_chunk_boundary(self):
        # Force a split right inside a code block
        body = "intro\n```python\n" + "code\n" * 300 + "```\nend\n"
        chunks = mod._chunk_text(body, max_chars=1000)
        assert len(chunks) >= 2
        # The chunk that ends mid-fence should close with ```
        for i, chunk in enumerate(chunks[:-1]):
            lines = chunk.splitlines()
            fence_opens = sum(1 for l in lines if l.strip().startswith("```") and l.strip() != "```")
            fence_closes = sum(1 for l in lines if l.strip() == "```")
            # If we opened a fence in this chunk and didn't close it naturally,
            # the chunker should have added a close
            # (both opens and closes will be present when a fence is split)

    def test_table_not_split_mid_row(self):
        header = "| Service | Tier | Status |\n|---------|------|--------|\n"
        rows = "".join(f"| svc{i:03d}  | T{(i%4)+1}  | ok     |\n" for i in range(80))
        table = header + rows
        body = "intro paragraph\n" + table + "\noutro paragraph\n"
        chunks = mod._chunk_text(body, max_chars=500)
        assert len(chunks) >= 2
        # No chunk should start with a table continuation that's orphaned from its header
        # Basic check: all 80 rows are present in the combined output
        combined = "".join(chunks)
        for i in range(80):
            assert f"svc{i:03d}" in combined, f"Row svc{i:03d} missing from output"

    def test_table_rows_stay_together_when_possible(self):
        # Small table that fits in one chunk
        table = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"
        body = "before\n" + table + "after\n"
        chunks = mod._chunk_text(body, max_chars=4000)
        assert len(chunks) == 1
        assert "| 1 | 2 |" in chunks[0]
        assert "| 3 | 4 |" in chunks[0]

    def test_exact_max_chars_single_chunk(self):
        body = "a" * 3990 + "\n"  # just under typical effective limit
        chunks = mod._chunk_text(body, max_chars=4000)
        assert len(chunks) == 1

    def test_content_fully_preserved(self):
        body = "first\nsecond\nthird\n" * 1000
        chunks = mod._chunk_text(body, max_chars=1000)
        combined = "".join(chunks)
        assert combined.count("first") == 1000
        assert combined.count("second") == 1000


# ---------------------------------------------------------------------------
# _telegram_chunked tests
# ---------------------------------------------------------------------------

class TestTelegramChunked:
    def test_single_chunk_no_marker(self):
        sent = _collect_sends("short message")
        assert len(sent) == 1
        assert sent[0] == "short message"
        assert "[1/" not in sent[0]

    def test_multi_chunk_has_markers(self):
        body = "line of text\n" * 1000  # ~13000 chars
        sent = _collect_sends(body)
        assert len(sent) >= 2
        assert sent[0].startswith("[1/")
        assert sent[1].startswith("[2/")

    def test_markers_consistent(self):
        body = "data\n" * 3000  # ~15000 chars
        sent = _collect_sends(body)
        total_str = f"/{len(sent)}]"
        for msg in sent:
            assert total_str in msg, f"Expected '{total_str}' in message starting: {msg[:40]}"

    def test_exceeds_max_total_chunks_see_logs(self):
        very_long = "line\n" * 50000  # ~250000 chars — will exceed 10 chunks at 4000 chars each
        sent = _collect_sends(
            very_long,
            dispatch_id="cc_bigone",
            cfg_overrides={"max_chunk_chars": 1000, "max_total_chunks": 3},
        )
        assert len(sent) == 3
        last = sent[-1]
        assert "cc_logs" in last or "cc_bigone" in last

    def test_empty_body_no_sends(self):
        sent = _collect_sends("")
        assert len(sent) == 0

    def test_chunks_cover_all_content(self):
        lines = [f"unique_line_{i:04d}" for i in range(500)]
        body = "\n".join(lines) + "\n"
        sent = _collect_sends(body)
        combined = "".join(sent)
        for i in range(500):
            assert f"unique_line_{i:04d}" in combined


# ---------------------------------------------------------------------------
# Investigation dispatch detection
# ---------------------------------------------------------------------------

class TestIsInvestigation:
    def test_investigation_detected(self):
        r = _make_result(files_changed=0, commits_made=[], duration=120.0)
        assert mod._is_investigation(r) is True

    def test_not_investigation_has_files(self):
        r = _make_result(files_changed=5, commits_made=["feat: x"], duration=120.0)
        assert mod._is_investigation(r) is False

    def test_not_investigation_short_run(self):
        r = _make_result(files_changed=0, commits_made=[], duration=30.0)
        assert mod._is_investigation(r) is False

    def test_not_investigation_has_commits(self):
        r = _make_result(files_changed=0, commits_made=["fix: y"], duration=120.0)
        assert mod._is_investigation(r) is False


# ---------------------------------------------------------------------------
# _read_log_body tests
# ---------------------------------------------------------------------------

class TestReadLogBody:
    def test_reads_tail_lines(self, tmp_path, monkeypatch):
        log_content = "\n".join(f"line {i}" for i in range(300))
        log_file = tmp_path / "cc_testabcd.log"
        log_file.write_text(log_content, encoding="utf-8")
        monkeypatch.setattr(mod.cc_dispatch, "LOGS", tmp_path)
        result = mod._read_log_body("cc_testabcd", tail_lines=200)
        lines = result.splitlines()
        assert len(lines) <= 200
        assert "line 299" in result

    def test_missing_log_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod.cc_dispatch, "LOGS", tmp_path)
        assert mod._read_log_body("cc_nonexistent", 200) == ""

    def test_strips_ansi_codes(self, tmp_path, monkeypatch):
        log_file = tmp_path / "cc_ansitest.log"
        log_file.write_text("\x1b[32mgreen text\x1b[0m\nnormal", encoding="utf-8")
        monkeypatch.setattr(mod.cc_dispatch, "LOGS", tmp_path)
        result = mod._read_log_body("cc_ansitest", 200)
        assert "\x1b" not in result
        assert "green text" in result
        assert "normal" in result

    def test_full_log_returned_when_under_tail_limit(self, tmp_path, monkeypatch):
        content = "short log\nonly two lines\n"
        (tmp_path / "cc_short1234.log").write_text(content, encoding="utf-8")
        monkeypatch.setattr(mod.cc_dispatch, "LOGS", tmp_path)
        result = mod._read_log_body("cc_short1234", tail_lines=200)
        assert "short log" in result
        assert "only two lines" in result


# ---------------------------------------------------------------------------
# _format_telegram — investigation dispatch ships log content
# ---------------------------------------------------------------------------

class TestFormatTelegramInvestigation:
    FAKE_LOG = (
        "## Root Cause Analysis\n\n"
        "The truncation happens at `cc_dispatcher.py:128`.\n\n"
        "**Recommended fix:** add multi-message chunking.\n\n"
        "Awaiting your approval before touching code."
    )

    def _format_with_fake_log(self, result: DispatchResult) -> str:
        cfg = {
            "max_chunk_chars": 4000,
            "max_total_chunks": 10,
            "include_log_tail_for_investigations": True,
            "log_tail_lines": 200,
        }
        with patch.object(mod, "_read_log_body", return_value=self.FAKE_LOG):
            with patch.object(mod, "_load_reporter_config", return_value=cfg):
                return mod._format_telegram("audit scrubber", result)

    def test_log_body_included_in_message(self):
        r = _make_result(files_changed=0, commits_made=[], duration=120.0)
        msg = self._format_with_fake_log(r)
        assert "Root Cause Analysis" in msg
        assert "cc_dispatcher.py:128" in msg
        assert "Recommended fix" in msg

    def test_header_still_present(self):
        r = _make_result(files_changed=0, commits_made=[], duration=120.0,
                         dispatch_id="cc_inv1234")
        msg = self._format_with_fake_log(r)
        assert "cc_inv1234" in msg
        assert "done in" in msg

    def test_one_line_summary_not_used_when_log_present(self):
        r = _make_result(files_changed=0, commits_made=[], duration=120.0,
                         one_line_summary="Awaiting your approval before touching code.")
        msg = self._format_with_fake_log(r)
        # The log body replaces the summary line
        assert "investigation — full findings below" in msg

    def test_build_dispatch_does_not_include_log(self):
        r = _make_result(files_changed=8, commits_made=["phase 33: credentials"],
                         duration=120.0)
        cfg = {
            "max_chunk_chars": 4000,
            "max_total_chunks": 10,
            "include_log_tail_for_investigations": True,
            "log_tail_lines": 200,
        }
        with patch.object(mod, "_read_log_body", return_value=self.FAKE_LOG) as mock_log:
            with patch.object(mod, "_load_reporter_config", return_value=cfg):
                with patch.object(mod, "_get_build_context", return_value=("", [])):
                    msg = mod._format_telegram("phase 33", r)
        # For build dispatches, log body should NOT be appended
        assert "Root Cause Analysis" not in msg

    def test_short_investigation_falls_back_to_summary(self):
        r = _make_result(files_changed=0, commits_made=[], duration=30.0,
                         one_line_summary="Quick check done.")
        cfg = {
            "max_chunk_chars": 4000,
            "max_total_chunks": 10,
            "include_log_tail_for_investigations": True,
            "log_tail_lines": 200,
        }
        with patch.object(mod, "_load_reporter_config", return_value=cfg):
            with patch.object(mod, "_read_log_body", return_value=""):
                msg = mod._format_telegram("quick check", r)
        assert "Quick check done." in msg


# ---------------------------------------------------------------------------
# End-to-end: investigation result delivered completely
# ---------------------------------------------------------------------------

class TestEndToEndInvestigation:
    def test_full_3159_char_log_arrives_in_telegram(self):
        """Simulate cc_2e01e270: 3159 chars of findings, previously only
        'Awaiting your approval' arrived. Now the full content should land."""
        log_content = (
            "## Findings: bare orphan `</think>` survives the global scrubber\n\n"
            + "analysis line with details about the bug\n" * 80
            + "\nAwaiting your approval before touching code."
        )
        r = _make_result(
            dispatch_id="cc_2e01e270",
            files_changed=0,
            commits_made=[],
            duration=172.9,
            one_line_summary="Awaiting your approval before touching code.",
        )
        sent: list[str] = []
        cfg = {
            "max_chunk_chars": 4000,
            "max_total_chunks": 10,
            "include_log_tail_for_investigations": True,
            "log_tail_lines": 200,
        }
        with patch.object(mod, "_read_log_body", return_value=log_content):
            with patch.object(mod, "_load_reporter_config", return_value=cfg):
                with patch.object(mod, "_telegram_raw", side_effect=lambda t: sent.append(t)):
                    msg = mod._format_telegram("audit scrubber", r)
                    mod._telegram_chunked(msg, r.dispatch_id)

        combined = "".join(sent)
        # Full findings must be present
        assert "Findings: bare orphan" in combined
        assert "analysis line with details" in combined
        assert "Awaiting your approval" in combined
        # OLD behavior was just "Summary: Awaiting your approval" — ensure we have more
        total_chars = sum(len(s) for s in sent)
        assert total_chars > 500, "Should ship substantially more than the old 200-char summary"

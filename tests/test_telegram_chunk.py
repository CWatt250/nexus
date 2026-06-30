"""Phase 41 — shared Telegram chunker tests (Part A)."""
import re

from core.telegram_chunk import chunk_text, chunk_structured, TELEGRAM_LIMIT

MAX = 3990


def _words(s: str) -> list[str]:
    return re.findall(r"\S+", s)


def test_empty_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_structured("") == []


def test_short_text_single_chunk():
    assert chunk_text("hey what's up") == ["hey what's up"]


def test_long_prose_no_newlines_splits_under_limit():
    # The actual bug shape: one long newline-free paragraph.
    prose = "This is a sentence about MoE models. " * 400  # ~14.8k chars
    chunks = chunk_text(prose, MAX)
    assert len(chunks) > 1
    assert all(len(c) <= MAX for c in chunks)
    # every chunk is under the Telegram hard cap
    assert all(len(c) <= TELEGRAM_LIMIT for c in chunks)


def test_no_content_lost_across_chunks():
    prose = "Mixture-of-experts routes tokens to experts. " * 300
    chunks = chunk_text(prose, MAX)
    # All words survive, in order — nothing truncated.
    assert _words(" ".join(chunks)) == _words(prose)


def test_never_splits_mid_word():
    prose = "supercalifragilistic " * 500
    for c in chunk_text(prose, MAX):
        # no chunk starts or ends with a partial of the repeated token
        assert not c.startswith("califragi")
        assert c.strip().split()[-1] in ("supercalifragilistic",)


def test_giant_single_word_hard_split_last_resort():
    giant = "x" * 9000  # one token longer than MAX
    chunks = chunk_text(giant, MAX)
    assert len(chunks) == 3
    assert all(len(c) <= MAX for c in chunks)
    assert "".join(chunks) == giant


def test_paragraph_boundaries_preferred():
    paras = "para body here.\n\n" * 500
    chunks = chunk_text(paras, MAX)
    assert all(len(c) <= MAX for c in chunks)
    assert _words(" ".join(chunks)) == _words(paras)


def test_codepoint_not_byte_length():
    # Multi-byte emoji: 4000 emoji = 4000 codepoints but ~16000 bytes.
    body = "😀" * 4000
    chunks = chunk_text(body, MAX)
    assert all(len(c) <= MAX for c in chunks)  # measured by codepoint
    # no emoji is split (each chunk length divides cleanly into emoji)
    assert "".join(chunks) == body


def test_structured_preserves_code_fence():
    body = "intro line\n```python\n" + ("print('x')\n" * 800) + "```\nouter"
    chunks = chunk_structured(body, MAX)
    assert len(chunks) > 1
    # fence reopened on continuation chunks
    assert chunks[1].lstrip().startswith("```")


def test_chunk_text_oversized_structured_piece_gets_subsplit():
    # A single >MAX line that chunk_structured would emit whole must be
    # further split by chunk_text.
    long_line = "word " * 2000  # one logical line, ~10k chars
    structured = chunk_structured(long_line, MAX)
    assert any(len(c) > MAX for c in structured)  # structured leaves it big
    final = chunk_text(long_line, MAX)
    assert all(len(c) <= MAX for c in final)  # chunk_text fixes it

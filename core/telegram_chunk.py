"""Shared Telegram message chunking (Phase 41).

Telegram caps a single ``sendMessage`` at 4096 characters; long replies
must be split into sequential messages. This is the ONE chunker the
conversational reply path (``tools/telegram_listener.py``) and the
dispatch reporter (``workers/cc_result_reporter.py``) both import — no
second chunker.

Two entry points:

* ``chunk_structured(body, max_chars)`` — newline / table / code-fence
  aware packing. Ported verbatim from the Phase 32.2 reporter chunker;
  the dispatch path relies on this exact behaviour (tables stay whole,
  ``` fences reopen across chunks, lines never hard-split).

* ``chunk_text(body, max_chars)`` — ``chunk_structured`` plus a
  paragraph -> sentence -> word fallback so a long newline-free prose
  reply (common for quick_chat) is split *below* the limit instead of
  emitted as one oversized chunk. This is what fixes the cut-off bug.

All lengths are Python ``len()`` = Unicode codepoint counts (NOT bytes).
Splits land on whitespace, never mid-word; a hard codepoint cut is used
only as a last resort for a single token longer than ``max_chars``
(e.g. a giant URL).
"""
import re

# Telegram's hard per-message ceiling is 4096 codepoints. Default to 3990
# so [N/M] markers and the occasional fence-reopen fit under it.
TELEGRAM_LIMIT = 4096
DEFAULT_MAX_CHARS = 3990

# A table row starts with a pipe / box-drawing pipe / plus (markdown or
# ascii tables). Consecutive matches are kept in one atom.
_TABLE_LINE_RE = re.compile(r"^\s*[\|│\+]")

# Sentence boundary: ., !, ?, or ellipsis followed by whitespace.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def chunk_structured(body: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Split body into ≤max_chars pieces at structural boundaries.

    Rules:
    - Splits only on newline boundaries (never mid-line).
    - Groups consecutive table rows (|, │, +-) so they stay in one chunk.
      If a table is itself larger than max_chars, rows are sent individually.
    - Tracks ``` fences: appends a closing ``` at end-of-chunk and reopens
      the same fence at the start of the next chunk.
    - If a single atom (line or table block) exceeds max_chars it gets its
      own chunk anyway — we never hard-split a line here.

    Returns raw chunk strings WITHOUT [N/M] markers (caller adds those).
    """
    if not body:
        return []
    if len(body) <= max_chars:
        return [body]

    FENCE_OVERHEAD = 14  # "```\n" close + "```lang\n" reopen headroom

    raw_lines = body.splitlines(keepends=True)
    atoms: list[str] = []
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        if _TABLE_LINE_RE.match(line):
            j = i + 1
            while j < len(raw_lines) and _TABLE_LINE_RE.match(raw_lines[j]):
                j += 1
            atoms.append("".join(raw_lines[i:j]))
            i = j
        else:
            atoms.append(line)
            i += 1

    chunks: list[str] = []
    buf = ""
    in_fence = False
    fence_header = "```"

    for atom in atoms:
        budget = max_chars - (FENCE_OVERHEAD if in_fence else 0)

        if buf and len(buf) + len(atom) > budget:
            emit = buf
            if in_fence:
                emit = buf.rstrip("\n") + "\n```\n"
            chunks.append(emit)
            buf = (fence_header + "\n") if in_fence else ""

        buf += atom

        for raw_line in atom.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("```"):
                if not in_fence:
                    in_fence = True
                    fence_header = stripped
                else:
                    in_fence = False

    if buf:
        chunks.append(buf)

    return [c for c in chunks if c.strip()]


def _pack_words(text: str, max_chars: int) -> list[str]:
    """Greedily pack whitespace-separated tokens up to max_chars. Breaks
    only at whitespace; a single token longer than max_chars is hard
    codepoint-split as a last resort."""
    out: list[str] = []
    buf = ""
    for tok in re.split(r"(\s+)", text):  # keeps separators as tokens
        if not tok:
            continue
        if len(tok) > max_chars:
            if buf.strip():
                out.append(buf)
            buf = ""
            for k in range(0, len(tok), max_chars):
                out.append(tok[k:k + max_chars])
            continue
        if len(buf) + len(tok) > max_chars:
            if buf.strip():
                out.append(buf)
            buf = tok if tok.strip() else ""
        else:
            buf += tok
    if buf.strip():
        out.append(buf)
    return out


def _pack_sentences(line: str, max_chars: int) -> list[str]:
    """Split an over-long single line on sentence boundaries, packing
    sentences up to max_chars. A single sentence over the limit falls
    through to word packing."""
    sentences = _SENTENCE_SPLIT_RE.split(line)
    out: list[str] = []
    buf = ""
    for s in sentences:
        if len(s) > max_chars:
            if buf.strip():
                out.append(buf)
            buf = ""
            out.extend(_pack_words(s, max_chars))
            continue
        cand = s if not buf else buf + " " + s
        if len(cand) > max_chars:
            if buf.strip():
                out.append(buf)
            buf = s
        else:
            buf = cand
    if buf.strip():
        out.append(buf)
    return out


def chunk_text(body: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Chunk arbitrary text so every piece is ≤max_chars codepoints.

    Structural split first (paragraphs/lines/tables/fences), then any
    still-oversized piece is split sentence -> word -> hard. Use this for
    conversational replies; ``chunk_structured`` alone leaves a long
    newline-free paragraph as one oversized chunk.
    """
    if not body:
        return []
    pieces = chunk_structured(body, max_chars)
    out: list[str] = []
    for piece in pieces:
        if len(piece) <= max_chars:
            out.append(piece)
        else:
            out.extend(_pack_sentences(piece, max_chars))
    return [c for c in out if c.strip()]

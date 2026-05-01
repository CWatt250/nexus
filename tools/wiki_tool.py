"""Phase 25 — Knowledge Garden tools.

Four LangGraph tools (wiki_query, wiki_ingest, wiki_update, wiki_create)
that let any agent path read and grow the wiki at ~/AI_Agent/wiki/.

The wiki has its own Chroma collection (`wiki`) so semantic hits aren't
drowned out by raw memory chunks. Slug + frontmatter matches are checked
before falling through to embeddings — they're cheaper and usually right.

See wiki/SCHEMA.md for the maintenance contract.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
from langchain_core.tools import tool

from tools.rag_tool import OllamaEmbeddingFunction, PERSIST_DIR

WIKI_ROOT = Path.home() / "AI_Agent" / "wiki"
SOURCES = WIKI_ROOT / "sources"
ENTITIES = WIKI_ROOT / "entities"
CONCEPTS = WIKI_ROOT / "concepts"
DECISIONS = WIKI_ROOT / "decisions"
LOG = WIKI_ROOT / "log.md"
INDEX = WIKI_ROOT / "index.md"
SCHEMA = WIKI_ROOT / "SCHEMA.md"

WIKI_COLLECTION = "wiki"
EXTRACTOR_TRIGGER_DIR = WIKI_ROOT / ".extractor_inbox"

CURATED_DIRS = (ENTITIES, CONCEPTS, DECISIONS)


def _ensure_dirs() -> None:
    for d in (SOURCES, ENTITIES, CONCEPTS, DECISIONS, EXTRACTOR_TRIGGER_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


# ── Frontmatter helpers ────────────────────────────────────────────────────
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Tolerant: missing/malformed → ({}, text)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    fm: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            fm[key] = [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()]
        elif val == "":
            fm[key] = []
        else:
            fm[key] = val.strip('"').strip("'")
    body = text[m.end():]
    return fm, body


def _serialize_frontmatter(fm: dict) -> str:
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ── Chroma collection ──────────────────────────────────────────────────────
_embed_fn = OllamaEmbeddingFunction()
_client: chromadb.api.ClientAPI | None = None
_collection = None


def _get_collection():
    """Lazy singleton — share Chroma persistence dir with rag_tool but
    use a separate collection so wiki hits don't get diluted."""
    global _client, _collection
    if _collection is None:
        PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(PERSIST_DIR))
        _collection = _client.get_or_create_collection(
            name=WIKI_COLLECTION,
            embedding_function=_embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _index_page(path: Path, fm: dict, body: str) -> None:
    """Add or replace a page in the wiki Chroma collection. Page id = relative path."""
    rel = str(path.relative_to(WIKI_ROOT))
    col = _get_collection()
    # delete-then-add gives us idempotent re-index. ignore errors if id absent.
    try:
        col.delete(ids=[rel])
    except Exception:
        pass
    text = (fm.get("name", "") + "\n" + fm.get("description", "") + "\n\n" + body).strip()
    if not text:
        return
    meta = {
        "path": rel,
        "name": str(fm.get("name", path.stem)),
        "type": str(fm.get("type", "")),
        "last_updated": str(fm.get("last_updated", _today())),
        "tags": ",".join(fm.get("tags", [])) if isinstance(fm.get("tags"), list) else str(fm.get("tags", "")),
    }
    col.add(documents=[text[:4000]], metadatas=[meta], ids=[rel])


def _reindex_all() -> int:
    """Walk curated dirs and (re)index every page. Returns count."""
    n = 0
    for d in CURATED_DIRS:
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            try:
                fm, body = _parse_frontmatter(p.read_text(encoding="utf-8"))
                _index_page(p, fm, body)
                n += 1
            except Exception:
                continue
    return n


# ── Search ─────────────────────────────────────────────────────────────────
def _slug_match(question: str) -> list[Path]:
    """Cheapest first: split the question, look for token matches in filenames."""
    tokens = [t for t in re.split(r"\W+", question.lower()) if len(t) >= 3]
    hits: list[tuple[int, Path]] = []
    for d in CURATED_DIRS:
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            stem = p.stem.lower()
            score = sum(1 for t in tokens if t in stem)
            if score:
                hits.append((score, p))
    hits.sort(key=lambda x: (-x[0], x[1].name))
    return [p for _, p in hits[:5]]


def _frontmatter_match(question: str) -> list[Path]:
    """Look for the question's tokens in frontmatter name/description/tags."""
    tokens = [t for t in re.split(r"\W+", question.lower()) if len(t) >= 3]
    if not tokens:
        return []
    hits: list[tuple[int, Path]] = []
    for d in CURATED_DIRS:
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            try:
                fm, _ = _parse_frontmatter(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            haystack = " ".join([
                str(fm.get("name", "")),
                str(fm.get("description", "")),
                ",".join(fm.get("tags", [])) if isinstance(fm.get("tags"), list) else str(fm.get("tags", "")),
            ]).lower()
            score = sum(1 for t in tokens if t in haystack)
            if score:
                hits.append((score, p))
    hits.sort(key=lambda x: (-x[0], x[1].name))
    return [p for _, p in hits[:5]]


def _semantic_match(question: str, k: int = 5) -> list[Path]:
    try:
        col = _get_collection()
        # If collection is empty (no docs yet), bail to avoid Chroma raising.
        if col.count() == 0:
            return []
        res = col.query(query_texts=[question], n_results=k)
    except Exception:
        return []
    out: list[Path] = []
    metas = (res.get("metadatas") or [[]])[0]
    for m in metas:
        rel = m.get("path") if isinstance(m, dict) else None
        if not rel:
            continue
        p = WIKI_ROOT / rel
        if p.exists():
            out.append(p)
    return out


def _format_hit(p: Path) -> str:
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return f"- {p.relative_to(WIKI_ROOT)} (unreadable)"
    fm, body = _parse_frontmatter(text)
    name = fm.get("name") or p.stem
    desc = fm.get("description") or body.strip().splitlines()[0] if body.strip() else ""
    last = fm.get("last_updated", "?")
    return f"### {name}\n_path:_ `wiki/{p.relative_to(WIKI_ROOT)}`  _updated:_ {last}\n\n{desc}\n"


@tool
def wiki_query(question: str, k: int = 5) -> str:
    """Search the Nexus knowledge garden (~/AI_Agent/wiki/) and return up to
    k matching pages. Searches by slug, frontmatter, then semantic similarity.

    Use this BEFORE answering questions about Colton, his projects (BidWatt,
    SubWatt, Argus), Nexus internals (dispatch, intent routing, phases), or
    any decision rationale. The wiki is the curated source of truth.

    Returns markdown-formatted hits with path, last_updated, and description.
    """
    _ensure_dirs()
    seen: set[Path] = set()
    ordered: list[Path] = []
    for finder in (_slug_match, _frontmatter_match, _semantic_match):
        for p in finder(question):
            if p in seen:
                continue
            seen.add(p)
            ordered.append(p)
            if len(ordered) >= k:
                break
        if len(ordered) >= k:
            break
    if not ordered:
        return "(no wiki hits — try `wiki_ingest` to add new info, or check wiki/index.md)"
    return "\n\n---\n\n".join(_format_hit(p) for p in ordered[:k])


# ── Ingest ─────────────────────────────────────────────────────────────────
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _fetch_url(url: str) -> tuple[str, str]:
    """Fetch a URL and return (descriptor, body_markdown). Best-effort:
    falls back to raw text if markitdown isn't available."""
    try:
        from tools import markitdown_tool  # noqa: PLC0415
        # markitdown_tool exposes either a `convert_to_markdown` or `markitdown_tool` callable
        fn = getattr(markitdown_tool, "_convert_to_markdown", None) \
            or getattr(markitdown_tool, "convert_to_markdown", None)
        if fn:
            md = fn(url) if not callable(getattr(fn, "invoke", None)) else fn.invoke({"source": url})
            descriptor = re.sub(r"\W+", "-", url.split("//", 1)[-1].split("/", 1)[0]).strip("-")[:40]
            return descriptor or "url", str(md)
    except Exception:
        pass
    # raw fallback
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        descriptor = re.sub(r"\W+", "-", url.split("//", 1)[-1].split("/", 1)[0]).strip("-")[:40]
        return descriptor or "url", body
    except Exception as exc:
        return "url-fetch-failed", f"<!-- fetch failed: {exc} -->\n{url}"


def _kebab(s: str, max_len: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:max_len] or "untitled"


def _trigger_extractor(source_path: Path) -> None:
    """Drop a tiny breadcrumb so workers/wiki_extractor.py knows there's a
    new source to process. Filesystem-as-queue keeps things simple."""
    try:
        EXTRACTOR_TRIGGER_DIR.mkdir(parents=True, exist_ok=True)
        (EXTRACTOR_TRIGGER_DIR / source_path.name).write_text(
            json.dumps({"source": str(source_path), "queued_at": _now_iso()}),
            encoding="utf-8",
        )
    except OSError:
        pass


@tool
def wiki_ingest(source: str, source_type: str = "manual") -> str:
    """Save a raw source into the knowledge garden's immutable layer.

    `source` may be a URL, a filesystem path, or raw text. `source_type`
    should be one of: dispatch_result, research, article, transcript,
    screenshot, pdf, manual. The file is named YYYY-MM-DD_<descriptor>.md
    and dropped into wiki/sources/. The wiki extractor worker is notified
    so it can update relevant entity / concept / decision pages.

    Returns the relative path of the new source file.
    """
    _ensure_dirs()
    today = _today()
    body: str
    descriptor: str
    source_url: str | None = None
    src = source.strip()

    if _URL_RE.match(src):
        source_url = src
        descriptor, body = _fetch_url(src)
        ext = ".md"
    elif Path(src).expanduser().exists() and Path(src).expanduser().is_file():
        path = Path(src).expanduser()
        body = path.read_text(encoding="utf-8", errors="replace")
        descriptor = _kebab(path.stem)
        ext = path.suffix or ".md"
    else:
        # raw text — descriptor is first line
        first = src.splitlines()[0] if src else "manual-note"
        descriptor = _kebab(first)
        body = src
        ext = ".md"

    descriptor = _kebab(descriptor)
    candidate = SOURCES / f"{today}_{descriptor}{ext}"
    n = 2
    while candidate.exists():
        candidate = SOURCES / f"{today}_{descriptor}-{n}{ext}"
        n += 1

    fm = {
        "ingested_at": _now_iso(),
        "source_type": source_type,
        "descriptor": descriptor,
    }
    if source_url:
        fm["source_url"] = source_url

    if ext == ".md":
        candidate.write_text(_serialize_frontmatter(fm) + "\n" + body, encoding="utf-8")
    else:
        # binary-ish — write body as-is, drop a sidecar .md with the frontmatter
        candidate.write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
        sidecar = candidate.with_suffix(candidate.suffix + ".meta.md")
        sidecar.write_text(_serialize_frontmatter(fm), encoding="utf-8")

    _trigger_extractor(candidate)
    rel = candidate.relative_to(WIKI_ROOT)
    return f"ingested → wiki/{rel} (extractor queued)"


# ── Update ─────────────────────────────────────────────────────────────────
def _resolve_page(page_path: str) -> Path | None:
    """Accept any of: 'wiki/entities/foo.md', 'entities/foo.md', 'foo',
    'foo.md'. Searches curated dirs by stem if just a name is given."""
    p = page_path.strip()
    candidates: list[Path] = []
    if p.startswith("wiki/"):
        candidates.append(Path.home() / "AI_Agent" / p)
    candidates.append(WIKI_ROOT / p)
    if "/" not in p:
        stem = p[:-3] if p.endswith(".md") else p
        for d in CURATED_DIRS:
            candidates.append(d / f"{stem}.md")
    for c in candidates:
        if c.exists():
            return c
    return None


def _append_log(line: str) -> None:
    if not LOG.exists():
        LOG.write_text("# Wiki Journal\n\n", encoding="utf-8")
    existing = LOG.read_text(encoding="utf-8")
    # insert just under the heading / separator block, preserving newest-first
    header_end = existing.find("---\n")
    if header_end == -1:
        LOG.write_text(existing + f"\n{line}\n", encoding="utf-8")
        return
    insert_at = existing.find("\n", header_end + 4) + 1
    new = existing[:insert_at] + f"\n{line}\n" + existing[insert_at:]
    LOG.write_text(new, encoding="utf-8")


@tool
def wiki_update(page_path: str, change_description: str, new_body: str | None = None) -> str:
    """Edit an existing wiki page. Bumps `last_updated` in frontmatter and
    appends a one-line entry to wiki/log.md describing the change.

    `page_path` accepts 'wiki/entities/foo.md', 'entities/foo.md', or just
    'foo' (will search by stem). `change_description` MUST be terse and
    factual — it goes into the journal verbatim. If `new_body` is given,
    it replaces the body below the frontmatter; otherwise the page is left
    unchanged and only the timestamp/log entry are written (useful for
    'I confirmed this is still current' touches).

    Returns the page's relative path on success.
    """
    _ensure_dirs()
    target = _resolve_page(page_path)
    if not target:
        return f"error: no wiki page matches `{page_path}`. Use wiki_create to make one."
    text = target.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    fm["last_updated"] = _today()
    if new_body is not None:
        body = new_body if new_body.endswith("\n") else new_body + "\n"
    target.write_text(_serialize_frontmatter(fm) + "\n" + body, encoding="utf-8")
    _index_page(target, fm, body)
    rel = target.relative_to(WIKI_ROOT)
    _append_log(f"{_today()} — {rel}: {change_description.strip()}")
    return f"updated wiki/{rel}"


# ── Create ─────────────────────────────────────────────────────────────────
def _index_md_add_line(line: str, section: str) -> None:
    """Append a bullet to `index.md` under the named section header."""
    if not INDEX.exists():
        return
    text = INDEX.read_text(encoding="utf-8")
    header = f"## {section}"
    h_idx = text.find(header)
    if h_idx == -1:
        # append at end as a loose addition
        INDEX.write_text(text.rstrip() + f"\n\n{header}\n\n{line}\n", encoding="utf-8")
        return
    # find next blank-line gap after the section's bullet list to insert before next ##
    next_h = text.find("\n## ", h_idx + len(header))
    insert_at = next_h if next_h != -1 else len(text)
    chunk = text[h_idx:insert_at].rstrip() + f"\n{line}\n\n"
    INDEX.write_text(text[:h_idx] + chunk + text[insert_at:].lstrip("\n"), encoding="utf-8")


@tool
def wiki_create(page_path: str, name: str, description: str, body: str,
                tags: list[str] | None = None) -> str:
    """Create a new wiki page with proper frontmatter and add a line to
    wiki/index.md.

    `page_path` should be 'entities/<slug>.md', 'concepts/<slug>.md', or
    'decisions/YYYY-MM-DD_<slug>.md'. `name` is the canonical display
    name. `description` is a one-line summary. `body` is the markdown
    body (no frontmatter — this tool writes it). `tags` is optional.

    Refuses to overwrite an existing page (use wiki_update for that).
    Returns the page's relative path.
    """
    _ensure_dirs()
    rel = page_path.strip().lstrip("/")
    if rel.startswith("wiki/"):
        rel = rel[len("wiki/"):]
    target = WIKI_ROOT / rel
    if target.exists():
        return f"error: wiki/{rel} already exists. Use wiki_update."
    if not any(str(target).startswith(str(d)) for d in CURATED_DIRS):
        return (f"error: page must live under entities/, concepts/, or decisions/. "
                f"Got: {rel}")
    target.parent.mkdir(parents=True, exist_ok=True)
    type_label = "entity" if target.parent == ENTITIES else \
                 "concept" if target.parent == CONCEPTS else "decision"
    fm = {
        "name": name,
        "description": description,
        "type": type_label,
        "last_updated": _today(),
        "sources": [],
        "tags": tags or [],
    }
    target.write_text(_serialize_frontmatter(fm) + "\n" + (body if body.endswith("\n") else body + "\n"),
                      encoding="utf-8")
    _index_page(target, fm, body)
    section = "Entities" if type_label == "entity" else \
              "Concepts" if type_label == "concept" else "Decisions"
    _index_md_add_line(f"- [{name}]({rel}) — {description}", section)
    _append_log(f"{_today()} — wiki/{rel}: created ({type_label}) — {description}")
    return f"created wiki/{rel}"


WIKI_TOOLS = [wiki_query, wiki_ingest, wiki_update, wiki_create]


# Convenience for one-off CLI use: `python -m tools.wiki_tool reindex`
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "reindex":
        n = _reindex_all()
        print(f"reindexed {n} wiki pages into Chroma collection '{WIKI_COLLECTION}'")
    elif len(sys.argv) >= 3 and sys.argv[1] == "query":
        print(wiki_query.invoke({"question": " ".join(sys.argv[2:])}))
    else:
        print("usage: python -m tools.wiki_tool {reindex | query <question>}")

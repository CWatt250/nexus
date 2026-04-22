"""Deep codebase indexer for Nexus.

`index_codebase` walks a git repo, reads every tracked source file,
extracts structural metadata (symbols / imports / deps / entry points /
routes / schemas), stores each file's preview + metadata in a dedicated
Chroma collection tagged by repo name, and writes a NEXUS.md summary at
the repo root.

`search_codebase`, `get_file_context`, and `list_repo_structure` expose
the indexed data for downstream tools (the autonomous coding agent in
particular)."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.utils import embedding_functions
from langchain_core.tools import tool

log = logging.getLogger("nexus.codebase")

CHROMA_DIR = Path.home() / "AI_Agent" / "chroma"
COLLECTION = "nexus-codebase"

MAX_FILES = 400
MAX_FILE_BYTES = 200_000
PREVIEW_BYTES = 3_500
SEARCH_K = 6

# File-type classification
CODE_EXTS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".lua",
    ".sh", ".bash", ".zsh", ".sql",
}
MARKUP_EXTS = {".html", ".htm", ".vue", ".svelte", ".astro"}
STYLE_EXTS = {".css", ".scss", ".sass", ".less"}
CONFIG_FILES = {
    "package.json", "requirements.txt", "pyproject.toml", "Cargo.toml",
    "go.mod", "composer.json", "Gemfile", "Dockerfile", "docker-compose.yml",
    "docker-compose.yaml", "vercel.json", "netlify.toml", "next.config.js",
    "next.config.ts", "vite.config.js", "vite.config.ts", "tsconfig.json",
    "tailwind.config.js", "tailwind.config.ts", ".env.example",
}
SKIP_DIRS = {"node_modules", "venv", ".venv", "__pycache__", ".git",
             "dist", "build", ".next", "target", "chroma"}

# ---------------------------------------------------------------------------
# Chroma
# ---------------------------------------------------------------------------

_embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)
_client = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _collection = _client.get_or_create_collection(
            name=COLLECTION, embedding_function=_embed_fn,
        )
    return _collection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _run(args: list[str], cwd: Path, timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 1, ""


def _git_tracked(repo: Path) -> list[Path]:
    rc, out = _run(["git", "ls-files"], repo)
    if rc != 0:
        return []
    files = []
    for ln in out.splitlines():
        p = (repo / ln).resolve()
        if p.exists() and p.is_file():
            if any(part in SKIP_DIRS for part in p.relative_to(repo).parts):
                continue
            files.append(p)
    return files[:MAX_FILES]


def _all_files(repo: Path) -> list[Path]:
    """Fallback for non-git dirs."""
    out = []
    for p in repo.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(repo).parts):
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        out.append(p)
        if len(out) >= MAX_FILES:
            break
    return out


def _classify(path: Path) -> str:
    n = path.name
    ext = path.suffix.lower()
    if n in CONFIG_FILES or ext in {".toml", ".yaml", ".yml"}:
        return "config"
    if ext in CODE_EXTS:
        return "code"
    if ext in MARKUP_EXTS:
        return "markup"
    if ext in STYLE_EXTS:
        return "style"
    if n.lower().startswith("readme"):
        return "docs"
    if ext in {".md", ".mdx", ".rst"}:
        return "docs"
    if ext in {".json"}:
        return "config"
    return "other"


def _detect_language(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".py": "python", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".go": "go",
        ".rs": "rust", ".java": "java", ".rb": "ruby", ".php": "php",
        ".html": "html", ".css": "css", ".scss": "scss",
        ".sh": "shell", ".bash": "shell", ".sql": "sql",
        ".vue": "vue", ".svelte": "svelte",
    }.get(ext, ext.lstrip(".") or "text")


def _safe_read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Symbol extraction (regex-based; good enough for summaries)
# ---------------------------------------------------------------------------

_PY_SYMBOLS = re.compile(r"^\s*(?:class|def|async\s+def)\s+([A-Za-z_][\w]*)", re.M)
_PY_IMPORTS = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w., ]+))", re.M)
_JS_SYMBOLS = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)",
    re.M,
)
_JS_IMPORTS = re.compile(r"^\s*(?:import|require)\s*\(?\s*['\"]([^'\"]+)['\"]", re.M)
_GO_SYMBOLS = re.compile(r"^\s*func\s+(?:\([^)]+\)\s+)?([A-Za-z_][\w]*)", re.M)
_RS_SYMBOLS = re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][\w]*)", re.M)
_URL_ROUTE = re.compile(
    r"""(?x)
    (?:@app\.(?:get|post|put|delete|patch|route)|router\.(?:get|post|put|delete|patch)|
       app\.(?:get|post|put|delete|patch)|express\.Router\(\)\.(?:get|post|put|delete|patch))
    \(\s*['"]([^'"]+)['"]
    """
)


def _extract_symbols(lang: str, text: str) -> dict:
    out = {"symbols": [], "imports": [], "routes": []}
    if not text:
        return out
    text = text[:MAX_FILE_BYTES]
    if lang == "python":
        out["symbols"] = list(dict.fromkeys(_PY_SYMBOLS.findall(text)))[:40]
        imps: list[str] = []
        for a, b in _PY_IMPORTS.findall(text):
            for part in (a, b):
                if not part:
                    continue
                for bit in part.split(","):
                    bit = bit.strip().split(" as ")[0]
                    if bit:
                        imps.append(bit)
        out["imports"] = list(dict.fromkeys(imps))[:30]
    elif lang in ("javascript", "typescript", "vue", "svelte"):
        out["symbols"] = list(dict.fromkeys(_JS_SYMBOLS.findall(text)))[:40]
        out["imports"] = list(dict.fromkeys(_JS_IMPORTS.findall(text)))[:30]
    elif lang == "go":
        out["symbols"] = list(dict.fromkeys(_GO_SYMBOLS.findall(text)))[:40]
    elif lang == "rust":
        out["symbols"] = list(dict.fromkeys(_RS_SYMBOLS.findall(text)))[:40]
    out["routes"] = list(dict.fromkeys(_URL_ROUTE.findall(text)))[:40]
    return out


# ---------------------------------------------------------------------------
# Project metadata
# ---------------------------------------------------------------------------

def _parse_package_json(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        "name": data.get("name", ""),
        "version": data.get("version", ""),
        "scripts": list((data.get("scripts") or {}).keys()),
        "deps": list((data.get("dependencies") or {}).keys()),
        "dev_deps": list((data.get("devDependencies") or {}).keys()),
        "type": data.get("type", ""),
    }


def _parse_pyproject(p: Path) -> dict:
    text = p.read_text(encoding="utf-8", errors="ignore")
    deps: list[str] = []
    for m in re.finditer(r'^\s*([a-zA-Z0-9_.\-]+)\s*=\s*"[^"]*"', text, re.M):
        name = m.group(1)
        if name in {"name", "version", "description", "python", "authors"}:
            continue
        deps.append(name)
    m = re.search(r"^\s*dependencies\s*=\s*\[(.*?)\]", text, re.M | re.S)
    if m:
        for dep in re.findall(r'"([^"]+)"', m.group(1)):
            deps.append(dep.split(">=")[0].split("==")[0].split("<")[0].strip())
    return {"deps": list(dict.fromkeys(deps))[:60]}


def _parse_requirements(p: Path) -> dict:
    deps = []
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        deps.append(re.split(r"[<>=!~\s]", ln, 1)[0])
    return {"deps": list(dict.fromkeys(deps))[:80]}


def _collect_project_meta(repo: Path, files: list[Path]) -> dict:
    meta: dict = {"languages": {}, "config_files": [], "entrypoints": [],
                  "test_files": [], "schemas": [], "routes": []}
    lang_counts: dict[str, int] = {}
    for f in files:
        cls = _classify(f)
        if cls == "config":
            meta["config_files"].append(str(f.relative_to(repo)))
        if cls == "code":
            lang = _detect_language(f)
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
        name = f.name.lower()
        if name in ("main.py", "__main__.py", "app.py", "server.py",
                    "index.js", "index.ts", "main.js", "main.ts",
                    "main.go", "main.rs", "manage.py", "cli.py"):
            meta["entrypoints"].append(str(f.relative_to(repo)))
        if ("test" in name and f.suffix in (".py", ".js", ".ts", ".tsx")
                or name.startswith("test_") or name.endswith("_test.py")):
            meta["test_files"].append(str(f.relative_to(repo)))
        if name.endswith(".sql") or "schema" in name or name.endswith(".prisma"):
            meta["schemas"].append(str(f.relative_to(repo)))
    meta["languages"] = dict(sorted(lang_counts.items(), key=lambda kv: -kv[1]))

    # Dependency files
    pkg = repo / "package.json"
    if pkg.exists():
        meta["package_json"] = _parse_package_json(pkg)
    py = repo / "pyproject.toml"
    if py.exists():
        meta["pyproject"] = _parse_pyproject(py)
    req = repo / "requirements.txt"
    if req.exists():
        meta["requirements"] = _parse_requirements(req)

    # GitHub remote
    rc, remote = _run(["git", "config", "--get", "remote.origin.url"], repo)
    if rc == 0 and remote.strip():
        meta["remote"] = remote.strip()
    return meta


# ---------------------------------------------------------------------------
# NEXUS.md
# ---------------------------------------------------------------------------

def _nexus_md(repo: Path, meta: dict, routes: list[str], symbols_per_file: dict[str, list[str]]) -> str:
    lines: list[str] = []
    lines.append(f"# NEXUS.md — {repo.name}\n")
    lines.append(f"_Auto-generated by Nexus codebase indexer on {time.strftime('%Y-%m-%d %H:%M:%S')}_\n")

    lines.append("## Stack")
    if meta.get("languages"):
        top = ", ".join(f"{k} ({n})" for k, n in list(meta["languages"].items())[:6])
        lines.append(f"- Languages by file count: {top}")
    pkg = meta.get("package_json") or {}
    if pkg:
        lines.append(f"- Node project: `{pkg.get('name','?')}` @ `{pkg.get('version','?')}`")
        if pkg.get("deps"):
            lines.append("- Runtime deps: " + ", ".join(f"`{d}`" for d in pkg["deps"][:20]))
        if pkg.get("scripts"):
            lines.append("- npm scripts: " + ", ".join(f"`{s}`" for s in pkg["scripts"]))
    py = meta.get("pyproject") or {}
    if py.get("deps"):
        lines.append("- Python deps (pyproject): " + ", ".join(f"`{d}`" for d in py["deps"][:20]))
    req = meta.get("requirements") or {}
    if req.get("deps"):
        lines.append("- Python deps (requirements.txt): " + ", ".join(f"`{d}`" for d in req["deps"][:20]))
    lines.append("")

    lines.append("## Key files")
    if meta.get("entrypoints"):
        lines.append("### Entry points")
        for e in meta["entrypoints"][:10]:
            syms = symbols_per_file.get(e, [])
            detail = (" — " + ", ".join(syms[:5])) if syms else ""
            lines.append(f"- `{e}`{detail}")
    if meta.get("config_files"):
        lines.append("### Configuration")
        for c in meta["config_files"][:12]:
            lines.append(f"- `{c}`")
    if meta.get("schemas"):
        lines.append("### Data / schemas")
        for s in meta["schemas"][:10]:
            lines.append(f"- `{s}`")
    if meta.get("test_files"):
        lines.append("### Tests")
        for t in meta["test_files"][:15]:
            lines.append(f"- `{t}`")
    lines.append("")

    if routes:
        lines.append("## API routes")
        for r in routes[:25]:
            lines.append(f"- `{r}`")
        lines.append("")

    lines.append("## Commands")
    if pkg.get("scripts"):
        for s in pkg["scripts"]:
            lines.append(f"- `npm run {s}`")
    if meta.get("test_files") or (repo / "pytest.ini").exists():
        lines.append("- `pytest` — run the Python test suite")
    if (repo / "Cargo.toml").exists():
        lines.append("- `cargo test` / `cargo run`")
    if (repo / "go.mod").exists():
        lines.append("- `go test ./...` / `go run .`")
    lines.append("")

    if meta.get("remote"):
        lines.append(f"## Remote\n- {meta['remote']}\n")

    lines.append("## Rules (edit me)")
    lines.append("- Respect the existing folder layout; don't shuffle files without reason.")
    lines.append("- Don't touch generated / build artifacts (`dist/`, `build/`, `.next/`).")
    lines.append("- Run the test command above before committing.")
    lines.append("- Match the existing style in the file you're editing.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Index / search / context
# ---------------------------------------------------------------------------

def _repo_tag(repo: Path) -> str:
    return repo.name


def _doc_id(repo: Path, file: Path) -> str:
    rel = file.relative_to(repo).as_posix()
    return f"codebase:{_repo_tag(repo)}:{rel}"


def _clear_repo(repo: Path) -> None:
    col = _get_collection()
    # Chroma's delete supports where filters.
    try:
        col.delete(where={"repo": _repo_tag(repo)})
    except Exception as exc:
        log.debug("collection delete where=repo failed: %s", exc)


def index_codebase_raw(repo_path: str) -> dict:
    """Index implementation returning a dict summary."""
    repo = _resolve(repo_path)
    if not repo.exists() or not repo.is_dir():
        return {"ok": False, "error": f"no such repo: {repo}"}

    files = _git_tracked(repo) if (repo / ".git").exists() else _all_files(repo)
    files = files[:MAX_FILES]
    if not files:
        return {"ok": False, "error": "no files to index"}

    _clear_repo(repo)
    col = _get_collection()

    docs: list[str] = []
    ids: list[str] = []
    metadatas: list[dict] = []
    routes: list[str] = []
    symbols_per_file: dict[str, list[str]] = {}

    for f in files:
        text = _safe_read(f)
        if not text:
            continue
        lang = _detect_language(f) if _classify(f) == "code" else ""
        symbols = _extract_symbols(lang, text) if lang else {"symbols": [], "imports": [], "routes": []}
        rel = f.relative_to(repo).as_posix()
        symbols_per_file[rel] = symbols["symbols"]
        routes.extend(symbols["routes"])

        header_parts = [f"FILE: {rel}", f"TYPE: {_classify(f)}"]
        if lang:
            header_parts.append(f"LANGUAGE: {lang}")
        if symbols["symbols"]:
            header_parts.append("SYMBOLS: " + ", ".join(symbols["symbols"][:20]))
        if symbols["imports"]:
            header_parts.append("IMPORTS: " + ", ".join(symbols["imports"][:20]))
        preview = text[:PREVIEW_BYTES]
        doc = "\n".join(header_parts) + "\n\n" + preview

        meta = {
            "repo": _repo_tag(repo),
            "path": rel,
            "type": _classify(f),
            "language": lang or "text",
            "bytes": f.stat().st_size,
            "symbols": ", ".join(symbols["symbols"][:20]),
            "imports": ", ".join(symbols["imports"][:20]),
        }
        docs.append(doc)
        ids.append(_doc_id(repo, f))
        metadatas.append(meta)

    # Batch add (Chroma accepts lists)
    if docs:
        col.add(documents=docs, metadatas=metadatas, ids=ids)

    meta = _collect_project_meta(repo, files)
    nexus_md = _nexus_md(repo, meta, routes, symbols_per_file)
    try:
        (repo / "NEXUS.md").write_text(nexus_md, encoding="utf-8")
    except OSError as exc:
        log.warning("could not write NEXUS.md: %s", exc)

    # Also index NEXUS.md itself so it shows up in semantic search
    try:
        col.add(
            documents=[f"NEXUS.md — {repo.name}\n\n{nexus_md[:PREVIEW_BYTES]}"],
            metadatas=[{"repo": _repo_tag(repo), "path": "NEXUS.md",
                        "type": "docs", "language": "markdown"}],
            ids=[f"codebase:{_repo_tag(repo)}:NEXUS.md#summary"],
        )
    except Exception:
        pass

    return {
        "ok": True,
        "repo": _repo_tag(repo),
        "path": str(repo),
        "indexed_files": len(docs),
        "total_files_seen": len(files),
        "languages": meta.get("languages", {}),
        "entrypoints": meta.get("entrypoints", []),
        "routes_found": len(routes),
        "nexus_md": str(repo / "NEXUS.md"),
    }


# ---------------------------------------------------------------------------
# LangGraph tools
# ---------------------------------------------------------------------------

@tool
def index_codebase(repo_path: str) -> str:
    """Deep-scan a git repo: read every tracked source file, extract
    functions/classes/imports/routes/deps, store each file's preview in
    Chroma RAG tagged with the repo name, and write a NEXUS.md at the
    repo root summarizing the stack, key files, and rules."""
    r = index_codebase_raw(repo_path)
    if not r.get("ok"):
        return f"ERROR: {r.get('error', 'unknown')}"
    lines = [
        f"Indexed {r['indexed_files']} files in {r['repo']} ({r['path']})",
        f"Languages: {r['languages']}",
        f"Entry points: {r['entrypoints']}",
        f"Routes found: {r['routes_found']}",
        f"NEXUS.md written to: {r['nexus_md']}",
    ]
    return "\n".join(lines)


@tool
def search_codebase(query: str, repo_path: Optional[str] = None, k: int = SEARCH_K) -> str:
    """Semantic search across indexed codebases. If `repo_path` is given,
    restrict results to that repo's index."""
    col = _get_collection()
    where = None
    if repo_path:
        where = {"repo": _resolve(repo_path).name}
    try:
        res = col.query(query_texts=[query], n_results=max(1, int(k)), where=where)
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    if not docs:
        return "(no matches)"
    out = []
    for i, d in enumerate(docs):
        meta = metas[i] if i < len(metas) else {}
        dist = dists[i] if i < len(dists) else None
        path = meta.get("path", "?")
        repo = meta.get("repo", "?")
        snippet = d[:600].replace("\n", " ")
        dist_s = f" dist={dist:.3f}" if isinstance(dist, (int, float)) else ""
        out.append(f"[{repo}] {path}{dist_s}\n  {snippet}")
    return "\n\n".join(out)


@tool
def get_file_context(file_path: str) -> str:
    """Return the file's content plus the list of repo files that import
    or reference its symbols (coarse — name-based)."""
    p = _resolve(file_path)
    if not p.exists():
        return f"ERROR: no such file: {p}"
    text = _safe_read(p)
    if not text:
        return "(empty / unreadable)"
    # Determine repo root
    repo = _find_repo(p)
    related: list[str] = []
    if repo is not None:
        col = _get_collection()
        # Use filename stem as a rough query — good enough for callers.
        stem = p.stem
        try:
            res = col.query(
                query_texts=[f"imports {stem}  uses {stem}"],
                n_results=8,
                where={"repo": _repo_tag(repo)},
            )
            metas = (res.get("metadatas") or [[]])[0]
            for m in metas:
                if not m:
                    continue
                rel = m.get("path")
                if rel and rel != p.relative_to(repo).as_posix():
                    related.append(rel)
        except Exception:
            pass

    head = f"FILE: {p}\n"
    if related:
        head += "RELATED (by symbol reference):\n  " + "\n  ".join(related[:8]) + "\n"
    body = text[:12_000]
    return head + "\n" + body


def _find_repo(path: Path) -> Optional[Path]:
    cur = path.parent if path.is_file() else path
    for _ in range(8):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


@tool
def list_repo_structure(repo_path: str, max_depth: int = 4) -> str:
    """Return a clean, indented tree of the repo (git-tracked files only
    when inside a git repo, otherwise every file outside build dirs)."""
    repo = _resolve(repo_path)
    if not repo.exists():
        return f"ERROR: no such repo: {repo}"
    files = _git_tracked(repo) if (repo / ".git").exists() else _all_files(repo)
    tree: dict = {}
    for f in files:
        parts = f.relative_to(repo).parts
        if len(parts) > max_depth:
            parts = parts[:max_depth] + ("…",)
        node = tree
        for i, part in enumerate(parts):
            node = node.setdefault(part, {})
    lines: list[str] = [repo.name + "/"]

    def _render(node: dict, prefix: str = ""):
        entries = sorted(node.items())
        for i, (name, child) in enumerate(entries):
            last = i == len(entries) - 1
            branch = "└── " if last else "├── "
            lines.append(f"{prefix}{branch}{name}" + ("/" if child else ""))
            if child:
                _render(child, prefix + ("    " if last else "│   "))

    _render(tree)
    return "\n".join(lines[:400])


CODEBASE_TOOLS = [index_codebase, search_codebase, get_file_context, list_repo_structure]

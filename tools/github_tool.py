"""GitHub tools for Nexus — direct PyGithub integration.

These tools work without MCP as a fallback. Auth reads from:
  1. GITHUB_TOKEN in the process environment, else
  2. GITHUB_TOKEN in ~/AI_Agent/.env
If neither is set, every tool returns a clear "no token" message instead
of raising so the agent can recover gracefully."""
from __future__ import annotations

import base64
import os
from pathlib import Path

from langchain_core.tools import tool

ENV_FILE = Path.home() / "AI_Agent" / ".env"

_client = None
_client_error: str | None = None


def _load_env_file() -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file. Lines starting with # are
    ignored; unquoted values are taken as-is."""
    out: dict[str, str] = {}
    if not ENV_FILE.exists():
        return out
    try:
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            out[k.strip()] = v
    except OSError:
        pass
    return out


def _token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if token:
        return token
    env = _load_env_file()
    return env.get("GITHUB_TOKEN") or env.get("GITHUB_PERSONAL_ACCESS_TOKEN") or None


def _get_client():
    """Lazy singleton. Returns the PyGithub client, or raises RuntimeError
    if no token is configured."""
    global _client, _client_error
    if _client is not None:
        return _client
    if _client_error is not None:
        raise RuntimeError(_client_error)
    token = _token()
    if not token:
        _client_error = "GITHUB_TOKEN not set (check ~/AI_Agent/.env)"
        raise RuntimeError(_client_error)
    from github import Auth, Github
    _client = Github(auth=Auth.Token(token))
    return _client


def _repo(full_name: str):
    return _get_client().get_repo(full_name)


def _err(exc: Exception) -> str:
    return f"ERROR: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def github_create_repo(name: str, private: bool = True, description: str = "") -> str:
    """Create a new repository under the authenticated GitHub user.

    Args:
        name: repository name (no owner prefix).
        private: True for private, False for public. Defaults to private.
        description: optional short description.
    Returns the repository's full_name and HTML URL."""
    try:
        user = _get_client().get_user()
        repo = user.create_repo(name=name, private=private, description=description or "")
    except Exception as exc:
        return _err(exc)
    return f"created {repo.full_name}\nurl: {repo.html_url}"


@tool
def github_list_repos(visibility: str = "all", limit: int = 20) -> str:
    """List the authenticated user's repositories.

    Args:
        visibility: "all", "public", or "private".
        limit: max number of repos to return (default 20).
    Returns one repo per line: full_name — description."""
    try:
        user = _get_client().get_user()
        repos = user.get_repos(visibility=visibility)
    except Exception as exc:
        return _err(exc)
    lines = []
    for i, r in enumerate(repos):
        if i >= max(1, int(limit)):
            break
        lines.append(f"{r.full_name} — {(r.description or '').strip()}")
    return "\n".join(lines) if lines else "(no repos)"


@tool
def github_create_issue(repo: str, title: str, body: str = "") -> str:
    """Open a new issue on a repo.

    Args:
        repo: full repo name ("owner/name").
        title: issue title.
        body: issue body (markdown).
    Returns the issue number and URL."""
    try:
        issue = _repo(repo).create_issue(title=title, body=body or "")
    except Exception as exc:
        return _err(exc)
    return f"#{issue.number} {issue.html_url}"


@tool
def github_list_issues(repo: str, state: str = "open", limit: int = 20) -> str:
    """List issues on a repo.

    Args:
        repo: full repo name ("owner/name").
        state: "open", "closed", or "all".
        limit: max issues to return.
    Returns one issue per line: #num [state] title — url"""
    try:
        issues = _repo(repo).get_issues(state=state)
    except Exception as exc:
        return _err(exc)
    lines = []
    for i, it in enumerate(issues):
        if i >= max(1, int(limit)):
            break
        lines.append(f"#{it.number} [{it.state}] {it.title} — {it.html_url}")
    return "\n".join(lines) if lines else "(no issues)"


@tool
def github_create_pr(repo: str, title: str, head: str, base: str = "main", body: str = "") -> str:
    """Open a pull request on a repo.

    Args:
        repo: full repo name ("owner/name").
        title: PR title.
        head: source branch (or "owner:branch" for cross-fork).
        base: target branch (default "main").
        body: PR description (markdown).
    Returns the PR number and URL."""
    try:
        pr = _repo(repo).create_pull(title=title, head=head, base=base, body=body or "")
    except Exception as exc:
        return _err(exc)
    return f"PR #{pr.number} {pr.html_url}"


@tool
def github_get_file(repo: str, path: str, ref: str = "") -> str:
    """Read a file from a GitHub repo.

    Args:
        repo: full repo name ("owner/name").
        path: path to the file in the repo.
        ref: optional branch / commit / tag (defaults to the repo's default branch).
    Returns the decoded text content (or an error if the path is a directory
    or binary)."""
    try:
        r = _repo(repo)
        content = r.get_contents(path, ref=ref) if ref else r.get_contents(path)
    except Exception as exc:
        return _err(exc)
    if isinstance(content, list):
        return "ERROR: path is a directory — " + ", ".join(c.name for c in content)
    try:
        raw = content.decoded_content
        return raw.decode("utf-8", errors="replace")
    except Exception as exc:
        return _err(exc)


@tool
def github_commit_file(
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str = "main",
    approve: bool = False,
) -> str:
    """Create or update a single file in a GitHub repo on the given branch.

    Args:
        repo: full repo name ("owner/name").
        path: path within the repo.
        content: new file content (utf-8 text).
        message: commit message.
        branch: branch to commit onto (default "main").
        approve: must be True to actually push. Default False returns a
                 dry-run preview so the model never silently ships a
                 commit without explicit confirmation.
    Returns the commit SHA and HTML URL."""
    if not approve:
        return (
            "DRY-RUN: github_commit_file not executed.\n"
            f"repo: {repo}\nbranch: {branch}\npath: {path}\n"
            f"message: {message}\ncontent_bytes: {len(content)}\n"
            "to actually push, call again with approve=True."
        )
    try:
        r = _repo(repo)
        try:
            existing = r.get_contents(path, ref=branch)
            if isinstance(existing, list):
                return "ERROR: path is a directory"
            result = r.update_file(
                path=path,
                message=message,
                content=content,
                sha=existing.sha,
                branch=branch,
            )
        except Exception:
            result = r.create_file(
                path=path,
                message=message,
                content=content,
                branch=branch,
            )
    except Exception as exc:
        return _err(exc)
    commit = result.get("commit") if isinstance(result, dict) else None
    sha = getattr(commit, "sha", "?") if commit else "?"
    url = getattr(commit, "html_url", "") if commit else ""
    return f"committed {path}@{sha[:8]} {url}".strip()


GITHUB_TOOLS = [
    github_create_repo,
    github_list_repos,
    github_create_issue,
    github_list_issues,
    github_create_pr,
    github_get_file,
    github_commit_file,
]

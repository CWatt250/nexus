"""GitHub tools for Nexus — direct PyGithub integration.

Token resolution priority (highest first):
  1. GITHUB_PAT in ~/AI_Agent/config/secrets.yaml  ← fine-grained PAT
  2. GITHUB_TOKEN / GITHUB_PERSONAL_ACCESS_TOKEN in env
  3. GITHUB_TOKEN / GITHUB_PERSONAL_ACCESS_TOKEN in ~/AI_Agent/.env

When a token is present, all calls use authenticated mode (Bearer auth
via PyGithub's Auth.Token, which sets the Authorization header for
every request). When no token is found, the client falls back to the
PyGithub anonymous client — public-only access, sharply lower rate
limits — and a single warning is logged so the operator notices.

Token values never reach stdout / journalctl: every error string runs
through core.secrets.redact() before being returned to the agent.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from langchain_core.tools import tool

# Allow `python tools/github_tool.py` direct invocation for unit tests.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import secrets  # noqa: E402

log = logging.getLogger("nexus.github_tool")

_client = None
_client_anonymous = False  # True when no token was configured at boot
_warned_anonymous = False


def _token() -> str | None:
    """Resolve the GitHub token by priority. secrets.yaml's GITHUB_PAT
    wins over .env's GITHUB_TOKEN so the new fine-grained PAT takes
    over without editing the legacy slot."""
    return (
        secrets.get("GITHUB_PAT")
        or secrets.get("GITHUB_TOKEN")
        or secrets.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    )


def _get_client():
    """Lazy singleton. Always returns a PyGithub Github() instance —
    authenticated when a token exists, anonymous otherwise. Anonymous
    mode logs a one-time warning and lets public-only calls still work."""
    global _client, _client_anonymous, _warned_anonymous
    if _client is not None:
        return _client
    from github import Auth, Github  # noqa: PLC0415
    token = _token()
    if token:
        _client = Github(auth=Auth.Token(token))
        _client_anonymous = False
    else:
        _client = Github()  # anonymous, public-only
        _client_anonymous = True
        if not _warned_anonymous:
            log.warning(
                "github_tool: no GITHUB_PAT / GITHUB_TOKEN found — "
                "running in anonymous mode (public repos only, "
                "60 req/h rate limit)."
            )
            _warned_anonymous = True
    return _client


def _reset_client() -> None:
    """Drop the cached client + reset the warning latch. Used by
    secrets.reload() callers and the unit test."""
    global _client, _client_anonymous, _warned_anonymous
    _client = None
    _client_anonymous = False
    _warned_anonymous = False


def _repo(full_name: str):
    return _get_client().get_repo(full_name)


def _err(exc: Exception) -> str:
    """Format an exception message safely. Always pass through redact()
    in case PyGithub's error string echoes the bearer token (rare on
    PyGithub but it has happened in older versions)."""
    msg = f"ERROR: {type(exc).__name__}: {exc}"
    return secrets.redact(msg)


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

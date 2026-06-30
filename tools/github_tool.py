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


def _gh_cli_token() -> str | None:
    """Token from the system `gh` CLI, which is interactively authed and
    auto-refreshes. Preferred because the stored PAT was found expired in the
    2026-06 audit (all calls 401'd). Falls back to secrets if gh isn't authed."""
    try:
        import subprocess  # noqa: PLC0415
        out = subprocess.run(["gh", "auth", "token"], capture_output=True,
                             text=True, timeout=5)
        tok = (out.stdout or "").strip()
        return tok or None
    except Exception:
        return None


def _token() -> str | None:
    """Resolve the GitHub token. Prefer the live `gh` CLI token (auto-
    refreshing), then secrets.yaml's GITHUB_PAT / GITHUB_TOKEN."""
    return (
        _gh_cli_token()
        or secrets.get("GITHUB_PAT")
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


@tool
def github_list_my_repos(visibility: str = "all", limit: int = 50) -> str:
    """List ALL repos the authenticated GitHub user can access (public + private).

    Use this for 'my repos', 'my private repos', 'list my repos'. This is
    the PAT-aware tool — it only returns useful results when GITHUB_PAT
    is configured. With no token, it falls through to the same anonymous
    mode (which can't see private repos at all) and reports clearly.

    Args:
        visibility: 'all' (default), 'public', or 'private'.
        limit: max repos to return (default 50).
    Returns one repo per line: '<full_name> [private?] <description>'."""
    visibility = (visibility or "all").lower().strip()
    if visibility not in ("all", "public", "private"):
        return f"ERROR: visibility must be all|public|private (got {visibility!r})"
    try:
        client = _get_client()
        if _client_anonymous:
            return (
                "ERROR: github_list_my_repos requires authentication. "
                "Add GITHUB_PAT to ~/AI_Agent/config/secrets.yaml."
            )
        user = client.get_user()
        repos = user.get_repos(visibility=visibility, sort="updated")
    except Exception as exc:
        return _err(exc)
    lines: list[str] = []
    private_count = 0
    public_count = 0
    for i, r in enumerate(repos):
        if i >= max(1, int(limit)):
            break
        marker = "🔒" if r.private else "🌐"
        if r.private:
            private_count += 1
        else:
            public_count += 1
        desc = (r.description or "").strip()
        lines.append(f"{marker} {r.full_name} — {desc}" if desc else f"{marker} {r.full_name}")
    if not lines:
        return f"(no repos with visibility={visibility})"
    summary = f"{len(lines)} repo(s) — {public_count} public, {private_count} private:"
    return summary + "\n" + "\n".join(lines)


@tool
def github_auth_status() -> str:
    """Report GitHub auth state: who, what scopes, rate limit, expiry.

    Returns 'Authenticated as <user>, scopes: <list>, rate limit:
    <remaining>/<limit>, expires: <date>' when a token is configured;
    'Anonymous (public-only)' when not. Useful for debugging private-repo
    access and for the agent to know what it can do before attempting a
    call that needs a write scope.
    """
    try:
        client = _get_client()
    except Exception as exc:
        return _err(exc)
    if _client_anonymous:
        try:
            rl = client.get_rate_limit().core
            return (
                "Anonymous (public-only). "
                f"Rate limit: {rl.remaining}/{rl.limit}, resets at {rl.reset.isoformat()}"
            )
        except Exception as exc:
            return f"Anonymous (public-only). Rate-limit lookup failed: {_err(exc)}"
    try:
        user = client.get_user()
        login = user.login
    except Exception as exc:
        return f"Token rejected: {_err(exc)}"
    # Scopes + token expiration are surfaced as response headers, not
    # body fields — pull them off the most recent connection's last
    # response. PyGithub exposes these via __requester._Requester__connection
    # in some versions but they're not part of the public API; instead,
    # do a low-level GET /user and read response headers ourselves.
    scopes = "(unknown)"
    expires = "(no expiry header)"
    try:
        import requests  # noqa: PLC0415
        token = _token() or ""
        if token:
            r = requests.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=10,
            )
            scopes = r.headers.get("x-oauth-scopes", "") or "(fine-grained PAT — no classic scopes)"
            expires = r.headers.get("github-authentication-token-expiration", "(no expiry)")
    except Exception as exc:
        scopes = f"(scope lookup failed: {type(exc).__name__})"
    try:
        rl = client.get_rate_limit().core
        rate = f"{rl.remaining}/{rl.limit} (resets {rl.reset.isoformat()})"
    except Exception:
        rate = "(rate-limit lookup failed)"
    return (
        f"Authenticated as {login}\n"
        f"  scopes: {scopes}\n"
        f"  rate limit: {rate}\n"
        f"  token expires: {expires}"
    )


GITHUB_TOOLS = [
    github_auth_status,
    github_list_my_repos,
    github_create_repo,
    github_list_repos,
    github_create_issue,
    github_list_issues,
    github_create_pr,
    github_get_file,
    github_commit_file,
]

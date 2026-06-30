"""Sandboxed execution tool (G4) — run untrusted/agent-generated code in a
bubblewrap filesystem sandbox (read-only system, writable only /tmp + the
workspace). Backed by safety.sandbox.run_sandboxed."""
from __future__ import annotations

from langchain_core.tools import tool

from safety.sandbox import run_sandboxed


@tool
def sandbox_exec(command: str, workspace: str = "") -> str:
    """Run a shell command in an ISOLATED bubblewrap sandbox: the whole system
    is read-only, only /tmp and the workspace dir are writable. Use this for
    UNTRUSTED or agent-generated code so a mistake can't damage the system.
    (Network is NOT isolated on this box.) Returns output, or an enablement
    hint if the sandbox isn't switched on yet.

    Args:
        command: shell command to run sandboxed.
        workspace: writable dir (default: the Nexus repo).
    """
    res = run_sandboxed(command, workspace=workspace or None)
    if res.get("blocked"):
        return res.get("stderr") or res.get("reason") or "sandbox blocked"
    rc = res.get("returncode")
    tag = "timed out" if res.get("timed_out") else f"exit {rc}"
    out = (res.get("stdout") or "")[:3000]
    err = (res.get("stderr") or "")[:1000]
    return f"[sandboxed · {tag}]\n{out}" + (f"\n-- stderr --\n{err}" if err else "")


SANDBOX_TOOLS = [sandbox_exec]

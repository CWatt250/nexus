"""Capability inventory tool — returns TOOLS.md content.

Lets the agent answer "what can you do" / "what tools do you have" /
"do you have access to X" from the live tool inventory rather than
hallucinating from training data.
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

ROOT = Path(__file__).resolve().parent.parent
TOOLS_MD = ROOT / "TOOLS.md"


@tool
def list_capabilities() -> str:
    """Return Nexus's full tool inventory (TOOLS.md content).

    Use this whenever the user asks what you can do, what tools you have,
    or whether you have access to something — answer from the actual
    inventory, never guess or deny capability.
    """
    if not TOOLS_MD.exists():
        return (
            "TOOLS.md missing — run "
            "`~/AI_Agent/venv/bin/python3 scripts/generate_tools_md.py` "
            "to regenerate."
        )
    return TOOLS_MD.read_text(encoding="utf-8")


CAPABILITIES_TOOLS = [list_capabilities]

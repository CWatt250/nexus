#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Nexus MCP server.

Exposes all Nexus LangChain tools as MCP tools over stdio so MCP clients
(Claude Code, other agents) can invoke them directly.

Run with:  python3 ~/AI_Agent/mcp/server.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mcp.types as mcp_types  # noqa: E402
from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402

# Importing nexus would build a LangGraph agent (and load models). We only
# need the TOOLS list, so import the tool modules directly.
from tools.browser_tool import browser_tool  # noqa: E402
from tools.file_tool import (  # noqa: E402
    file_edit_tool,
    file_read_tool,
    file_write_tool,
)
from tools.brave_search_tool import brave_search, brave_search_news  # noqa: E402
from tools.github_tool import GITHUB_TOOLS  # noqa: E402
from tools.markitdown_tool import markitdown_tool  # noqa: E402
from tools.mem0_tool import mem0_add, mem0_search  # noqa: E402
from tools.rag_tool import memory_add, memory_search  # noqa: E402
from tools.search_tool import glob_tool, grep_tool  # noqa: E402
from tools.terminal_tool import terminal  # noqa: E402
from tools.tts_tool import tts_save, tts_speak  # noqa: E402
from tools.whisper_tool import whisper_record, whisper_transcribe  # noqa: E402
from tools.codebase_tool import CODEBASE_TOOLS  # noqa: E402
from tools.test_runner_tool import TEST_RUNNER_TOOLS  # noqa: E402
from tools.diff_tool import DIFF_TOOLS  # noqa: E402
from tools.coding_agent import CODING_AGENT_TOOLS  # noqa: E402

TOOLS = [
    terminal,
    file_read_tool,
    file_write_tool,
    file_edit_tool,
    glob_tool,
    grep_tool,
    browser_tool,
    memory_search,
    memory_add,
    markitdown_tool,
    mem0_add,
    mem0_search,
    *GITHUB_TOOLS,
    brave_search,
    brave_search_news,
    whisper_record,
    whisper_transcribe,
    tts_speak,
    tts_save,
    *CODEBASE_TOOLS,
    *TEST_RUNNER_TOOLS,
    *DIFF_TOOLS,
    *CODING_AGENT_TOOLS,
]

# stderr-only logging — stdout is reserved for MCP framing.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s nexus-mcp-server %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("nexus.mcp.server")

server = Server("nexus")

_BY_NAME = {t.name: t for t in TOOLS}


def _schema_for(tool) -> dict:
    """Pull the JSON schema from a LangChain tool's args pydantic model.
    Strip the `title` field so MCP clients get a clean schema."""
    try:
        schema = tool.args_schema.model_json_schema()
    except Exception:
        schema = {"type": "object", "properties": {}}
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    if "type" not in schema:
        schema["type"] = "object"
    return schema


@server.list_tools()
async def handle_list_tools() -> list[mcp_types.Tool]:
    out = []
    for t in TOOLS:
        out.append(
            mcp_types.Tool(
                name=t.name,
                description=(t.description or "").strip(),
                inputSchema=_schema_for(t),
            )
        )
    return out


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[mcp_types.TextContent]:
    tool = _BY_NAME.get(name)
    if tool is None:
        return [mcp_types.TextContent(type="text", text=f"ERROR: unknown tool {name!r}")]
    log.info("invoke %s args=%s", name, list((arguments or {}).keys()))
    try:
        # LangChain tool.invoke is sync; offload so we don't block the event loop.
        result = await asyncio.to_thread(tool.invoke, arguments or {})
    except Exception as exc:
        log.exception("tool %s failed", name)
        text = f"ERROR: {type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"
        return [mcp_types.TextContent(type="text", text=text)]
    if not isinstance(result, str):
        result = str(result)
    return [mcp_types.TextContent(type="text", text=result)]


async def main() -> None:
    log.info("nexus-mcp-server starting; exposing %d tools", len(TOOLS))
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

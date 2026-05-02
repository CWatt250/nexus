#!/home/cwatt250/AI_Agent/venv/bin/python3
"""Nexus MCP client — connect to external MCP servers listed in
~/AI_Agent/mcp/servers.json and expose their tools as LangChain tools so
the LangGraph agent can invoke them.

Each configured server runs in its own persistent background thread with
its own asyncio loop; tool invocations are marshalled in through
`asyncio.run_coroutine_threadsafe`. This keeps the model/langgraph side
fully synchronous while the MCP protocol stays async underneath."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import threading
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

from mcp import ClientSession, StdioServerParameters  # installed mcp SDK
from mcp.client.stdio import stdio_client

SERVERS_FILE = Path.home() / "AI_Agent" / "mcp" / "servers.json"
STARTUP_TIMEOUT = 30
CALL_TIMEOUT = 120

log = logging.getLogger("nexus.mcp.client")

_JSON_TO_PY = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class _Worker:
    """Keeps one MCP stdio server alive on a dedicated asyncio loop thread."""

    def __init__(self, name: str, command: str, env: dict | None) -> None:
        self.name = name
        self.command = command
        self.env = env or {}
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.error: str | None = None
        self.tools: list[Any] = []
        self.session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._thread = threading.Thread(target=self._run, name=f"mcp-{name}", daemon=True)

    def start(self) -> None:
        self._thread.start()
        if not self.ready.wait(timeout=STARTUP_TIMEOUT):
            self.error = self.error or f"startup timed out after {STARTUP_TIMEOUT}s"

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception as exc:
            log.exception("worker %s crashed: %s", self.name, exc)
            self.error = f"{type(exc).__name__}: {exc}"
            self.ready.set()

    async def _main(self) -> None:
        parts = shlex.split(self.command)
        if not parts:
            self.error = "empty command"
            self.ready.set()
            return
        # Resolve first token on PATH when it's a bare name.
        resolved = shutil.which(parts[0]) or parts[0]
        params = StdioServerParameters(
            command=resolved,
            args=parts[1:],
            env={**os.environ, **self.env},
        )
        stack = AsyncExitStack()
        self._stack = stack
        try:
            reader, writer = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(reader, writer))
            await session.initialize()
            listing = await session.list_tools()
            self.session = session
            self.tools = list(listing.tools or [])
            self.ready.set()
            # Block forever so the context manager stays open.
            await asyncio.Event().wait()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.warning("MCP server %s failed during startup: %s", self.name, self.error)
            self.ready.set()
        finally:
            try:
                await stack.aclose()
            except Exception:
                pass

    def call(self, tool_name: str, arguments: dict) -> str:
        if not self.session:
            return f"ERROR: MCP server {self.name!r} is not connected ({self.error or 'no session'})"
        coro = self.session.call_tool(tool_name, arguments)
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            result = fut.result(timeout=CALL_TIMEOUT)
        except Exception as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"
        parts: list[str] = []
        for c in getattr(result, "content", []) or []:
            text = getattr(c, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(str(c))
        if getattr(result, "isError", False):
            return f"ERROR:\n" + "\n".join(parts)
        return "\n".join(parts) if parts else ""


def _model_from_schema(schema: dict, tool_fullname: str):
    props = (schema or {}).get("properties", {}) or {}
    required = set((schema or {}).get("required", []) or [])
    fields: dict[str, tuple[type, Any]] = {}
    for name, spec in props.items():
        if not isinstance(spec, dict):
            spec = {}
        json_type = spec.get("type", "string")
        if isinstance(json_type, list):
            json_type = next((t for t in json_type if t != "null"), "string")
        py_type = _JSON_TO_PY.get(json_type, str)
        default: Any = ... if name in required else None
        desc = spec.get("description", "") or ""
        fields[name] = (py_type, Field(default, description=desc))
    safe = tool_fullname.replace("-", "_").replace(".", "_")
    return create_model(f"Args_{safe}", **fields)


def _wrap_as_langchain_tool(worker: _Worker, mcp_tool) -> StructuredTool:
    fullname = f"{worker.name}__{mcp_tool.name}"
    schema = mcp_tool.inputSchema or {"type": "object", "properties": {}}
    ArgsModel = _model_from_schema(schema, fullname)

    def _invoke(**kwargs: Any) -> str:
        # Drop None kwargs so optional params don't confuse strict servers.
        clean = {k: v for k, v in kwargs.items() if v is not None}
        return worker.call(mcp_tool.name, clean)

    description = (mcp_tool.description or "").strip()
    if not description:
        description = f"MCP tool {mcp_tool.name} from server {worker.name}"
    return StructuredTool.from_function(
        func=_invoke,
        name=fullname,
        description=description,
        args_schema=ArgsModel,
    )


_LOADED_WORKERS: list[_Worker] = []


def load_mcp_tools(*, config_path: Path = SERVERS_FILE) -> list[StructuredTool]:
    """Spawn every enabled MCP server in `servers.json` and return the
    aggregated LangChain tool list. Safe to call once at Nexus startup."""
    if not config_path.exists():
        log.info("no %s found; skipping MCP client", config_path)
        return []
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("%s is not valid JSON: %s", config_path, exc)
        return []
    servers = config.get("servers", []) or []
    out: list[StructuredTool] = []
    for cfg in servers:
        if not isinstance(cfg, dict):
            continue
        if cfg.get("enabled") is False:
            continue
        name = cfg.get("name") or "?"
        command = cfg.get("command")
        env = cfg.get("env") or {}
        # Skip servers with empty-value env placeholders (e.g. GITHUB_TOKEN="")
        missing_env = [k for k, v in env.items() if v == ""]
        if missing_env and cfg.get("skip_if_missing_env", True):
            log.info("skipping MCP server %s — env not set: %s", name, missing_env)
            continue
        if not command:
            continue
        log.info("starting MCP server %s: %s", name, command)
        t0 = time.time()
        worker = _Worker(name, command, env)
        worker.start()
        if worker.error:
            log.warning("MCP server %s not loaded: %s", name, worker.error)
            continue
        elapsed = time.time() - t0
        log.info("MCP server %s ready in %.2fs with %d tools", name, elapsed, len(worker.tools))
        _LOADED_WORKERS.append(worker)
        allowed = cfg.get("allowed_tools")
        if allowed is not None:
            allowed_set = {str(x) for x in allowed}
            filtered = [t for t in worker.tools if t.name in allowed_set]
            missing = allowed_set - {t.name for t in worker.tools}
            if missing:
                log.warning("MCP %s: allowed_tools not found on server: %s", name, sorted(missing))
            log.info("MCP %s: filtered %d tools -> %d via allowed_tools", name, len(worker.tools), len(filtered))
            tools_to_wrap = filtered
        else:
            tools_to_wrap = worker.tools
        for t in tools_to_wrap:
            try:
                out.append(_wrap_as_langchain_tool(worker, t))
            except Exception as exc:
                log.warning("could not wrap MCP tool %s/%s: %s", name, t.name, exc)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    tools = load_mcp_tools()
    print(f"loaded {len(tools)} MCP tools")
    for t in tools:
        print(f"  - {t.name}: {(t.description or '')[:80]}")

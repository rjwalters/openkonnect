"""The MCP server: exactly two tools, ``session_status`` and ``board_summary``."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from . import __version__
from .session import Session
from .tools import board_summary as board_summary_mod
from .tools import session_status as session_status_mod

INSTRUCTIONS = (
    "openkonnect drives a running KiCad 10 through its IPC API. Call session_status "
    "first to see whether KiCad is reachable and what is open. Failures are "
    "structured: error.code, error.message (what to do), error.retryable, "
    "error.details. Every call is time-bounded; see meta.timeout_s."
)

_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)


def _to_result(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2, default=str))],
        structuredContent=payload,
        isError=not payload.get("ok", False),
    )


def build_server(session: Session | None = None) -> FastMCP:
    session = session or Session()
    mcp = FastMCP("openkonnect", instructions=INSTRUCTIONS)
    mcp._mcp_server.version = __version__

    async def run(body: Callable[[Session], dict[str, Any]]) -> CallToolResult:
        payload = await anyio.to_thread.run_sync(
            lambda: session.run_tool(body), abandon_on_cancel=True
        )
        return _to_result(payload)

    @mcp.tool(
        name="session_status",
        description=session_status_mod.DESCRIPTION,
        annotations=_READ_ONLY,
        structured_output=False,
    )
    async def session_status() -> CallToolResult:
        return await run(session_status_mod.session_status)

    @mcp.tool(
        name="board_summary",
        description=board_summary_mod.DESCRIPTION,
        annotations=_READ_ONLY,
        structured_output=False,
    )
    async def board_summary() -> CallToolResult:
        return await run(board_summary_mod.board_summary)

    return mcp

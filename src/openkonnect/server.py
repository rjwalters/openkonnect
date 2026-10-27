"""The MCP server.

Read-only: ``session_status`` (SES-01) and ``board_summary`` (LAY-01).
File sync (#4): ``save_before_external_write`` (SES-04), ``sync_from_disk``
(SES-05) and ``run_kct_handoff`` (SES-06).
Selection and findings (#5): ``get_selection`` (SES-08, read-only) and
``show_findings`` (SES-09, changes only the editor selection).
"""

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
from .tools import get_selection as get_selection_mod
from .tools import run_kct_handoff as handoff_mod
from .tools import save_before_external_write as save_mod
from .tools import session_status as session_status_mod
from .tools import show_findings as show_findings_mod
from .tools import sync_from_disk as sync_mod

INSTRUCTIONS = (
    "openkonnect drives a running KiCad 10 through its IPC API. Call session_status "
    "first to see whether KiCad is reachable and what is open. Failures are "
    "structured: error.code, error.message (what to do), error.retryable, "
    "error.details. Every call is time-bounded; see meta.timeout_s."
)

_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
#: Saving writes the board file but never loses editor state.
_SAVES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
#: Changes only the editor selection: no document change, nothing lost.
_SELECTS = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
#: Reloading can discard unsaved editor state (only with discard_unsaved).
_RELOADS = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


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

    @mcp.tool(
        name="get_selection",
        description=get_selection_mod.DESCRIPTION,
        annotations=_READ_ONLY,
        structured_output=False,
    )
    async def get_selection(
        kinds: list[str] | None = None,
        include_geometry: bool = False,
    ) -> CallToolResult:
        return await run(lambda s: get_selection_mod.get_selection(s, kinds, include_geometry))

    @mcp.tool(
        name="show_findings",
        description=show_findings_mod.DESCRIPTION,
        annotations=_SELECTS,
        structured_output=False,
    )
    async def show_findings(
        findings: list[Any] | dict[str, Any] | str,
        mode: str = "select",
        focus: int | str = "all",
        zoom: bool = False,
    ) -> CallToolResult:
        return await run(lambda s: show_findings_mod.show_findings(s, findings, mode, focus, zoom))

    @mcp.tool(
        name="save_before_external_write",
        description=save_mod.DESCRIPTION,
        annotations=_SAVES,
        structured_output=False,
    )
    async def save_before_external_write(
        documents: str | list[str] | None = None,
        allow_save: bool = False,
        schematic_unverified_ok: bool = False,
    ) -> CallToolResult:
        return await run(
            lambda s: save_mod.save_before_external_write(
                s, documents, allow_save, schematic_unverified_ok
            )
        )

    @mcp.tool(
        name="sync_from_disk",
        description=sync_mod.DESCRIPTION,
        annotations=_RELOADS,
        structured_output=False,
    )
    async def sync_from_disk(
        documents: str | list[str] | None = None,
        discard_unsaved: bool = False,
        expected_hash: str | None = None,
        editor_hash: str | None = None,
    ) -> CallToolResult:
        return await run(
            lambda s: sync_mod.sync_from_disk(
                s, documents, discard_unsaved, expected_hash, editor_hash
            )
        )

    @mcp.tool(
        name="run_kct_handoff",
        description=handoff_mod.DESCRIPTION,
        annotations=_RELOADS,
        structured_output=False,
    )
    async def run_kct_handoff(
        command: list[str],
        documents: str | list[str] | None = None,
        allow_save: bool = False,
        discard_unsaved: bool = False,
        timeout_s: float = handoff_mod.DEFAULT_COMMAND_TIMEOUT_S,
    ) -> CallToolResult:
        # Not wrapped in run_tool: each IPC step is bounded on its own, the
        # command by timeout_s, and the session lock is free while kct runs.
        payload = await anyio.to_thread.run_sync(
            lambda: handoff_mod.run_kct_handoff(
                session, command, documents, allow_save, discard_unsaved, timeout_s
            ),
            abandon_on_cancel=True,
        )
        return _to_result(payload)

    return mcp

"""MCP level: the server lists its tools and answers over the protocol."""

from __future__ import annotations

import json

import anyio
from mcp.shared.memory import create_connected_server_and_client_session

from openkonnect.server import build_server

from .conftest import FakeBackend, board_ref, make_session


def _call(session, tool: str):
    async def go():
        server = build_server(session)
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            tools = await client.list_tools()
            result = await client.call_tool(tool, {})
            return tools, result

    return anyio.run(go)


def test_tool_list():
    tools, _ = _call(make_session(sockets=[], running=False), "session_status")
    by_name = {t.name: t for t in tools.tools}
    assert sorted(by_name) == [
        "board_summary",
        "get_selection",
        "run_kct_handoff",
        "save_before_external_write",
        "session_status",
        "show_findings",
        "sync_from_disk",
    ]
    for name in ("board_summary", "session_status", "get_selection"):
        assert by_name[name].annotations.readOnlyHint is True
    # show_findings changes only the editor selection: not read-only, not destructive.
    assert by_name["show_findings"].annotations.readOnlyHint is False
    assert by_name["show_findings"].annotations.destructiveHint is False
    for name in ("save_before_external_write", "sync_from_disk", "run_kct_handoff"):
        assert by_name[name].annotations.readOnlyHint is False
    assert by_name["sync_from_disk"].annotations.destructiveHint is True
    for t in tools.tools:
        assert t.description


def test_session_status_over_mcp_when_kicad_absent():
    _, result = _call(make_session(sockets=[], running=False), "session_status")
    assert result.isError is False
    payload = result.structuredContent
    assert payload["ok"] is True
    assert payload["kicad_running"] is False
    assert json.loads(result.content[0].text) == payload


def test_board_summary_over_mcp():
    backend = FakeBackend(open_boards=[[board_ref()]])
    _, result = _call(make_session(backend), "board_summary")
    assert result.isError is False
    assert result.structuredContent["counts"]["footprints"] == 3


def test_board_summary_error_over_mcp():
    _, result = _call(make_session(sockets=[], running=True, api_cfg=False), "board_summary")
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "ipc_disabled"


def test_show_findings_over_mcp_accepts_a_kct_json_object():
    from kicad_tools.validate.violations import DRCViolation

    from .conftest import EditorFake

    v = DRCViolation(
        rule_id="courtyard_missing", severity="warning", message="", items=("R2",)
    ).to_dict()
    fake = EditorFake()

    async def go():
        server = build_server(make_session(fake))
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return await client.call_tool(
                "show_findings", {"findings": {"violations": [v]}, "mode": "both", "focus": 0}
            )

    result = anyio.run(go)
    assert result.isError is False, result.content
    payload = result.structuredContent
    assert payload["selection"]["footprints"] == ["R2"]
    assert [u["part"] for u in payload["unsupported"]] == ["highlight"]

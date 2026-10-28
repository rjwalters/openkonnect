"""Docs vs code: spec C-3 and the README tool table must match the server (#14)."""

from __future__ import annotations

import re
from pathlib import Path

import anyio
from mcp.shared.memory import create_connected_server_and_client_session

from openkonnect.errors import ErrorCode
from openkonnect.server import build_server

from .conftest import make_session

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "spec" / "capabilities.md"
README = ROOT / "README.md"

RESERVED_MARKER = "Reserved, not yet emitted:"


def _codes_in_table(text: str) -> set[str]:
    """Every backticked code in the first column of the markdown table rows."""
    codes: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        first_cell = line.split("|")[1]
        codes.update(re.findall(r"`([a-z_]+)`", first_cell))
    return codes


def _c3_tables() -> tuple[set[str], set[str]]:
    text = SPEC.read_text(encoding="utf-8")
    start = text.index("**C-3 Errors.**")
    end = text.index("**C-4 ", start)
    section = text[start:end]
    split = section.index(RESERVED_MARKER)
    return _codes_in_table(section[:split]), _codes_in_table(section[split:])


def test_every_error_code_is_in_the_c3_emitted_table():
    emitted, _ = _c3_tables()
    values = {c.value for c in ErrorCode}
    assert values - emitted == set(), "ErrorCode values missing from spec C-3"
    assert emitted - values == set(), "C-3 lists as emitted a code that ErrorCode lacks"


def test_c3_reserved_codes_are_not_emitted():
    _, reserved = _c3_tables()
    assert reserved, "C-3 reserved table is empty or was not found"
    assert reserved & {c.value for c in ErrorCode} == set()


def test_readme_tool_table_matches_registered_tools():
    async def go():
        server = build_server(make_session(sockets=[], running=False))
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return await client.list_tools()

    registered = {t.name for t in anyio.run(go).tools}
    rows = re.findall(
        r"^\| `([a-z_]+)` \| ([A-Z]{3}-\d{2}) \|", README.read_text(encoding="utf-8"), re.M
    )
    names = [name for name, _ in rows]
    assert len(names) == len(set(names)), "a tool appears twice in the README table"
    assert set(names) == registered
